"""Zero-dependency HTTP demo for the existing offline Shopping Copilot.

The web layer is deliberately separate from the official ``agent.Agent`` entry.
It renders a compact, target-blind decision receipt from the agent's real trace;
it does not recompute rankings or change the scored pipeline.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .audit import GENESIS_DIGEST, SCHEMA_VERSION, chain_digest
from .control_intent import ControlIntent, parse_control_intent
from .explanations import explain_product, product_advice, summarize_explanations
from .localization import localized_agent_message, localized_control_message, message_locale
from .shadow_policy import shadow_question_board
from .shopping_guide import describe, build_shopping_guide


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
DEFAULT_PROFILE = {
    "purchase_frequency": "unspecified",
    "average_prior_rating": None,
    "rating_style": "unspecified",
    "preference_tags": ["comfort"],
    "summary": "Comfort is a weak preference, never a hard requirement.",
}
SCENARIOS = (
    {
        "id": "precise",
        "label": "Precise buy",
        "title": "I need a blue basketball jersey under $60.",
        "followups": ["Breathable mesh and a drawstring matter.", "Show me the strongest matches."],
    },
    {
        "id": "explore",
        "label": "Explore",
        "title": "Help me find something comfortable for a summer wedding.",
        "followups": ["A dress, preferably cotton.", "Keep it casual and under $80."],
    },
    {
        "id": "override",
        "label": "Change my mind",
        "title": "I need a black dress under $50.",
        "followups": ["Blue instead.", "No budget limit. Switch to shoes, not leather."],
    },
)
SELECTION_SCHEMA_VERSION = "show-me-your-agent.selection.v1"
SESSION_SCHEMA_VERSION = "show-me-your-agent.session.v1"
CHAT_SCHEMA_VERSION = "show-me-your-agent.chat.v1"
SELECTION_STATE_SCHEMA_VERSION = "show-me-your-agent.selection-state.v1"
HEALTH_SCHEMA_VERSION = "show-me-your-agent.health.v1"
PRODUCT_DETAIL_SCHEMA_VERSION = "show-me-your-agent.product-detail.v1"
MAX_SELECTIONS = 3
MAX_MESSAGE_CHARS = 1_000


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "request_failed"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class Session:
    next_turn: int
    created_at: float
    last_access: float = field(default_factory=time.time)
    intent_version: int = 1
    previous_category: str | None = None
    shown_by_intent: dict[int, set[str]] = field(default_factory=lambda: {1: set()})
    previous_state: dict[str, dict[str, str]] = field(default_factory=dict)
    audit_turns: list[dict[str, Any]] = field(default_factory=list)
    audit_head: str = GENESIS_DIGEST
    selections: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_products: list[dict[str, Any]] = field(default_factory=list)
    comparison_cache_key: str | None = None
    comparison_cache: dict[str, Any] | None = None
    locale: str = "en"
    rejected_asins: set[str] = field(default_factory=set)
    finalized_at_utc: str | None = None
    finalized_turn: int | None = None
    finalized_asins: tuple[str, ...] = ()


def find_catalog(explicit: str | None = None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        (
            ROOT / "mvp" / "demo_data" / "catalog.jsonl",
        )
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(
        "Catalog not found. Provide an explicit local catalog using "
        "`--catalog path/to/catalog.jsonl`."
    )


def _event_map(trace: dict[str, Any]) -> dict[str, Any]:
    return {event["stage"]: event["output"] for event in trace.get("events", ())}


def _soft_values(soft: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, values in soft.items():
        result[key] = [str(item.get("value", item)) if isinstance(item, dict) else str(item) for item in values]
    return result


def _state_snapshot(receipt: dict[str, Any]) -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = {}
    for group in ("hard", "soft", "excluded"):
        values = receipt.get(group, {})
        snapshot[group] = {
            str(key): json.dumps(value, ensure_ascii=False, sort_keys=True)
            for key, value in values.items()
        }
    return snapshot


def _state_changes(
    before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for group in ("hard", "soft", "excluded"):
        previous = before.get(group, {})
        current = after.get(group, {})
        for key in sorted(previous.keys() | current.keys()):
            if key not in previous:
                changes.append(
                    {"kind": "added", "group": group, "slot": key, "value": json.loads(current[key])}
                )
            elif key not in current:
                changes.append(
                    {"kind": "removed", "group": group, "slot": key, "previous": json.loads(previous[key])}
                )
            elif previous[key] != current[key]:
                changes.append(
                    {
                        "kind": "updated",
                        "group": group,
                        "slot": key,
                        "previous": json.loads(previous[key]),
                        "value": json.loads(current[key]),
                    }
                )
    return changes


def _state_evidence(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metadata = state.get("slot_metadata", {})
    evidence: dict[str, dict[str, Any]] = {"hard": {}, "soft": {}}
    for group in ("hard", "soft"):
        rows = metadata.get(group, {})
        for slot, row in rows.items():
            if not isinstance(row, dict):
                continue
            quote = str(row.get("evidence") or "").strip()
            evidence[group][str(slot)] = {
                "source_turn": row.get("source_turn"),
                "evidence": quote[:160],
            }
    return evidence


def build_receipt(trace: dict[str, Any]) -> dict[str, Any]:
    """Bound the large internal trace to the evidence a shopper can inspect."""
    events = _event_map(trace)
    state = events.get("3_state", {})
    feedback = events.get("3_feedback", state)
    retrieval = events.get("2_retrieval_retry") or events.get("2_retrieval") or {}
    ranking = events.get("4B_ranking", {})
    pre = events.get("4A_pre_policy", {})
    post = events.get("4B_post_policy", {})
    model_assist = events.get("1_model_enhancement")
    retrieval_warnings = [str(value) for value in retrieval.get("warnings", ())]
    novelty_mode = "not_applied"
    if any("Novelty filter excluded" in warning for warning in retrieval_warnings):
        novelty_mode = "fresh_pool"
    elif any("Novelty pool exhausted" in warning for warning in retrieval_warnings):
        novelty_mode = "fallback_reuse"
    ranked = ranking.get("ranked_candidates", ())
    candidate_products = [
        row.get("product", {})
        for row in retrieval.get("candidates", ())
        if isinstance(row, dict) and isinstance(row.get("product"), dict)
    ]
    known_attributes = {
        *state.get("hard_constraints", {}).keys(),
        *state.get("soft_preferences", {}).keys(),
    }
    asked_attributes = [
        question.get("ask_attribute", "")
        for question in feedback.get("asked_questions", state.get("asked_questions", ()))
        if isinstance(question, dict)
    ]
    timings = [
        {"stage": event["stage"], "elapsed_ms": event.get("elapsed_ms", 0)}
        for event in trace.get("events", ())
        if event["stage"] not in {"response", "3_feedback"}
    ]
    return {
        "state_version": state.get("state_version"),
        "hard": state.get("hard_constraints", {}),
        "soft": _soft_values(state.get("soft_preferences", {})),
        "excluded": state.get("exclusions", {}),
        "rejected_asins": state.get("rejected_asins", []),
        "state_evidence": _state_evidence(state),
        "cleared": state.get("suggestions", {}).get("cleared_slots", []),
        "intent_changed": state.get("suggestions", {}).get("intent_changed", False),
        "questions_asked": feedback.get("suggestions", {}).get("clarification_count", 0),
        "shown_count": len(feedback.get("shown_asins", ())),
        "candidate_count": retrieval.get("returned_count", 0),
        "retrieval_stats": retrieval.get("stats", {}),
        "novelty_mode": novelty_mode,
        "ranking_method": ranking.get("ranking_method"),
        "model_assist": model_assist,
        "model_usage": (model_assist or {}).get(
            "usage", {"prompt_tokens": 0, "completion_tokens": 0}
        ),
        "top_evidence": ranked[0].get("evidence", []) if ranked else [],
        "shadow_questions": shadow_question_board(
            candidate_products,
            already_known=known_attributes,
            already_asked=asked_attributes,
            turns_left=max(0, 10 - int(trace.get("turn", 1))),
        )[:3],
        "pre_action": pre.get("action"),
        "pre_reason": pre.get("reason"),
        "post_action": post.get("action"),
        "post_reason": post.get("reason"),
        "question": post.get("question") or pre.get("question"),
        "timings": timings,
    }


class AgentRuntime:
    def __init__(self, agent: Any, *, orchestration_mode: str = "test", model_provider: str = "off", model_name: str | None = None, model_cloud: bool | None = None, comparison_enhancer: Any = None, session_ttl_seconds: float = 3600.0, max_sessions: int = 128, scenarios: tuple[dict[str, Any], ...] = SCENARIOS):
        if session_ttl_seconds <= 0:
            raise ValueError("session_ttl_seconds must be positive")
        if not 1 <= max_sessions <= 10_000:
            raise ValueError("max_sessions must be between 1 and 10000")
        self.agent = agent
        self.orchestration_mode = orchestration_mode
        self.model_provider = model_provider
        self.model_name = model_name
        self.model_cloud = model_provider not in {"off", "local"} if model_cloud is None else bool(model_cloud)
        self.comparison_enhancer = comparison_enhancer
        self.session_ttl_seconds = float(session_ttl_seconds)
        self.max_sessions = int(max_sessions)
        self.scenarios = tuple(deepcopy(scenarios))
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()

    @classmethod
    def create(cls, catalog: Path, *, orchestration_mode: str = "adaptive", provider: Any = None, session_ttl_seconds: float = 3600.0, max_sessions: int = 128, scenarios: tuple[dict[str, Any], ...] = SCENARIOS) -> "AgentRuntime":
        from agent import Agent
        from shopping_agent.comparison_enhancer import ComparisonEnhancer
        from shopping_agent.requirement_enhancer import RequirementEnhancer

        enhancer = RequirementEnhancer(provider) if provider is not None else None
        return cls(
            Agent(
                catalog_path=catalog,
                trace_enabled=True,
                orchestration_mode=orchestration_mode,
                requirement_enhancer=enhancer,
            ),
            orchestration_mode=orchestration_mode,
            model_provider=provider.name if provider is not None else "off",
            model_name=provider.model if provider is not None else None,
            model_cloud=(
                urlsplit(provider.base_url).hostname not in {"127.0.0.1", "localhost", "::1"}
                if provider is not None else False
            ),
            comparison_enhancer=ComparisonEnhancer(provider) if provider is not None else None,
            session_ttl_seconds=session_ttl_seconds,
            max_sessions=max_sessions,
            scenarios=scenarios,
        )

    def _drop_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        drop = getattr(self.agent, "drop_session", None)
        if callable(drop):
            drop(session_id)

    def _prune_sessions(self, *, reserve_slot: bool = False) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, session in self.sessions.items()
            if now - session.last_access > self.session_ttl_seconds
        ]
        for session_id in expired:
            self._drop_session(session_id)
        target = self.max_sessions - (1 if reserve_slot else 0)
        while len(self.sessions) > target:
            oldest = min(self.sessions, key=lambda key: self.sessions[key].last_access)
            self._drop_session(oldest)

    def _session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if not session:
            raise ApiError(HTTPStatus.NOT_FOUND, "Session not found. Start a new conversation.", "session_not_found")
        now = time.time()
        if now - session.last_access > self.session_ttl_seconds:
            self._drop_session(session_id)
            raise ApiError(HTTPStatus.NOT_FOUND, "Session expired. Start a new conversation.", "session_expired")
        session.last_access = now
        return session

    def health(self) -> dict[str, Any]:
        with self.lock:
            self._prune_sessions()
            return {
                "schema_version": HEALTH_SCHEMA_VERSION,
                "ok": True,
                "sessions": len(self.sessions),
                "max_sessions": self.max_sessions,
                "session_ttl_seconds": self.session_ttl_seconds,
                "concurrency_mode": "single_threaded_sqlite_owner",
                "model_provider": self.model_provider,
                "model_name": self.model_name,
                **self._model_disclosure(),
                "catalog_products": self.agent.catalog_size,
            }

    def new_session(self, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = uuid.uuid4().hex[:16]
        effective_profile = dict(DEFAULT_PROFILE)
        if profile:
            allowed = {"purchase_frequency", "average_prior_rating", "rating_style", "preference_tags", "summary"}
            effective_profile.update({key: value for key, value in profile.items() if key in allowed})
        with self.lock:
            self._prune_sessions(reserve_slot=True)
            self.agent.reset(session_id, effective_profile)
            now = time.time()
            self.sessions[session_id] = Session(next_turn=1, created_at=now, last_access=now)
        return {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_id": session_id,
            "turn": 1,
            "max_turns": 10,
            "orchestration_mode": self.orchestration_mode,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            **self._model_disclosure(),
            "catalog_products": self.agent.catalog_size,
            "scenarios": deepcopy(self.scenarios),
        }

    def _model_disclosure(self) -> dict[str, str | bool]:
        if self.model_provider == "off" and getattr(getattr(self.agent, 'retriever', None), 'mode', None) == 'search_tool':
            return {
                'cloud_model': False,
                'search_backend': 'search_tool',
                'catalog_count_scope': 'cached_search_details',
                'data_boundary': 'local_search_model',
                'data_disclosure': 'Local search model ranks products; cloud assistance is off. Search-model token counts are not reported by the tool.',
            }
        if self.model_provider == "off":
            return {
                "cloud_model": False,
                "data_boundary": "local_only_no_model",
                "data_disclosure": "Local only: no model request or shopper message leaves this process.",
            }
        if not self.model_cloud:
            return {
                "cloud_model": False,
                "data_boundary": "configured_local_endpoint",
                "data_disclosure": "Local model: shopper messages and selected catalog excerpts go only to the configured localhost endpoint.",
            }
        if self.model_provider == "deepseek":
            return {
                "cloud_model": True,
                "data_boundary": "deepseek_cloud",
                "data_disclosure": "Cloud model: shopper messages are sent to DeepSeek; compare/finalize/export may also send selected catalog excerpts. Token usage is reported per turn.",
            }
        return {
            "cloud_model": True,
            "data_boundary": "configured_external_provider",
            "data_disclosure": "Configured model endpoint may receive shopper messages and selected catalog excerpts. Token usage is reported per turn.",
        }

    def audit(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            session = self._session(session_id)
            # Round-trip through JSON to return a detached, serializable snapshot.
            turns = json.loads(json.dumps(session.audit_turns, ensure_ascii=False))
            return {
                "schema_version": SCHEMA_VERSION,
                "session_id": session_id,
                "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(session.created_at)),
                "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "agent_mode": f"{self.orchestration_mode}:model-provider-{self.model_provider}",
                "privacy_note": (
                    "Local export. Contains user messages but no hidden labels or full candidate pools. "
                    + self._model_disclosure()["data_disclosure"]
                ),
                "model": {
                    "provider": self.model_provider,
                    "name": self.model_name,
                    **self._model_disclosure(),
                },
                "integrity": {
                    "algorithm": "sha256-chain",
                    "head": session.audit_head,
                    "signed": False,
                    "note": "Detects edits or reordering; it is not an identity signature.",
                },
                "turn_count": len(turns),
                "turns": turns,
            }

    @staticmethod
    def _bounded_catalog_product(product: dict[str, Any]) -> dict[str, Any]:
        """Expose enough catalog evidence for B without an unbounded payload."""
        descriptions = product.get("description") or ()
        if isinstance(descriptions, str):
            descriptions = (descriptions,)
        features = product.get("features") or ()
        if isinstance(features, str):
            features = (features,)
        details = product.get("details") or {}
        if not isinstance(details, dict):
            details = {}
        return {
            "parent_asin": str(product.get("parent_asin") or ""),
            "title": str(product.get("title") or "Untitled catalog product")[:500],
            "store": str(product.get("store") or "Independent seller")[:200],
            "price": product.get("price"),
            "average_rating": product.get("average_rating"),
            "rating_number": product.get("rating_number"),
            "categories": [str(value)[:200] for value in (product.get("categories") or ())[:10]],
            "product_description": [str(value)[:1500] for value in descriptions[:4]],
            "product_bullet_points": [str(value)[:500] for value in features[:12]],
            "details": {
                str(key)[:200]: str(value)[:500]
                for key, value in list(details.items())[:20]
            },
        }

    @staticmethod
    def _comparison_evidence(match: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        rows = match.get("signals", ()) if isinstance(match, dict) else ()
        return {
            "strengths": [row for row in rows if row.get("status") == "supported"],
            "tradeoffs": [row for row in rows if row.get("status") == "conflict"],
            "unknowns": [row for row in rows if row.get("status") == "unknown"],
        }

    @staticmethod
    def _comparison_summary(products: list[dict[str, Any]]) -> dict[str, Any]:
        rows = [
            {
                "parent_asin": product["parent_asin"],
                "title": product["title"],
                "price": product.get("price"),
                "rating": product.get("average_rating"),
                "hard_coverage": product.get("requirement_match", {}).get("hard_coverage"),
                "supported_count": len(product.get("comparison_evidence", {}).get("strengths", ())),
                "tradeoff_count": len(product.get("comparison_evidence", {}).get("tradeoffs", ())),
                "unknown_count": len(product.get("comparison_evidence", {}).get("unknowns", ())),
            }
            for product in products
        ]
        priced = [row for row in rows if isinstance(row["price"], (int, float))]
        rated = [row for row in rows if isinstance(row["rating"], (int, float))]
        return {
            "rows": rows,
            "lowest_price_asin": min(priced, key=lambda row: row["price"])["parent_asin"] if priced else None,
            "highest_rating_asin": max(rated, key=lambda row: row["rating"])["parent_asin"] if rated else None,
            "note": "Leaders are descriptive catalog values, not a new ranking decision.",
        }

    def update_selection(
        self,
        session_id: str,
        *,
        parent_asin: str | None = None,
        selected: bool | None = None,
        reason: str = "",
        clear: bool = False,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if len(reason) > 300:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Selection reason must be 300 characters or fewer.", "selection_reason_too_long")
        with self.lock:
            session = self._session(session_id)
            if clear:
                session.selections.clear()
            else:
                if not isinstance(parent_asin, str) or not parent_asin.strip() or not isinstance(selected, bool):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "parent_asin and selected are required.", "invalid_selection")
                parent_asin = parent_asin.strip()
                shown = set().union(*session.shown_by_intent.values())
                if parent_asin not in shown:
                    raise ApiError(HTTPStatus.CONFLICT, "Only products shown in this session can be selected.", "product_not_shown")
                if selected:
                    if parent_asin not in session.selections and len(session.selections) >= MAX_SELECTIONS:
                        raise ApiError(HTTPStatus.CONFLICT, f"Select at most {MAX_SELECTIONS} products.", "selection_limit")
                    session.selections[parent_asin] = {
                        "selected_at_turn": session.next_turn - 1,
                        "intent_version": session.intent_version,
                        "reason": reason,
                    }
                else:
                    session.selections.pop(parent_asin, None)
            session.comparison_cache_key = None
            session.comparison_cache = None
            session.finalized_at_utc = None
            session.finalized_turn = None
            session.finalized_asins = ()
            return {"session_id": session_id, **self._selection_state(session)}

    @staticmethod
    def _selection_state(session: Session) -> dict[str, Any]:
        current_asins = tuple(session.selections)
        finalized = bool(
            current_asins
            and current_asins == session.finalized_asins
            and session.finalized_at_utc
        )
        return {
            "schema_version": SELECTION_STATE_SCHEMA_VERSION,
            "selected_asins": list(current_asins),
            "selection_count": len(current_asins),
            "max_selections": MAX_SELECTIONS,
            "rejected_asins": sorted(session.rejected_asins),
            "status": "finalized" if finalized else "ready_for_comparison" if current_asins else "draft",
            "finalized": finalized,
            "finalized_at_utc": session.finalized_at_utc if finalized else None,
            "finalized_turn": session.finalized_turn if finalized else None,
        }

    def selection_handoff(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            session = self._session(session_id)
            latest_receipt = session.audit_turns[-1]["receipt"] if session.audit_turns else {}
            selected_products: list[dict[str, Any]] = []
            for parent_asin, selection in session.selections.items():
                catalog_product = self.agent.get_catalog_product(parent_asin)
                if not isinstance(catalog_product, dict):
                    continue
                observed: dict[str, Any] = {}
                for turn in reversed(session.audit_turns):
                    observed = next(
                        (row for row in turn.get("products", ()) if row.get("parent_asin") == parent_asin),
                        {},
                    )
                    if observed:
                        break
                match = observed.get("match", {})
                advice = observed.get("advice") or product_advice(catalog_product, match)
                selected_products.append(
                    {
                        **self._bounded_catalog_product(catalog_product),
                        "selection": dict(selection),
                        "last_observed_rank": observed.get("rank"),
                        "ranking_score": observed.get("score"),
                        "requirement_match": match,
                        "advice": advice,
                        "comparison_evidence": self._comparison_evidence(match),
                    }
                )
            current_asins = tuple(session.selections)
            finalized = bool(current_asins and current_asins == session.finalized_asins and session.finalized_at_utc)
            handoff = {
                "schema_version": SELECTION_SCHEMA_VERSION,
                "session_id": session_id,
                "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "status": "finalized" if finalized else "ready_for_comparison" if selected_products else "draft",
                "intent_version": session.intent_version,
                "requirements": {
                    "state_version": latest_receipt.get("state_version"),
                    "hard": latest_receipt.get("hard", {}),
                    "soft": latest_receipt.get("soft", {}),
                    "excluded": latest_receipt.get("excluded", {}),
                    "rejected_asins": latest_receipt.get("rejected_asins", []),
                    "evidence": latest_receipt.get("state_evidence", {}),
                },
                "selected_products": selected_products,
                "decision": {
                    "finalized": finalized,
                    "finalized_at_utc": session.finalized_at_utc if finalized else None,
                    "finalized_turn": session.finalized_turn if finalized else None,
                    "selected_asins": list(current_asins),
                },
                "comparison_summary": self._comparison_summary(selected_products),
                "comparison_contract": {
                    "owner": "B",
                    "instruction": "Compare only supported catalog facts; label missing or ambiguous attributes as unknown.",
                    "consumer": "D",
                    "ranking_is_final": True,
                },
            }
            if self.comparison_enhancer is not None and selected_products:
                cache_key = json.dumps(
                    {
                        "intent_version": session.intent_version,
                        "state_version": latest_receipt.get("state_version"),
                        "selected_asins": list(session.selections),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if session.comparison_cache_key == cache_key and session.comparison_cache is not None:
                    assist = deepcopy(session.comparison_cache)
                    assist["cached"] = True
                    assist["usage_this_call"] = {"prompt_tokens": 0, "completion_tokens": 0}
                else:
                    assist = self.comparison_enhancer.compare(handoff).to_dict()
                    assist["cached"] = False
                    assist["usage_this_call"] = dict(assist["usage"])
                    session.comparison_cache_key = cache_key
                    session.comparison_cache = deepcopy(assist)
                handoff["comparison_assist"] = assist
            return handoff

    def product_detail(self, session_id: str, parent_asin: str) -> dict[str, Any]:
        """Return bounded catalog evidence only for a product shown in this session."""
        parent_asin = parent_asin.strip()
        if not parent_asin:
            raise ApiError(HTTPStatus.BAD_REQUEST, "parent_asin must not be empty.", "invalid_product_id")
        with self.lock:
            session = self._session(session_id)
            shown = set().union(*session.shown_by_intent.values())
            if parent_asin not in shown:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "Only products shown in this session can be viewed.",
                    "product_not_shown",
                )
            catalog_product = self.agent.get_catalog_product(parent_asin)
            if not isinstance(catalog_product, dict):
                raise ApiError(HTTPStatus.NOT_FOUND, "Catalog product not found.", "catalog_product_not_found")

            observed: dict[str, Any] = {}
            for turn in reversed(session.audit_turns):
                observed = next(
                    (row for row in turn.get("products", ()) if row.get("parent_asin") == parent_asin),
                    {},
                )
                if observed:
                    break
            latest_receipt = session.audit_turns[-1]["receipt"] if session.audit_turns else {}
            match = observed.get("match") or explain_product(catalog_product, latest_receipt)
            advice = observed.get("advice") or product_advice(catalog_product, match)
            return {
                "schema_version": PRODUCT_DETAIL_SCHEMA_VERSION,
                "session_id": session_id,
                **self._bounded_catalog_product(catalog_product),
                "last_observed_rank": observed.get("rank"),
                "ranking_score": observed.get("score"),
                "requirement_match": match,
                "advice": advice,
                "source_note": "Bounded frozen-catalog evidence; no retrieval, reranking or model call was performed.",
            }

    def _control_turn(
        self,
        session_id: str,
        session: Session,
        message: str,
        turn: int,
        control: ControlIntent,
    ) -> dict[str, Any]:
        by_rank = {int(product["rank"]): product for product in session.last_products}
        requested = [rank for rank in control.ranks if rank in by_rank]
        ignored = [rank for rank in control.ranks if rank not in by_rank]
        action = control.action
        handoff: dict[str, Any] | None = None
        selection_changed = False
        feedback_changed = False
        rejected_feedback: list[str] = []
        liked_feedback: list[str] = []

        if action in {"select", "compare"}:
            added: list[int] = []
            already_selected: list[int] = []
            capped: list[int] = []
            for rank in requested:
                product = by_rank[rank]
                parent_asin = product["parent_asin"]
                if parent_asin in session.selections:
                    already_selected.append(rank)
                    if parent_asin in session.rejected_asins:
                        session.rejected_asins.remove(parent_asin)
                        liked_feedback.append(parent_asin)
                        feedback_changed = True
                    continue
                if len(session.selections) >= MAX_SELECTIONS:
                    capped.append(rank)
                    continue
                session.selections[parent_asin] = {
                    "selected_at_turn": turn,
                    "intent_version": session.intent_version,
                    "reason": f"Selected by chat command at rank {rank}",
                }
                selection_changed = True
                added.append(rank)
                if parent_asin in session.rejected_asins:
                    session.rejected_asins.remove(parent_asin)
                    liked_feedback.append(parent_asin)
                    feedback_changed = True
            if not control.ranks:
                detail = "No ranks were provided; keeping the current shortlist."
            elif added:
                detail = f"Selected rank(s) {', '.join(f'#{rank}' for rank in added)}."
            elif already_selected:
                detail = f"Rank(s) {', '.join(f'#{rank}' for rank in already_selected)} were already selected."
            else:
                detail = "None of those ranks exist in the latest result set."
            if capped:
                detail += f" Shortlist limit {MAX_SELECTIONS}; skipped {', '.join(f'#{rank}' for rank in capped)}."
            if action == "compare":
                detail += (
                    " The structured comparison handoff is ready."
                    if session.selections else
                    " Select at least one shown product before comparing."
                )
        elif action == "reject":
            for rank in requested:
                parent_asin = by_rank[rank]["parent_asin"]
                if parent_asin not in session.rejected_asins:
                    session.rejected_asins.add(parent_asin)
                    rejected_feedback.append(parent_asin)
                    feedback_changed = True
                if session.selections.pop(parent_asin, None) is not None:
                    selection_changed = True
            detail = (
                f"Rejected rank(s) {', '.join(f'#{rank}' for rank in requested)}; they will stay out of later results."
                if requested else "None of those ranks exist in the latest result set."
            )
        elif action == "remove":
            for rank in requested:
                removed = session.selections.pop(by_rank[rank]["parent_asin"], None)
                selection_changed = selection_changed or removed is not None
            detail = (
                f"Removed rank(s) {', '.join(f'#{rank}' for rank in requested)} from the shortlist."
                if requested else "None of those ranks exist in the latest result set."
            )
        elif action == "clear":
            selection_changed = bool(session.selections)
            session.selections.clear()
            detail = "Cleared the shortlist."
        elif action == "finalize":
            if session.selections:
                session.finalized_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                session.finalized_turn = turn
                session.finalized_asins = tuple(session.selections)
                detail = "Finalized the current selection."
            else:
                detail = "Select at least one shown product before finalizing."
        else:
            detail = (
                "The structured selection handoff draft is ready."
                if session.selections else "Select at least one shown product before finalizing."
            )
        if ignored:
            detail += f" Ignored unavailable rank(s): {', '.join(f'#{rank}' for rank in ignored)}."

        feedback_state = None
        if feedback_changed:
            feedback_state = self.agent.record_product_feedback(
                session_id,
                source_turn=turn,
                rejected_asins=rejected_feedback,
                liked_asins=liked_feedback,
            ).to_dict()
        if selection_changed or feedback_changed:
            session.comparison_cache_key = None
            session.comparison_cache = None
            session.finalized_at_utc = None
            session.finalized_turn = None
            session.finalized_asins = ()
        if action in {"compare", "handoff", "finalize"}:
            handoff = self.selection_handoff(session_id)
        detail = localized_control_message(
            locale=session.locale,
            action=action,
            selection_count=len(session.selections),
            handoff_ready=bool(session.selections),
            fallback=detail,
        )
        raw_comparison_usage = (
            handoff.get("comparison_assist", {}).get("usage_this_call", {})
            if handoff is not None else {}
        )
        comparison_usage = {
            "prompt_tokens": max(0, int(raw_comparison_usage.get("prompt_tokens") or 0)),
            "completion_tokens": max(0, int(raw_comparison_usage.get("completion_tokens") or 0)),
        }
        response = self.agent.record_control(
            session_id,
            message,
            turn,
            {"message": detail, "usage": comparison_usage},
            {"action": action, "ranks": list(control.ranks)},
            10,
        )
        prior = session.audit_turns[-1]["receipt"] if session.audit_turns else {}
        receipt = deepcopy(prior)
        receipt.update(
            {
                "state_changes": [],
                "pre_action": "control",
                "pre_reason": f"selection_{action}",
                "post_action": None,
                "post_reason": None,
                "timings": [{"stage": "0_control", "elapsed_ms": 0.0}],
                "shadow_questions": [],
                "model_assist": None,
                "model_usage": {
                    "prompt_tokens": comparison_usage["prompt_tokens"],
                    "completion_tokens": comparison_usage["completion_tokens"],
                },
                "comparison_assist": handoff.get("comparison_assist") if handoff else None,
                "selection_status": handoff.get("status") if handoff else None,
                "selection_decision": handoff.get("decision") if handoff else None,
                "selection_count": len(session.selections),
                "rejected_asins": sorted(session.rejected_asins),
                "selection_reset": False,
            }
        )
        if feedback_state is not None:
            receipt["state_version"] = feedback_state["state_version"]
            receipt["rejected_asins"] = list(feedback_state["rejected_asins"])
        products = deepcopy(session.last_products)
        assistant = {
            "message": response["message"],
            "ask_attribute": None,
            "usage": response["usage"],
        }
        audit_record = {
            "turn": turn,
            "user_message": message,
            "assistant": assistant,
            "products": [
                {
                    "rank": product["rank"],
                    "parent_asin": product["parent_asin"],
                    "score": product["score"],
                    "title": product["title"],
                    "match": product["match"],
                    "advice": product["advice"],
                }
                for product in products
            ],
            "receipt": receipt,
            "control": {"action": action, "ranks": list(control.ranks)},
        }
        if handoff is not None:
            audit_record["selection_decision"] = deepcopy(handoff["decision"])
        digest = chain_digest(session.audit_head, audit_record)
        audit_record["integrity"] = {"previous_sha256": session.audit_head, "sha256": digest}
        session.audit_head = digest
        session.audit_turns.append(audit_record)
        session.next_turn += 1
        selection_state = self._selection_state(session)
        result = {
            "schema_version": CHAT_SCHEMA_VERSION,
            "turn": turn,
            "next_turn": session.next_turn,
            "remaining_turns": max(0, 11 - session.next_turn),
            "assistant": assistant,
            "products": products,
            "receipt": receipt,
            "selection_state": selection_state,
            "shopping_guide": build_shopping_guide(products),
        }
        if handoff is not None:
            result["handoff"] = handoff
        return result

    def chat(self, session_id: str, message: str) -> dict[str, Any]:
        message = message.strip()
        if not message:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Message cannot be empty.", "empty_message")
        if len(message) > MAX_MESSAGE_CHARS:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"Message must be {MAX_MESSAGE_CHARS:,} characters or fewer.",
                "message_too_long",
            )
        with self.lock:
            session = self._session(session_id)
            session.locale = message_locale(message, session.locale)
            if session.next_turn > 10:
                raise ApiError(HTTPStatus.CONFLICT, "This conversation has reached the ten-turn limit.", "turn_limit")
            turn = session.next_turn
            control = parse_control_intent(message)
            if control is not None:
                return self._control_turn(session_id, session, message, turn, control)
            response = self.agent.respond(session_id, message, turn, 10)
            get_trace = getattr(self.agent, "get_trace", None)
            trace = (
                get_trace(session_id, turn)
                if callable(get_trace)
                else self.agent.trace[-1]  # Test doubles and legacy adapters.
            )
            if trace.get("session_id") != session_id or trace.get("turn") != turn:
                raise RuntimeError("Agent trace did not match the completed turn.")
            session.next_turn += 1

            ranking = _event_map(trace).get("4B_ranking", {})
            evidence_by_asin = {
                row["parent_asin"]: row.get("evidence", [])
                for row in ranking.get("ranked_candidates", ())
            }
            products = []
            for rank, row in enumerate(response.get("recommendations", ()), 1):
                product = self.agent.get_catalog_product(row["parent_asin"]) or {}
                products.append(
                    {
                        "rank": rank,
                        "parent_asin": row["parent_asin"],
                        "score": round(float(row.get("score", 0)), 6),
                        "title": str(product.get("title") or "Untitled catalog product"),
                        "store": str(product.get("store") or "Independent seller"),
                        "price": product.get("price"),
                        "rating": product.get("average_rating"),
                        "rating_count": product.get("rating_number"),
                        "category": (product.get("categories") or ["Catalog"])[-1],
                        "features": [str(value) for value in (product.get("features") or ())[:3]],
                        "evidence": evidence_by_asin.get(row["parent_asin"], []),
                    }
                )

            receipt = build_receipt(trace)
            was_finalized = bool(session.finalized_at_utc)
            category = str(receipt.get("hard", {}).get("category") or "").strip().casefold()
            intent_reset = bool(receipt.get("intent_changed"))
            if session.previous_category and category and category != session.previous_category:
                intent_reset = True
            if intent_reset:
                session.intent_version += 1
            selection_reset = bool(intent_reset and session.selections)
            if selection_reset:
                session.selections.clear()
                session.comparison_cache_key = None
                session.comparison_cache = None
                session.finalized_at_utc = None
                session.finalized_turn = None
                session.finalized_asins = ()
            if intent_reset:
                session.rejected_asins.clear()
            if category:
                session.previous_category = category

            current_state = _state_snapshot(receipt)
            receipt["state_changes"] = _state_changes(session.previous_state, current_state)
            session.previous_state = current_state
            finalization_invalidated = bool(was_finalized and receipt["state_changes"])
            if finalization_invalidated:
                session.comparison_cache_key = None
                session.comparison_cache = None
                session.finalized_at_utc = None
                session.finalized_turn = None
                session.finalized_asins = ()

            current_asins = {product["parent_asin"] for product in products}
            shown_asins = session.shown_by_intent.setdefault(session.intent_version, set())
            repeated_asins = current_asins & shown_asins
            shown_asins.update(current_asins)
            receipt["intent_version"] = session.intent_version
            receipt["intent_reset"] = intent_reset
            receipt["selection_reset"] = selection_reset
            receipt["selection_finalization_invalidated"] = finalization_invalidated
            receipt["repeat_count"] = len(repeated_asins)
            receipt["new_product_count"] = len(current_asins - repeated_asins)
            for product in products:
                catalog_product = self.agent.get_catalog_product(product["parent_asin"]) or {}
                product["match"] = explain_product(catalog_product, receipt)
                product["advice"] = product_advice(catalog_product, product["match"])
                product["shopper_notes"] = describe(product)
            receipt["result_quality"] = summarize_explanations(
                product["match"] for product in products
            )
            session.last_products = deepcopy(products)

            assistant = {
                "message": localized_agent_message(
                    locale=session.locale,
                    fallback=response.get("message", ""),
                    ask_attribute=response.get("ask_attribute"),
                    receipt=receipt,
                    product_count=len(products),
                ),
                "ask_attribute": response.get("ask_attribute"),
                "usage": response.get("usage", {}),
            }
            if session.locale == 'zh' and products and not response.get('ask_attribute'):
                assistant['message'] = build_shopping_guide(products)['intro']
            audit_record = {
                "turn": turn,
                "user_message": message,
                "assistant": assistant,
                "products": [
                    {
                        "rank": product["rank"],
                        "parent_asin": product["parent_asin"],
                        "score": product["score"],
                        "title": product["title"],
                        "match": product["match"],
                        "advice": product["advice"],
                    }
                    for product in products
                ],
                "receipt": receipt,
            }
            digest = chain_digest(session.audit_head, audit_record)
            audit_record["integrity"] = {
                "previous_sha256": session.audit_head,
                "sha256": digest,
            }
            session.audit_head = digest
            session.audit_turns.append(audit_record)

        return {
            "schema_version": CHAT_SCHEMA_VERSION,
            "turn": turn,
            "next_turn": session.next_turn,
            "remaining_turns": max(0, 11 - session.next_turn),
            "assistant": assistant,
            "products": products,
            "receipt": receipt,
            "selection_state": self._selection_state(session),
            "shopping_guide": build_shopping_guide(products),
        }


class DemoHandler(BaseHTTPRequestHandler):
    runtime: AgentRuntime
    server_version = "ShowMeYourAgentMVP/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length.", "invalid_content_length") from exc
        if length > 32_768:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body is too large.", "body_too_large")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Expected a JSON request body.", "invalid_json") from exc
        if not isinstance(value, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Expected a JSON object.", "invalid_json_object")
        return value

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._json(HTTPStatus.OK, self.runtime.health())
            return
        files = {"/": "index.html", "/styles.css": "styles.css", "/app.js": "app.js"}
        filename = files.get(path)
        if not filename:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = (STATIC_ROOT / filename).read_bytes()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        try:
            path = urlsplit(self.path).path
            payload = self._body()
            if path == "/api/session":
                self._json(HTTPStatus.CREATED, self.runtime.new_session(payload.get("profile")))
            elif path == "/api/chat":
                session_id = payload.get("session_id")
                message = payload.get("message")
                if not isinstance(session_id, str) or not isinstance(message, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "session_id and message must be strings.", "invalid_chat_request")
                self._json(HTTPStatus.OK, self.runtime.chat(session_id, message))
            elif path == "/api/audit":
                session_id = payload.get("session_id")
                if not isinstance(session_id, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "session_id must be a string.", "invalid_session_id")
                self._json(HTTPStatus.OK, self.runtime.audit(session_id))
            elif path == "/api/select":
                session_id = payload.get("session_id")
                if not isinstance(session_id, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "session_id must be a string.", "invalid_session_id")
                self._json(
                    HTTPStatus.OK,
                    self.runtime.update_selection(
                        session_id,
                        parent_asin=payload.get("parent_asin"),
                        selected=payload.get("selected"),
                        reason=payload.get("reason", "") if isinstance(payload.get("reason", ""), str) else "",
                        clear=payload.get("clear") is True,
                    ),
                )
            elif path == "/api/handoff":
                session_id = payload.get("session_id")
                if not isinstance(session_id, str):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "session_id must be a string.", "invalid_session_id")
                self._json(HTTPStatus.OK, self.runtime.selection_handoff(session_id))
            elif path == "/api/product":
                session_id = payload.get("session_id")
                parent_asin = payload.get("parent_asin")
                if not isinstance(session_id, str) or not isinstance(parent_asin, str):
                    raise ApiError(
                        HTTPStatus.BAD_REQUEST,
                        "session_id and parent_asin must be strings.",
                        "invalid_product_request",
                    )
                self._json(HTTPStatus.OK, self.runtime.product_detail(session_id, parent_asin))
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "Endpoint not found.", "endpoint_not_found")
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc), "error_code": exc.code})
        except Exception as exc:  # keep the demo responsive while logging the real cause
            print(f"Request failed: {type(exc).__name__}: {exc}")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": "The local agent could not complete this request.",
                "error_code": "internal_error",
            })


def create_server(runtime: AgentRuntime, host: str, port: int) -> HTTPServer:
    handler = type("ConfiguredDemoHandler", (DemoHandler,), {"runtime": runtime})
    # The in-memory SQLite FTS index belongs to the construction thread. A
    # single-threaded HTTP loop also preserves strict turn ordering; ranking is
    # fast enough that extra request threads would add risk without user value.
    return HTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--catalog")
    parser.add_argument(
        "--orchestration-mode",
        choices=("adaptive", "score_compat"),
        default="adaptive",
        help="adaptive is the product-facing default; score_compat reproduces the submission policy",
    )
    parser.add_argument("--model-provider", choices=("off", "local", "deepseek"), default="off")
    parser.add_argument("--model", help="override the provider's model name")
    parser.add_argument("--model-base-url", help="override the OpenAI-compatible API base URL")
    parser.add_argument("--model-timeout", type=float, default=20.0)
    parser.add_argument("--session-ttl", type=float, default=3600.0, help="idle session lifetime in seconds")
    parser.add_argument("--max-sessions", type=int, default=128, help="maximum in-memory sessions")
    args = parser.parse_args()
    if args.session_ttl <= 0:
        parser.error("--session-ttl must be positive")
    if not 1 <= args.max_sessions <= 10_000:
        parser.error("--max-sessions must be between 1 and 10000")
    catalog = find_catalog(args.catalog)
    print(f"Loading 50,000-product catalog from {catalog} ...", flush=True)
    from shopping_agent.model_provider import create_model_provider

    try:
        provider = create_model_provider(
            args.model_provider,
            base_url=args.model_base_url,
            model=args.model,
            timeout_seconds=args.model_timeout,
        )
    except ValueError as exc:
        parser.error(str(exc))
    runtime = AgentRuntime.create(
        catalog,
        orchestration_mode=args.orchestration_mode,
        provider=provider,
        session_ttl_seconds=args.session_ttl,
        max_sessions=args.max_sessions,
    )
    server = create_server(runtime, args.host, args.port)
    print(
        f"Show Me Your Agent MVP ({args.orchestration_mode}): http://{args.host}:{args.port}",
        flush=True,
    )
    if provider is not None:
        print(
            f"Optional requirement assist: {provider.name}/{provider.model}; token usage is reported per turn.",
            flush=True,
        )
    if provider is None:
        print("Press Ctrl+C to stop. Model assistance is off; turns make no model or network calls.", flush=True)
    elif not runtime.model_cloud:
        print("Press Ctrl+C to stop. Model requests stay on the configured local endpoint.", flush=True)
    else:
        print(
            "Press Ctrl+C to stop. Shopping turns send messages to the cloud model; "
            "compare/export may also send selected catalog excerpts.",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

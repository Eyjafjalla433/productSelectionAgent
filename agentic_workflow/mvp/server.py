"""Zero-dependency HTTP demo for the existing offline Shopping Copilot.

The web layer is deliberately separate from the official ``agent.Agent`` entry.
It renders a compact, target-blind decision receipt from the agent's real trace;
it does not recompute rankings or change the scored pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import re
import threading
import time
import uuid
from decimal import Decimal, ROUND_FLOOR
from copy import deepcopy
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .audit import GENESIS_DIGEST, SCHEMA_VERSION, chain_digest
from .control_intent import ControlIntent, has_unhandled_shortlist_mix, parse_control_intent, parse_detail_reference, plan_compound_turn, plan_selection_and_requirements, plan_shortlist_actions, plan_rejection_and_similarity, plan_similarity_and_requirements, plan_similarity_and_cheaper
from .similarity import facets_not_in_preferences, parse_facet_reply, repair_facet_reply, requirement_from_facets, supported_facets
from .explanations import explain_product, product_advice, summarize_explanations
from .localization import localized_agent_message, localized_control_message, message_locale
from .shadow_policy import shadow_question_board
from .shopping_guide import describe, build_shopping_guide, preference_tradeoff
from .product_question import answer_product_question
from .question_help import explain_pending_question, explain_shopping_term, split_term_question_and_request
from .preference_comparison import compare_preferences, decision_followup, declines_comparison_question, resolve_comparison_reply


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


def _known_catalog_price(product: dict[str, Any]) -> Decimal | None:
    value = product.get('price')
    if isinstance(value, bool):
        return None
    try:
        price = Decimal(str(value).replace('$', '').replace(',', ''))
    except (ValueError, ArithmeticError):
        return None
    return price if price.is_finite() and price >= 0 else None


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
    selection_history: list[dict[str, dict[str, Any]]] = field(default_factory=list)
    selection_future: list[dict[str, dict[str, Any]]] = field(default_factory=list)
    last_products: list[dict[str, Any]] = field(default_factory=list)
    focused_product_id: str | None = None
    focused_by_result_set: dict[tuple[str, ...], str] = field(default_factory=dict)
    pending_detail_attribute: str | None = None
    pending_similarity: dict[str, Any] | None = None
    pending_comparison: dict[str, Any] | None = None
    pending_comfort: dict[str, Any] | None = None
    pending_reset_scope: bool = False
    reset_snapshots: dict[int, dict[str, Any]] = field(default_factory=dict)
    comparison_cache_key: str | None = None
    comparison_cache: dict[str, Any] | None = None
    locale: str = "en"
    rejected_asins: set[str] = field(default_factory=set)
    rejection_history: list[tuple[str, ...]] = field(default_factory=list)
    rejection_future: list[tuple[str, ...]] = field(default_factory=list)
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
    for group in ("hard", "soft", "soft_priority", "soft_priority_turn", "excluded"):
        values = receipt.get(group, {})
        snapshot[group] = {
            str(key): json.dumps(value, ensure_ascii=False, sort_keys=True)
            for key, value in values.items()
        }
    return snapshot


def _preference_summary(receipt: dict[str, Any], *, include_next_step: bool = True) -> tuple[str, dict[str, Any]]:
    """Describe only current-session requirements, not inferred personal memory."""
    state = {group: deepcopy(receipt.get(group) or {}) for group in ('hard', 'soft', 'excluded')}

    def phrase(slot: str, raw: Any) -> str:
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        shown = ' or '.join(str(value) for value in values)
        if slot in {'price_min', 'budget_min'}:
            return f'at least ${shown}'
        if slot in {'price_max', 'budget_max'}:
            return f'under ${shown}'
        if slot == 'budget_target':
            return f'around ${shown}'
        if slot == 'budget_floor_target':
            return f'prefer above ${shown} (unverified)'
        if slot == 'fit_avoid':
            return f'nothing too {shown}'
        if slot == 'color':
            return f'{shown} color'
        if slot == 'size':
            return f'size {shown.upper()}'
        if slot == 'category':
            return shown
        if slot.startswith('feature_') or slot == 'feature':
            return shown
        return f'{shown} {slot}' if slot in {'brand', 'material', 'style', 'use_case'} else f'{slot}: {shown}'

    parts = []
    category = state['hard'].get('category')
    if category:
        parts.append(f"You're shopping for {phrase('category', category)}.")
    purpose = state['soft'].get('shopping_purpose')
    occasion = state['soft'].get('shopping_occasion')
    if purpose or occasion:
        context = ' '.join(' or '.join(str(item) for item in value)
                           if isinstance(value, (list, tuple)) else str(value)
                           for value in (occasion, purpose) if value)
        parts.append(f'Context: {context}.')
    for group, intro in (('hard', 'Must have'), ('soft', 'Prefer'), ('excluded', 'Avoid')):
        entries = [(
            f"{receipt['ordered_preferences'][slot]['preferred']} first; "
            f"{receipt['ordered_preferences'][slot]['also_acceptable']} also okay"
            if group == 'soft' and slot in receipt.get('ordered_preferences', {})
            else phrase(slot, value)
        ) for slot, value in state[group].items()
                   if slot not in {'category', 'shopping_purpose', 'shopping_occasion'}]
        if entries:
            parts.append(f"{intro}: {', '.join(entries)}.")
    if not parts:
        parts.append("You haven't set any search requirements in this session yet.")
    if include_next_step:
        parts.append('Tell me what to change, or say undo to reverse your last requirement edit.')
    return ' '.join(parts), state


def _session_choice_summary(session: Session, agent: Any) -> tuple[str, dict[str, Any]]:
    """Disclose session-scoped saved and hidden choices without making a new search."""
    def labels(asins: list[str]) -> list[str]:
        names = []
        for asin in asins:
            product = agent.get_catalog_product(asin) or {}
            title = ' '.join(str(product.get('title') or asin).split())
            names.append(title[:72] + ('...' if len(title) > 72 else ''))
        return names

    saved = list(session.selections)
    hidden = sorted(session.rejected_asins)
    saved_names = labels(saved)
    hidden_names = labels(hidden)
    parts = []
    if saved_names:
        parts.append('Saved for comparison: ' + '; '.join(saved_names[:3]) +
                     (f'; and {len(saved_names) - 3} more.' if len(saved_names) > 3 else '.'))
    if hidden_names:
        parts.append('Hidden from future results: ' + '; '.join(hidden_names[:3]) +
                     (f'; and {len(hidden_names) - 3} more.' if len(hidden_names) > 3 else '.'))
    if not parts:
        parts.append('No products are saved or hidden in this session.')
    return ' '.join(parts), {'saved_asins': saved, 'hidden_asins': hidden}


def _state_changes(
    before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for group in ("hard", "soft", "soft_priority", "soft_priority_turn", "excluded"):
        previous = before.get(group, {})
        current = after.get(group, {})
        keys = (previous.keys() & current.keys() if group in {'soft_priority', 'soft_priority_turn'}
                else previous.keys() | current.keys())
        for key in sorted(keys):
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


def _correction_acknowledgement(message: str, receipt: dict[str, Any]) -> str | None:
    """Explain applied corrections from the state diff, never from wording alone."""
    if not re.search(r'\b(?:actually|instead|change|switch|rather)\b', message, re.I):
        return None
    if receipt.get('intent_reset') or receipt.get('requirement_undo') or receipt.get('requirement_redo'):
        return None
    changes = receipt.get('state_changes') or []
    supported = {'color', 'material', 'size', 'budget_target', 'price_min', 'price_max', 'style'}
    if not changes or any(
        change.get('group') in {'soft_priority', 'soft_priority_turn'} or
        change.get('slot') not in supported or change.get('kind') not in {'added', 'updated', 'removed'}
        for change in changes
    ):
        return None

    def value(slot: str, raw: Any) -> str:
        if slot == 'budget_target':
            try:
                amount = raw[0] if isinstance(raw, (tuple, list)) else raw
                return f'around ${float(amount):g}'
            except (TypeError, ValueError):
                pass
        if slot in {'price_min', 'price_max'}:
            try:
                return f'${float(raw):g}'
            except (TypeError, ValueError):
                pass
        if isinstance(raw, (tuple, list)):
            return ' or '.join(value(slot, item) for item in raw)
        if slot == 'size':
            return str(raw).upper()
        return str(raw)

    def label(slot: str) -> str:
        return {'budget_target': 'your budget target', 'price_min': 'your minimum price',
                'price_max': 'your price limit'}.get(slot, f'the {slot}')

    edits = []
    for change in changes:
        slot = change['slot']
        name = label(slot)
        if change['kind'] == 'updated':
            edits.append(f"I changed {name} from {value(slot, change['previous'])} to {value(slot, change['value'])}.")
        elif change['kind'] == 'removed':
            prior = value(slot, change['previous'])
            if slot in {'color', 'material', 'size', 'style'}:
                tier = 'preference' if change['group'] == 'soft' else 'requirement'
                edits.append(f"I dropped the {prior} {slot} {tier}.")
            else:
                edits.append(f"I removed {name} ({prior}).")
        else:
            if slot in {'color', 'material', 'size', 'style'}:
                edits.append(f"I added {slot} {value(slot, change['value'])}.")
            else:
                edits.append(f"I added {name}: {value(slot, change['value'])}.")
    if len(changes) > 1 and all(change['kind'] == 'updated' for change in changes):
        clauses = [
            f"{label(change['slot'])} from {value(change['slot'], change['previous'])} "
            f"to {value(change['slot'], change['value'])}"
            for change in changes
        ]
        changed = 'I changed ' + (', '.join(clauses[:-1]) + ', and ' + clauses[-1]
                                  if len(clauses) > 2 else ' and '.join(clauses)) + '.'
    else:
        changed = ' '.join(edits)
    changed_slots = {change['slot'] for change in changes}
    hard = receipt.get('hard') or {}
    soft = receipt.get('soft') or {}
    kept = []
    if 'material' not in changed_slots and hard.get('material'):
        kept.append(str(hard['material']))
    if 'size' not in changed_slots and hard.get('size'):
        kept.append(f"size {str(hard['size']).upper()}")
    if 'budget_target' not in changed_slots and soft.get('budget_target'):
        try:
            kept.append(f"your roughly ${float(soft['budget_target'][0]):g} budget")
        except (TypeError, ValueError, IndexError):
            pass
    if kept:
        changed += ' I kept ' + ', '.join(kept[:3]) + '.'
    return changed + (' You can undo that change.' if len(changes) == 1
                      else ' You can undo these changes together.')


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
        "soft_priority": {name: row.get('priority', 1.0)
                          for name, row in state.get('slot_metadata', {}).get('soft', {}).items()},
        "soft_priority_turn": {name: row.get('priority_turn')
                               for name, row in state.get('slot_metadata', {}).get('soft', {}).items()},
        "ordered_preferences": {
            name: {'preferred': rows[0]['value'], 'also_acceptable': rows[1]['value']}
            for name, rows in state.get('soft_preferences', {}).items()
            if name in {'color', 'material', 'style'} and len(rows) == 2
            and rows[0]['weight'] > rows[1]['weight']
        },
        "excluded": state.get("exclusions", {}),
        "rejected_asins": state.get("rejected_asins", []),
        "state_evidence": _state_evidence(state),
        "cleared": state.get("suggestions", {}).get("cleared_slots", []),
        "requirement_undo": state.get("suggestions", {}).get("requirement_undo"),
        "can_undo_requirements": state.get('suggestions', {}).get('can_undo_requirements', False),
        "requirement_redo": state.get('suggestions', {}).get('requirement_redo'),
        "can_redo_requirements": state.get('suggestions', {}).get('can_redo_requirements', False),
        "conversation_act": state.get('suggestions', {}).get('conversation_act'),
        "negative_feedback": state.get('suggestions', {}).get('negative_feedback', False),
        "requested_more": state.get('suggestions', {}).get('requested_more', False),
        "requirements_changed": state.get('suggestions', {}).get('requirements_changed', False),
        "preference_priority_shift": state.get('suggestions', {}).get('preference_priority_shift'),
        "declined_options": state.get('suggestions', {}).get('declined_options', False),
        "intent_changed": state.get("suggestions", {}).get("intent_changed", False),
        "category_changed": state.get("suggestions", {}).get("category_changed", False),
        "search_reset": state.get("suggestions", {}).get("search_reset", False),
        "search_reset_scope": state.get("suggestions", {}).get("search_reset_scope"),
        "reset_history_action": state.get("suggestions", {}).get("reset_history_action"),
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
            turns_left=None if state.get('suggestions', {}).get('max_turns', 10) is None else max(0, state.get('suggestions', {}).get('max_turns', 10) - int(trace.get('turn', 1))),
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
            "max_turns": getattr(self.agent, 'max_turns', 10),
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
            previous_selection = deepcopy(session.selections)
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
            self._remember_selection(session, previous_selection)
            session.comparison_cache_key = None
            session.comparison_cache = None
            session.finalized_at_utc = None
            session.finalized_turn = None
            session.finalized_asins = ()
            return {"session_id": session_id, **self._selection_state(session)}

    @staticmethod
    def _remember_selection(session, previous):
        if previous != session.selections:
            session.selection_history.append(previous)
            del session.selection_history[:-20]
            session.selection_future.clear()

    def _prune_reset_snapshots(self, session_id: str, session: Session) -> None:
        """Keep only snapshots still reachable by requirement Undo or Redo."""
        if not session.reset_snapshots:
            return
        memory = getattr(self.agent, 'memory', None)
        history = getattr(memory, 'requirement_history', None)
        future = getattr(memory, 'requirement_future', None)
        if not isinstance(history, dict) or not isinstance(future, dict):
            return
        reachable = {
            entry['_reset_meta']['turn']
            for entry in (*history.get(session_id, ()), *future.get(session_id, ()))
            if isinstance(entry, dict) and isinstance(entry.get('_reset_meta'), dict)
        }
        for reset_turn in tuple(session.reset_snapshots):
            if reset_turn not in reachable:
                session.reset_snapshots.pop(reset_turn)

    def _selection_state(self, session: Session) -> dict[str, Any]:
        current_asins = tuple(session.selections)
        # Selected items may no longer be in the latest result batch. Return
        # their bounded cached facts so restoring a shortlist is visible too.
        selected_products = []
        receipt = session.audit_turns[-1]['receipt'] if session.audit_turns else {}
        for asin in current_asins:
            product = self.agent.get_catalog_product(asin) or {}
            match = explain_product(product, receipt)
            selected_products.append({
                'parent_asin': asin, 'title': str(product.get('title') or 'Catalog details unavailable'),
                'price': product.get('price'), 'rating': product.get('average_rating'),
                'match': match, 'advice': product_advice(product, match),
            })
        finalized = bool(
            current_asins
            and current_asins == session.finalized_asins
            and session.finalized_at_utc
        )
        return {
            "schema_version": SELECTION_STATE_SCHEMA_VERSION,
            "selected_asins": list(current_asins),
            "selected_products": selected_products,
            "selection_count": len(current_asins),
            "can_undo_selection": bool(session.selection_history),
            "can_redo_selection": bool(session.selection_future),
            "max_selections": MAX_SELECTIONS,
            "rejected_asins": sorted(session.rejected_asins),
            "can_undo_rejection": bool(session.rejection_history),
            "can_redo_rejection": bool(session.rejection_future),
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
        compound_answers: list[str] | None = None,
        compound_contexts: list[dict[str, Any] | None] | None = None,
        compound_focus: str | None = None,
        shortlist_actions: tuple[ControlIntent, ...] | None = None,
        preface: str | None = None,
    ) -> dict[str, Any]:
        by_rank = {int(product["rank"]): product for product in session.last_products}
        requested = [rank for rank in control.ranks if rank in by_rank]
        ignored = [rank for rank in control.ranks if rank not in by_rank]
        action = control.action
        previous_selection = deepcopy(session.selections)
        handoff: dict[str, Any] | None = None
        price_comparison: dict[str, Any] | None = None
        preference_comparison: dict[str, Any] | None = None
        review_comparison: dict[str, Any] | None = None
        comfort_question: dict[str, Any] | None = None
        rank_explanation: dict[str, Any] | None = None
        similarity_question: dict[str, Any] | None = None
        preference_state: dict[str, Any] | None = None
        session_choice_state: dict[str, Any] | None = None
        selection_changed = False
        feedback_changed = False
        rejected_feedback: list[str] = []
        liked_feedback: list[str] = []
        rejection_action_ids: tuple[str, ...] = ()

        if action == 'question_help':
            detail = control.attribute or 'The current choices are still open.'
        elif action == 'preferences':
            previous_receipt = session.audit_turns[-1]['receipt'] if session.audit_turns else {}
            detail, preference_state = _preference_summary(previous_receipt, include_next_step=False)
            choice_detail, session_choice_state = _session_choice_summary(session, self.agent)
            detail = ('Here is what I am using in this session. This is not an account-wide memory report. '
                      + detail + ' ' + choice_detail +
                      ' Tell me what to change, or say Undo to reverse your last requirement edit.')
            pending_question = getattr(getattr(self.agent, 'memory', None), 'pending', {}).get(session_id)
            if pending_question:
                detail += ' You can still answer the earlier question or keep browsing.'
            pending = session.pending_similarity
            if pending:
                facets = pending['facets']
                similarity_question = {
                    'target_slot': 'similarity_attribute', 'options': list(facets),
                    'evidence': {'parent_asin': pending['parent_asin'], 'facets': facets},
                }
                detail += (f" The question about #{pending['rank']} is still open: "
                           f"you can tell me whether its {', '.join(facets)} matters.")
        elif action == 'preference_compare_skip':
            detail = "No problem — I'll leave these options as they are. You can tell me what matters most, or ask about either item."
            self.agent.dismiss_pending_question(session_id)
        elif action == 'comfort_skip':
            detail = ("No problem — we'll leave the current options and preferences as they are. "
                      "You can ask about a product or tell me what matters next.")
            self.agent.dismiss_pending_question(session_id)
        elif action == 'decline':
            detail = ("No problem — I'll leave these options and preferences as they are."
                      if session.last_products else
                      "No problem. Tell me what you're looking for whenever you're ready.")
            self.agent.dismiss_pending_question(session_id)
        elif action == 'reset_scope':
            session.pending_reset_scope = True
            similarity_question = {'target_slot': 'reset_scope',
                                   'options': ['Reset search only', 'Reset everything', 'Cancel']}
            detail = ("You have saved or hidden products. Should I reset just the search and keep those choices, "
                      "reset everything including them, or leave things as they are? "
                      "Either reset can be undone.")
        elif action == 'reset_skip':
            detail = "Okay, I'll leave your search, saved products, and hidden products as they are."
        elif action == 'similar_clarification':
            pending = session.pending_similarity
            if pending:
                facets = pending['facets']
                similarity_question = {
                    'target_slot': 'similarity_attribute', 'options': list(facets),
                    'evidence': {'parent_asin': pending['parent_asin'], 'facets': facets},
                }
                if pending.get('repair_kind') == 'excluded_facet':
                    detail = (f"Got it—I'll leave the {pending['excluded_facet']} out. "
                              f"Which of #{pending['rank']}'s remaining listed details matters: "
                              f"{', '.join(facets)}? You can also tell me a different preference.")
                else:
                    detail = (f"#{pending['rank']} has three supported details. Which two did you mean: "
                              f"{', '.join(facets)}? You can name them together.")
            else:
                detail = ("No need to settle that now. I've kept the current results and preferences; "
                          "you can name a detail or make a new request whenever you're ready.")
        elif action == 'similar':
            if control.uses_focus:
                requested = [rank for rank, product in by_rank.items()
                             if product['parent_asin'] == session.focused_product_id]
            if len(requested) != 1:
                session.pending_similarity = None
                detail = ("Which item do you mean? Please use one rank from the current list."
                          if not control.ranks or control.uses_focus else
                          "That rank is not in the current list. Please use a displayed rank.")
            else:
                rank = requested[0]
                product = by_rank[rank]
                source = self.agent.get_catalog_product(product['parent_asin']) or {}
                supported = supported_facets(source)
                facets = facets_not_in_preferences(supported, session.previous_state)
                had_supported_facets = bool(supported)
                session.focused_product_id = product['parent_asin']
                result_key = tuple(row['parent_asin'] for row in session.last_products)
                session.focused_by_result_set[result_key] = session.focused_product_id
                if facets:
                    session.pending_similarity = {
                        'parent_asin': product['parent_asin'], 'rank': rank,
                        'title': product['title'], 'facets': facets,
                    }
                    similarity_question = {
                        'target_slot': 'similarity_attribute',
                        'options': list(facets),
                        'evidence': {'parent_asin': product['parent_asin'], 'facets': facets},
                    }
                    labels = {'fit': 'listed fit', 'color': 'color', 'fabric': 'verified fabric'}
                    choices = ', '.join(labels[key] for key in facets)
                    detail = (f"What do you like about #{rank}—its {choices}? "
                              "Pick one or more, or tell me another detail that matters. I'll keep these results while you decide.")
                else:
                    session.pending_similarity = None
                    detail = (f"You already have the verified details I can carry over from #{rank} in your preferences. "
                              "What different detail should I match?"
                              if had_supported_facets else
                              f"I can use #{rank} as a reference, but its listing doesn't clearly verify a fit, "
                              "color, or fabric to carry into a new search. What detail do you want to match?")
                if control.attribute == 'cheaper_unpriced':
                    detail = (f"#{rank} has no catalog price, so I can't verify what would be cheaper. "
                              "I haven't changed your search or made up a price. Give me a price limit "
                              "if you have one. " + detail)
                elif control.attribute == 'cheaper_no_lower_price':
                    detail = (f"#{rank}'s catalog price leaves no lower nonnegative cent amount to search. "
                              "I haven't changed your search. " + detail)
            if shortlist_actions:
                hidden_ranks = []
                for step in shortlist_actions:
                    if step.action != 'reject':
                        continue
                    for hidden_rank in step.ranks:
                        hidden_product = by_rank.get(hidden_rank)
                        if not hidden_product:
                            continue
                        hidden_asin = hidden_product['parent_asin']
                        if hidden_asin not in session.rejected_asins:
                            session.rejected_asins.add(hidden_asin)
                            rejected_feedback.append(hidden_asin)
                            feedback_changed = True
                        if session.selections.pop(hidden_asin, None) is not None:
                            selection_changed = True
                        hidden_ranks.append(hidden_rank)
                if hidden_ranks:
                    labels = ', '.join(f'#{rank}' for rank in hidden_ranks)
                    detail = f"I'll leave {labels} out of future results. " + detail
        elif action == 'multi_selection':
            statements = []
            for step in shortlist_actions or ():
                valid = [rank for rank in step.ranks if rank in by_rank]
                if step.action == 'select':
                    added, existing, capped = [], [], []
                    for rank in valid:
                        asin = by_rank[rank]['parent_asin']
                        if asin in session.selections:
                            existing.append(rank)
                        elif len(session.selections) >= MAX_SELECTIONS:
                            capped.append(rank)
                        else:
                            session.selections[asin] = {
                                'selected_at_turn': turn, 'intent_version': session.intent_version,
                                'reason': f'Selected by chat command at rank {rank}',
                            }
                            selection_changed = True
                            added.append(rank)
                            if asin in session.rejected_asins:
                                session.rejected_asins.remove(asin)
                                liked_feedback.append(asin)
                                feedback_changed = True
                    if added:
                        statements.append(f"Kept {', '.join(f'#{rank}' for rank in added)} on your shortlist.")
                    if existing:
                        statements.append(f"{', '.join(f'#{rank}' for rank in existing)} was already on your shortlist.")
                    if capped:
                        statements.append(f"The shortlist is full; I couldn't add {', '.join(f'#{rank}' for rank in capped)}.")
                elif step.action == 'remove':
                    removed = [rank for rank in valid if session.selections.pop(by_rank[rank]['parent_asin'], None) is not None]
                    selection_changed = selection_changed or bool(removed)
                    if removed:
                        statements.append(f"Removed {', '.join(f'#{rank}' for rank in removed)} from your shortlist.")
                    elif valid:
                        statements.append(f"{', '.join(f'#{rank}' for rank in valid)} wasn't on your shortlist.")
                elif step.action == 'reject':
                    for rank in valid:
                        asin = by_rank[rank]['parent_asin']
                        if asin not in session.rejected_asins:
                            session.rejected_asins.add(asin)
                            rejected_feedback.append(asin)
                            feedback_changed = True
                        if session.selections.pop(asin, None) is not None:
                            selection_changed = True
                    if valid:
                        statements.append(f"I'll leave {', '.join(f'#{rank}' for rank in valid)} out of future results.")
            detail = ' '.join(statements) or 'I could not find those ranks in the current list.'
        elif action == 'mixed_request':
            detail = ("I heard more than one action, but can't safely apply this combination in one turn. "
                      "I haven't changed your shortlist or search. Which should I do first?")
        elif action == 'multi_detail':
            detail = '\n\n'.join(compound_answers or [])
            session.focused_product_id = compound_focus
            result_key = tuple(product['parent_asin'] for product in session.last_products)
            if compound_focus:
                session.focused_by_result_set[result_key] = compound_focus
            else:
                session.focused_by_result_set.pop(result_key, None)
            session.pending_detail_attribute = None
        elif action == 'detail':
            if control.uses_focus:
                requested = [rank for rank, product in by_rank.items()
                             if product['parent_asin'] == session.focused_product_id]
            if len(requested) == 1:
                rank = requested[0]
                session.focused_product_id = by_rank[rank]['parent_asin']
                result_key = tuple(product['parent_asin'] for product in session.last_products)
                session.focused_by_result_set[result_key] = session.focused_product_id
                session.pending_detail_attribute = None
                product = self.agent.get_catalog_product(by_rank[rank]['parent_asin']) or {}
                detail = answer_product_question(product, rank, control.attribute, session.locale)
            else:
                session.focused_product_id = None
                result_key = tuple(product['parent_asin'] for product in session.last_products)
                session.focused_by_result_set.pop(result_key, None)
                session.pending_detail_attribute = control.attribute
                if control.uses_focus:
                    detail = '你指的是哪一款？告诉我当前列表的序号就好。' if session.locale == 'zh' else 'Which product do you mean? Please give its rank in the current list.'
                else:
                    detail = '当前结果里没有这个序号，请按页面上的序号再问我。' if session.locale == 'zh' else 'That rank is not in the current results. Please use a displayed rank.'
        elif action == 'explain_rank':
            if control.uses_focus:
                requested = [rank for rank, product in by_rank.items()
                             if product['parent_asin'] == session.focused_product_id]
            if len(requested) != 1:
                detail = ("Which product do you mean? Give me its rank in the current list."
                          if control.uses_focus or not control.ranks else
                          "That rank is not in the current results. Please use a displayed rank.")
            else:
                rank = requested[0]
                product = by_rank[rank]
                session.focused_product_id = product['parent_asin']
                result_key = tuple(row['parent_asin'] for row in session.last_products)
                session.focused_by_result_set[result_key] = session.focused_product_id
                matches = list(dict.fromkeys(
                    str(signal.get('value', '')).strip() for signal in product.get('match', {}).get('signals', ())
                    if signal.get('status') == 'supported' and signal.get('tier') == 'hard'
                    and str(signal.get('value', '')).strip()
                ))[:3]
                soft_matches = list(dict.fromkeys(
                    (signal.get('slot'), ', '.join(map(str, signal['value']))
                     if isinstance(signal.get('value'), (list, tuple)) else str(signal.get('value')))
                    for signal in product.get('match', {}).get('signals', ())
                    if signal.get('status') == 'supported' and signal.get('tier') == 'soft'
                    and signal.get('slot') in {'color', 'style', 'material', 'size'}
                    and signal.get('value')
                ))
                highlights = list(dict.fromkeys(
                    ' '.join(str(row.get('evidence', '')).split())[:100]
                    for row in product.get('advice', {}).get('catalog_highlights', ())
                    if str(row.get('evidence', '')).strip()
                ))[:2]
                budget_unverified = bool(
                    product.get('price') is None and
                    ('budget_target' in session.previous_state.get('soft', {}) or
                     any(key in session.previous_state.get('hard', {}) for key in ('price_min', 'price_max')))
                )
                previous_receipt = session.audit_turns[-1]['receipt'] if session.audit_turns else {}
                ranking_method = previous_receipt.get('ranking_method')
                rank_explanation = {'rank': rank, 'parent_asin': product['parent_asin'],
                                    'supported_requirements': matches, 'listing_highlights': highlights,
                                    'supported_preferences': [{'slot': slot, 'value': value}
                                                              for slot, value in soft_matches],
                                    'scope': 'current_display', 'budget_unverified': budget_unverified,
                                    'ranking_method': ranking_method}
                if matches:
                    stated = (matches[0] if len(matches) == 1 else
                              ' and '.join(matches) if len(matches) == 2 else
                              ', '.join(matches[:-1]) + ', and ' + matches[-1])
                    detail = f"I recommended #{rank} because its catalog record supports what you asked for: {stated}."
                else:
                    detail = f"I don't have a specific requirement match to cite for #{rank}."
                if control.attribute == 'claimed_first' and rank != 1:
                    detail = f"#{rank} is currently ranked #{rank}, not first. " + detail
                if highlights:
                    detail += ' The listing mentions ' + ' and '.join(f'"{item}"' for item in highlights) + '.'
                if ranking_method == 'search_tool+supported_primary_color_first':
                    detail += (" You said one color was your first choice and another was acceptable. "
                               "Listings with verified evidence for your first choice come first; "
                               "the alternative remains available, and order within each group follows search relevance.")
                elif ranking_method == 'search_tool+supported_soft_preferences':
                    detail += (" Catalog-supported preferences can move items ahead; "
                               "the original search order is kept within each evidence group. "
                               "Unlabeled sizes and fits remain unverified, not ruled out.")
                elif ranking_method == 'search_tool+supported_soft_size_first':
                    detail += (" Items with an explicit listing match for your preferred size come first; "
                               "other sizes remain available. Check the seller's size chart before buying.")
                elif ranking_method == 'search_tool+supported_soft_fit_guidance':
                    detail += (" Listings explicitly labeled with the fit you wanted to avoid move lower, "
                               "but remain available; unlabeled fits are not verified. "
                               "The original search order is kept within each group.")
                elif ranking_method == 'search_tool+supported_soft_style_first':
                    detail += (" Items with catalog evidence for your fit preference come first; "
                               "the original search order is kept within each group. "
                               "That is not a verified quality advantage.")
                elif ranking_method in {'search_tool+weighted_catalog_preferences',
                                        'search_tool+supported_soft_material_first'}:
                    if soft_matches:
                        labels = ', '.join(value for _, value in soft_matches[:3])
                        detail += f" Its listing supports your preference for {labels}."
                    weights = self.agent.memory.snapshot(session_id).soft_preferences
                    fit_weight = max((row.weight for row in weights.get('style', ())), default=0.0)
                    fabric_weight = max((row.weight for row in weights.get('material', ())), default=0.0)
                    rank_explanation['preference_weights'] = {
                        'fit': round(fit_weight, 6), 'fabric': round(fabric_weight, 6)}
                    if fit_weight and fabric_weight and fit_weight != fabric_weight:
                        stronger, weaker = ('fit', 'fabric') if fit_weight > fabric_weight else ('fabric', 'fit')
                        detail += f" Your {stronger} preference currently has more weight than {weaker}."
                    detail += (" Only catalog-supported preferences affect this order; items tied on that evidence "
                               "keep the search tool's order. This is not a verified quality advantage.")
                else:
                    detail += (" Its exact position comes from the current relevance ranking, "
                               "not a verified quality advantage over the other items.")
                if budget_unverified:
                    detail += " This source has no price for it, so your budget fit remains unverified."
        elif action == 'comfort_question':
            session.pending_comfort = None
            if control.uses_focus:
                requested = [rank for rank, product in by_rank.items()
                             if product['parent_asin'] == session.focused_product_id]
            comfort_question = {'scope': 'current_display', 'requested_ranks': list(control.ranks),
                                'verified_winner': None, 'evidence': 'unavailable'}
            if not session.last_products:
                detail = "I don't have any displayed products to check yet. Tell me what you're looking for first."
            elif control.ranks and ignored:
                detail = "That rank isn't in the current list. Please use a displayed rank."
            elif control.ranks or control.uses_focus:
                if not requested:
                    detail = "Which product do you mean? Please give me its rank in the current list."
                else:
                    rank = requested[0]
                    comfort_question['requested_ranks'] = [rank]
                    detail = (f"I can't verify how comfortable #{rank} feels from this catalog. "
                              "A fit label or seller description isn't a wear test; check recent buyer reviews "
                              "and the size chart before deciding.")
            else:
                detail = ("I can't honestly pick the most comfortable one from these listings. "
                          "They don't provide comparable wear-test or review-text evidence.")
                facets = {}
                for label, pattern, value in (
                    ('breathability', r'\bbreathable\b', 'breathable'),
                    ('relaxed fit', r'\brelaxed[ -]fit\b', 'relaxed fit'),
                ):
                    ranks = []
                    for product in session.last_products:
                        source = self.agent.get_catalog_product(product['parent_asin']) or {}
                        fields = [str(source.get('title') or '')]
                        fields.extend(str(item) for item in (source.get('features') or ()))
                        negation = (r'\b(?:not|less|non)\s+breathable\b' if label == 'breathability'
                                    else r'\b(?:not|never)\s+(?:a\s+)?relaxed[ -]fit\b')
                        if any(re.search(pattern, field, re.I) and not re.search(negation, field, re.I)
                               for field in fields):
                            ranks.append(int(product['rank']))
                    if ranks and len(ranks) < len(session.last_products):
                        facets[label] = {'value': value, 'listing_ranks': ranks}
                if facets:
                    session.pending_comfort = facets
                    comfort_question['offered_facets'] = facets
                    choices = ' or '.join(facets)
                    prompt = ("If that's what you mean, tell me and I'll prioritize that listed detail."
                              if len(facets) == 1 else
                              "If either matters to you, tell me which and I'll prioritize that listed detail.")
                    detail += (f" Some listings explicitly mention {choices}. {prompt} "
                               "You can also keep browsing these options.")
        elif action == 'review_compare':
            def rating_value(product):
                if isinstance(product.get('rating'), bool):
                    return None
                try:
                    value = float(product.get('rating'))
                except (TypeError, ValueError):
                    return None
                return value if math.isfinite(value) and 0 <= value <= 5 else None

            def rating_count(product):
                raw = product.get('rating_count')
                if isinstance(raw, bool):
                    return None
                try:
                    value = int(str(raw).replace(',', ''))
                except (TypeError, ValueError):
                    return None
                return value if value > 0 else None

            rows = []
            for product in session.last_products:
                count = rating_count(product)
                rating = rating_value(product)
                if product.get('rating_count') is not None and count is None:
                    rating = None  # A zero or invalid count cannot support an average.
                rows.append((product, rating, count))
            rated = [(product, rating, count) for product, rating, count in rows if rating is not None]
            counted = [(product, count, rating) for product, rating, count in rows if count is not None]
            review_comparison = {'mode': control.attribute, 'rated_count': len(rated),
                                 'counted_count': len(counted), 'winner_ranks': [],
                                 'unknown_rating_count': len(session.last_products) - len(rated)}
            if not session.last_products:
                detail = "I don't have a displayed list to compare yet. Tell me what you're looking for first."
            elif control.attribute == 'most_reviews' and counted:
                largest = max(count for _, count, _ in counted)
                winners = [(product, rating) for product, count, rating in counted if count == largest]
                review_comparison['winner_ranks'] = [product['rank'] for product, _ in winners]
                labels = ', '.join(f"#{product['rank']}" for product, _ in winners[:3])
                detail = (f"{labels} {'has' if len(winners) == 1 else 'tie for'} the most listed ratings "
                          f"here: {largest:,}.")
                if len(winners) == 1 and winners[0][1] is not None:
                    detail += f" Its catalog average is {winners[0][1]:g}/5."
                detail += " More ratings do not necessarily mean better reviews; these are not live figures."
            elif control.attribute == 'most_reviews':
                detail = ("These listings don't give usable rating counts, so I can't tell which "
                          "has the most reviews. I'll keep the current list.")
            elif rated:
                highest = max(rating for _, rating, _ in rated)
                winners = [(product, count) for product, rating, count in rated if rating == highest]
                review_comparison['winner_ranks'] = [product['rank'] for product, _ in winners]
                labels = ', '.join(f"#{product['rank']}" for product, _ in winners[:3])
                detail = (f"By catalog average score, {labels} {'is' if len(winners) == 1 else 'are'} "
                          f"highest in this list at {highest:g}/5")
                if len(winners) == 1 and winners[0][1] is not None:
                    detail += f" from {winners[0][1]:,} ratings"
                detail += "."
                if control.attribute == 'best_reviews' and counted:
                    largest = max(count for _, count, _ in counted)
                    most_reviewed = next((product for product, count, _ in counted if count == largest), None)
                    if most_reviewed and most_reviewed['rank'] not in review_comparison['winner_ranks']:
                        detail += f" #{most_reviewed['rank']} has the most ratings ({largest:,})."
                if review_comparison['unknown_rating_count']:
                    detail += (f" {review_comparison['unknown_rating_count']} item(s) have no usable "
                               "average, so I can't rule them out.")
                detail += " I haven't checked review text or live scores."
            else:
                detail = ("This catalog doesn't include usable review scores for these items, so I "
                          "can't identify a best-reviewed one. I'll keep the list; check current seller "
                          "reviews if that matters most.")
        elif action == 'price_compare':
            def known_price(product):
                value = product.get('price')
                if isinstance(value, bool):
                    return None
                try:
                    number = float(str(value).replace('$', '').replace(',', ''))
                except (TypeError, ValueError):
                    return None
                return number if math.isfinite(number) and number >= 0 else None

            priced = [(product, known_price(product)) for product in session.last_products]
            priced = [(product, price) for product, price in priced if price is not None]
            unknown_count = len(session.last_products) - len(priced)
            baseline = (by_rank.get(control.ranks[0]) if control.ranks else
                        next((product for product in session.last_products
                              if product['parent_asin'] == session.focused_product_id), None))
            if control.attribute == 'cheaper' and baseline and control.ranks:
                session.focused_product_id = baseline['parent_asin']
                result_key = tuple(product['parent_asin'] for product in session.last_products)
                session.focused_by_result_set[result_key] = session.focused_product_id
            price_comparison = {'mode': control.attribute, 'priced_count': len(priced),
                                'unknown_count': unknown_count,
                                'baseline_rank': baseline['rank'] if baseline and control.attribute == 'cheaper' else None,
                                'cheaper_ranks': []}
            if not session.last_products:
                detail = "I don't have a displayed list to compare yet. Tell me what you're looking for first."
            elif not priced:
                lead = ("I can't name the cheapest" if control.attribute == 'cheapest' else
                        "I can't tell which is cheaper")
                detail = (f"{lead}: this catalog doesn't list prices for these items. "
                          "I'll keep the current list; a live price check is needed before comparing costs.")
            elif control.attribute == 'cheapest':
                lowest = min(price for _, price in priced)
                winners = [product for product, price in priced if price == lowest]
                ranks = ', '.join(f"#{product['rank']}" for product in winners[:3])
                detail = (f"Among the {len(priced)} displayed items with catalog prices, {ranks} "
                          f"{'has' if len(winners) == 1 else 'tie for'} the lowest "
                          f"{'price of ' if len(winners) == 1 else 'price at '}${lowest:g}. "
                          "These are catalog prices, not live offers.")
                if unknown_count:
                    detail += f" {unknown_count} other item(s) have no price, so I can't rule them out."
            else:
                if baseline is None:
                    detail = ("Cheaper than which item? Tell me a rank from this list, such as "
                              "'cheaper than #2', and I'll compare the listed prices.")
                else:
                    rank = baseline['rank']
                    baseline_price = known_price(baseline)
                    if baseline_price is None:
                        detail = (f"I don't have a catalog price for #{rank}, so I can't tell which "
                                  "displayed items are cheaper than it. The list is unchanged.")
                    else:
                        cheaper = sorted(((product, price) for product, price in priced
                                          if price < baseline_price), key=lambda row: (row[1], row[0]['rank']))
                        price_comparison['cheaper_ranks'] = [product['rank'] for product, _ in cheaper]
                        if cheaper:
                            labels = [f"#{product['rank']} (${price:g})" for product, price in cheaper[:3]]
                            choices = (labels[0] if len(labels) == 1 else
                                       ' and '.join(labels) if len(labels) == 2 else
                                       ', '.join(labels[:-1]) + ', and ' + labels[-1])
                            detail = (f"Compared with #{rank} (${baseline_price:g}), {choices} "
                                      f"{'is' if len(labels) == 1 else 'are'} cheaper in this list. "
                                      "These are catalog prices, not live offers.")
                        else:
                            detail = (f"None of the other displayed items with catalog prices is cheaper "
                                      f"than #{rank} (${baseline_price:g}). These are not live prices.")
                        if unknown_count:
                            detail += f" {unknown_count} item(s) have no price, so I can't compare them."
        elif action == 'preference_compare':
            if not by_rank:
                detail = "I don't have any displayed items to compare yet. What are you shopping for?"
            elif ignored:
                detail = (f"I can't find {', '.join(f'#{rank}' for rank in ignored)} in the current results. "
                          "Which displayed items should I compare?")
            elif len(control.ranks) > 3:
                detail = "Please choose up to three displayed items to compare."
            elif len(control.ranks) == 1:
                detail = f"Which other displayed item should I compare with #{control.ranks[0]}?"
            else:
                chosen = ([by_rank[rank] for rank in requested] if requested else
                          list(by_rank.values())[:3 if control.attribute == 'buying_advice' else 2])
                if len(chosen) < 2:
                    detail = "I need at least two displayed items to compare."
                else:
                    detail, preference_comparison = compare_preferences(
                        chosen, self.agent.get_catalog_product,
                        decision_help=control.attribute == 'buying_advice')
                    session.pending_comparison = preference_comparison['question']
        elif action in {"select", "compare"}:
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
        elif action == 'undo_selection':
            if session.selection_history:
                session.selection_future.append(previous_selection)
                session.selections = session.selection_history.pop()
                selection_changed = True
                detail = "I've undone the last shortlist edit. Your search preferences are unchanged; the restored choices are not finalized."
            else:
                detail = "There isn't a shortlist edit to undo for your current requirements. Your choices are unchanged."
        elif action == 'redo_selection':
            if session.selection_future:
                session.selection_history.append(previous_selection)
                session.selections = session.selection_future.pop()
                selection_changed = True
                detail = "I've redone the shortlist edit. Your search preferences are unchanged; the restored choices are not finalized."
            else:
                detail = "There isn't a shortlist edit to redo. Your choices are unchanged."
        elif action == 'undo_rejection':
            if session.rejection_history:
                rejection_action_ids = session.rejection_history[-1]
                liked_feedback.extend(rejection_action_ids)
                session.rejected_asins.difference_update(rejection_action_ids)
                feedback_changed = True
                detail = ("I've undone your last product rejection. That item can appear in future searches; "
                          "the current list and search requirements stay as they are.")
            else:
                detail = "There isn't a product rejection to undo. Your current list and search requirements are unchanged."
        elif action == 'redo_rejection':
            if session.rejection_future:
                rejection_action_ids = session.rejection_future[-1]
                rejected_feedback.extend(rejection_action_ids)
                session.rejected_asins.update(rejection_action_ids)
                removed_selections = [session.selections.pop(asin, None) for asin in rejection_action_ids]
                selection_changed = any(value is not None for value in removed_selections)
                feedback_changed = True
                detail = ("I've reapplied that product rejection. The item will stay out of future searches; "
                          "the current list and search requirements stay as they are.")
            else:
                detail = "There isn't a product rejection to redo. Your choices are unchanged."
        elif action == 'retain':
            detail = "Understood. I've left your shortlist and search preferences unchanged."
        elif action == 'hold':
            session.finalized_at_utc = None
            session.finalized_turn = None
            session.finalized_asins = ()
            session.comparison_cache_key = None
            session.comparison_cache = None
            detail = ("I haven't finalized anything. I can't automatically confirm a shortlist based on that condition; your choices and search preferences are unchanged."
                      if control.attribute == 'conditional' else
                      "No rush. I'll keep your choices as a draft so you can continue comparing. Your search preferences are unchanged.")
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
        if action in {'undo_rejection', 'redo_rejection', 'detail'} and session.pending_similarity:
            pending = session.pending_similarity
            similarity_question = {
                'target_slot': 'similarity_attribute', 'options': list(pending['facets']),
                'evidence': {'parent_asin': pending['parent_asin'],
                             'facets': pending['facets']},
            }
            detail += f" You can still tell me which listed detail of #{pending['rank']} matters to you."

        feedback_state = None
        if feedback_changed:
            session.selection_history.clear()
            session.selection_future.clear()
        elif action in {'select', 'compare', 'remove', 'clear', 'multi_selection'}:
            self._remember_selection(session, previous_selection)
        if feedback_changed:
            feedback_state = self.agent.record_product_feedback(
                session_id,
                source_turn=turn,
                rejected_asins=rejected_feedback,
                liked_asins=liked_feedback,
            ).to_dict()
            if action == 'undo_rejection':
                session.rejection_history.pop()
                session.rejection_future.append(rejection_action_ids)
            elif action == 'redo_rejection':
                session.rejection_future.pop()
                session.rejection_history.append(rejection_action_ids)
            else:
                if liked_feedback:
                    liked = set(liked_feedback)
                    session.rejection_history = [remaining for group in session.rejection_history
                                                 if (remaining := tuple(asin for asin in group if asin not in liked))]
                    session.rejection_future = [remaining for group in session.rejection_future
                                                if (remaining := tuple(asin for asin in group if asin not in liked))]
                if rejected_feedback:
                    session.rejection_history.append(tuple(dict.fromkeys(rejected_feedback)))
                    del session.rejection_history[:-20]
                    session.rejection_future.clear()
        if selection_changed or feedback_changed:
            session.comparison_cache_key = None
            session.comparison_cache = None
            session.finalized_at_utc = None
            session.finalized_turn = None
            session.finalized_asins = ()
        if action in {"compare", "handoff", "finalize"}:
            handoff = self.selection_handoff(session_id)
        if action in {'reset_scope', 'reset_skip'} and control.attribute:
            detail = control.attribute + '\n\n' + detail
        detail = detail if action in {'question_help', 'detail', 'multi_detail', 'price_compare', 'review_compare', 'comfort_question', 'comfort_skip', 'decline', 'reset_scope', 'reset_skip', 'explain_rank', 'preference_compare', 'preference_compare_skip'} else localized_control_message(
            locale=session.locale,
            action=action,
            selection_count=len(session.selections),
            handoff_ready=bool(session.selections),
            fallback=detail,
        )
        if preface:
            detail = preface + '\n\n' + detail
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
        if action == 'question_help':
            for field in ('hard', 'soft', 'excluded', 'ordered_preferences'):
                receipt.setdefault(field, {})
        receipt.update(
            {
                "state_changes": [],
                "pre_action": "control",
                "pre_reason": ('shopping_term_help' if action == 'question_help' else
                               'product_detail' if action in {'detail', 'multi_detail'} else
                               'price_comparison' if action == 'price_compare' else
                               'preference_comparison' if action == 'preference_compare' else
                               'preference_comparison_skip' if action == 'preference_compare_skip' else
                               'review_comparison' if action == 'review_compare' else
                               'comfort_question' if action == 'comfort_question' else
                               'comfort_question_skip' if action == 'comfort_skip' else
                               'offer_declined' if action == 'decline' else
                               'reset_scope_clarification' if action == 'reset_scope' else
                               'reset_scope_cancelled' if action == 'reset_skip' else
                               'rank_explanation' if action == 'explain_rank' else
                               'preference_summary' if action == 'preferences' else f"selection_{action}"),
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
                "rejection_undo": 'applied' if action == 'undo_rejection' and rejection_action_ids else
                                  'empty' if action == 'undo_rejection' else None,
                "rejection_redo": 'applied' if action == 'redo_rejection' and rejection_action_ids else
                                  'empty' if action == 'redo_rejection' else None,
                "can_undo_rejection": bool(session.rejection_history),
                "can_redo_rejection": bool(session.rejection_future),
                "selection_reset": False,
                "question": (similarity_question or
                             deepcopy(getattr(getattr(self.agent, 'memory', None), 'pending', {})
                                      .get(session_id))
                             if action in {'question_help', 'preferences'} or preface else similarity_question),
                "suggested_replies": ([option['value'] for option in preference_comparison['question']['options']]
                                      + ['Either is fine']
                                      if preference_comparison and (preference_comparison.get('question') or {}).get('kind') == 'decision_tradeoff'
                                      else None),
                "display_mode": "retained_previous_results",
                "new_product_count": 0,
                "repeat_count": len(session.last_products),
                "focused_product_id": session.focused_product_id,
                "price_comparison": price_comparison,
                "preference_comparison": preference_comparison,
                "review_comparison": review_comparison,
                "comfort_question": comfort_question,
                "rank_explanation": rank_explanation,
                "preference_summary": preference_state,
                "session_choice_summary": session_choice_state,
                "similarity_reference": ({'parent_asin': session.pending_similarity['parent_asin'],
                                          'rank': session.pending_similarity['rank'],
                                          'facets': session.pending_similarity['facets']}
                                         if action in {'similar', 'similar_clarification', 'preferences', 'detail',
                                                       'undo_rejection', 'redo_rejection'}
                                         and session.pending_similarity else None),
                "compound_request": ({"detail_contexts": compound_contexts, "requirements_message": None}
                                     if action == 'multi_detail' else
                                     {"shortlist_actions": [{"action": step.action, "ranks": list(step.ranks)}
                                                            for step in shortlist_actions or ()]}
                                     if action == 'multi_selection' else
                                     {"rejected_ranks": [rank for step in shortlist_actions or ()
                                                         for rank in step.ranks],
                                      "similar_ranks": list(control.ranks)}
                                     if action == 'similar' and shortlist_actions else None),
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
            "remaining_turns": None if getattr(self.agent, 'max_turns', 10) is None else max(0, getattr(self.agent, 'max_turns', 10) + 1 - session.next_turn),
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
            turn_limit = getattr(self.agent, 'max_turns', 10)
            if turn_limit is not None and session.next_turn > turn_limit:
                raise ApiError(HTTPStatus.CONFLICT, "This conversation has reached its configured turn limit.", "turn_limit")
            turn = session.next_turn
            pending_questions = getattr(getattr(self.agent, 'memory', None), 'pending', {})
            pending_question = pending_questions.get(session_id)
            question_reason = explain_pending_question(message, pending_question)
            if question_reason:
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('question_help', attribute=question_reason))
            pending_help = explain_shopping_term(message, pending_question)
            if pending_help:
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('question_help', attribute=pending_help))
            term_request = split_term_question_and_request(message, pending_question)
            if term_request:
                product_question = parse_control_intent(term_request[1])
                if product_question and product_question.action == 'detail':
                    return self._control_turn(session_id, session, message, turn,
                                              product_question, preface=term_request[0])
                from intent_router.turn_router import TurnIntentRouter
                parsed_request = TurnIntentRouter().understand_turn(
                    term_request[1], pending_question=pending_question)
                reset_cancel = bool(session.pending_reset_scope and re.fullmatch(
                    r'(?:cancel|keep (?:everything|things as they are)|never mind)[.!?]*',
                    term_request[1], re.I))
                if (not parsed_request.slot_updates and not reset_cancel and
                        not parsed_request.decision_evidence.get('requested_results')):
                    courtesy = bool(re.fullmatch(
                        r'(?:thanks|thank you|just curious|i(?: am|\'m) not sure)[.!?]*',
                        term_request[1].casefold()))
                    explanation = (term_request[0] +
                                   (" I haven't changed your preferences. You can still choose an option or say Show me first."
                                    if courtesy and pending_question else
                                    " I haven't changed your preferences. You can keep browsing."
                                    if courtesy else
                                    " I haven't changed your preferences. Please ask the other question separately."))
                    return self._control_turn(session_id, session, message, turn,
                                              ControlIntent('question_help', attribute=explanation))
            conversation_message = term_request[1] if term_request else message
            reset_pending = session.pending_reset_scope
            session.pending_reset_scope = False
            if reset_pending and re.fullmatch(r'(?:cancel|keep (?:everything|things as they are)|never mind)[.!?]*', conversation_message, re.I):
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('reset_skip',
                                                        attribute=term_request[0] if term_request else None))
            full_reset_requested = bool(re.fullmatch(r'(?:reset|clear) everything[.!?]*', conversation_message, re.I))
            pre_full_snapshot = ({
                'selections': deepcopy(session.selections),
                'selection_history': deepcopy(session.selection_history),
                'selection_future': deepcopy(session.selection_future),
                'rejected_asins': set(session.rejected_asins),
                'rejection_history': deepcopy(session.rejection_history),
                'rejection_future': deepcopy(session.rejection_future),
                'last_products': deepcopy(session.last_products),
                'focused_product_id': session.focused_product_id,
                'focused_by_result_set': deepcopy(session.focused_by_result_set),
                'finalized_at_utc': session.finalized_at_utc,
                'finalized_turn': session.finalized_turn,
                'finalized_asins': session.finalized_asins,
            } if full_reset_requested or session.previous_category else None)
            reset_request = re.fullmatch(
                r"(?:let'?s|let us)?\s*start over|start a new search|"
                r"clear (?:all )?(?:my |the )?search (?:requirements|preferences|filters)",
                conversation_message.strip(' .!?'), re.I,
            )
            if reset_request and (session.selections or session.rejected_asins):
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('reset_scope',
                                                        attribute=term_request[0] if term_request else None))
            direct_control = parse_control_intent(conversation_message)
            comfort_pending = session.pending_comfort
            session.pending_comfort = None  # A comfort choice is valid only on the next turn.
            if comfort_pending and direct_control is not None and direct_control.action == 'decline':
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('comfort_skip'))
            comfort_resolution = None
            comfort_decline_extra = None
            if comfort_pending and direct_control is None:
                normalized = re.sub(r'^(?:i (?:mean|prefer|care about) )', '', message.casefold()).strip(' .!?')
                decline = re.fullmatch(
                    r'(?:no thanks|neither|none of those|no preference|not important|'
                    r'keep browsing|show me first)'
                    r'(?:\s*,?\s*(?:but|and)\s+(?P<extra>.+))?', normalized)
                if decline:
                    extra = decline.group('extra')
                    if not extra:
                        return self._control_turn(session_id, session, message, turn,
                                                  ControlIntent('comfort_skip'))
                    from intent_router.turn_router import TurnIntentRouter
                    parsed_extra = TurnIntentRouter().understand_turn(extra)
                    if parsed_extra.slot_updates and not parse_control_intent(extra):
                        comfort_decline_extra = extra
                aliases = {'breathability': 'breathability', 'breathable': 'breathability',
                           'relaxed fit': 'relaxed fit', 'a relaxed fit': 'relaxed fit'}
                followup = re.fullmatch(
                    r'(?P<choice>breathability|breathable|a relaxed fit|relaxed fit)'
                    r'(?:\s*(?:,|and|but)\s*(?P<extra>.+))?', normalized)
                chosen = aliases.get(followup.group('choice')) if followup else None
                if chosen in comfort_pending:
                    extra = followup.group('extra') if followup else None
                    if extra:
                        from intent_router.turn_router import TurnIntentRouter
                        parsed_extra = TurnIntentRouter().understand_turn(extra)
                        if (not parsed_extra.slot_updates or
                                any(update.slot == 'category' for update in parsed_extra.slot_updates) or
                                parsed_extra.decision_evidence.get('requirement_control') or
                                parse_control_intent(extra)):
                            extra = None
                            chosen = None
                    if chosen:
                        comfort_resolution = {'facet': chosen, **comfort_pending[chosen],
                                              'extra_message': extra}
            comparison_question = session.pending_comparison
            session.pending_comparison = None  # Only the immediate next turn can answer it.
            comparison_resolution = (resolve_comparison_reply(message, comparison_question)
                                     if direct_control is None or direct_control.action == 'detail' else None)
            if comparison_resolution:
                direct_control = None
            if direct_control is None and declines_comparison_question(message, comparison_question):
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('preference_compare_skip'))
            if comparison_resolution and comparison_resolution['extra_message']:
                from intent_router.turn_router import TurnIntentRouter
                extra_intent = TurnIntentRouter().understand_turn(comparison_resolution['extra_message'])
                extra_slots = {update.slot for update in extra_intent.slot_updates}
                if (not extra_slots or 'category' in extra_slots or
                        comparison_resolution['slot'] in extra_slots or
                        extra_intent.decision_evidence.get('requirement_control') or
                        parse_control_intent(comparison_resolution['extra_message'])):
                    comparison_resolution = None
            if direct_control is not None and direct_control.action == 'preferences':
                return self._control_turn(session_id, session, message, turn, direct_control)
            if (direct_control is not None and
                    direct_control.action in {'undo_rejection', 'redo_rejection'} and
                    session.pending_similarity):
                return self._control_turn(session_id, session, message, turn, direct_control)
            if (direct_control is not None and direct_control.action == 'detail' and
                    session.pending_similarity and len(direct_control.ranks) == 1 and
                    direct_control.ranks[0] in {int(product['rank']) for product in session.last_products}):
                return self._control_turn(session_id, session, message, turn, direct_control)
            similarity_pending = session.pending_similarity
            facet_reply = (parse_facet_reply(message, similarity_pending['facets'])
                           if similarity_pending else None)
            chosen_facets, extra_message = facet_reply if facet_reply else ((), None)
            similarity_resolution = ({**similarity_pending, 'chosen': chosen_facets, 'extra_message': extra_message}
                                     if similarity_pending and chosen_facets else None)
            repair = (repair_facet_reply(message, similarity_pending['facets'])
                      if similarity_pending and not similarity_resolution else None)
            similarity_exclusion = None
            if repair and repair[0] == 'excluded_facet' and repair[1] in {'fit', 'color'}:
                key = repair[1]
                similarity_exclusion = {
                    'parent_asin': similarity_pending['parent_asin'],
                    'source_rank': similarity_pending['rank'],
                    'facet': key,
                    'value': similarity_pending['facets'][key]['value'],
                    'evidence': similarity_pending['facets'][key]['evidence'],
                }
            elif repair:
                if similarity_pending.get('repair_attempts', 0) >= 1:
                    session.pending_similarity = None
                else:
                    kind, excluded = repair
                    remaining = {key: value for key, value in similarity_pending['facets'].items()
                                 if key != excluded}
                    if remaining:
                        session.pending_similarity = {
                            **similarity_pending, 'facets': remaining,
                            'repair_kind': kind, 'excluded_facet': excluded,
                            'repair_attempts': 1,
                        }
                    else:
                        session.pending_similarity = None
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('similar_clarification'))
            session.pending_similarity = None  # Any new turn resolves or abandons that question.
            compound = None if similarity_resolution or similarity_exclusion or comfort_decline_extra else plan_compound_turn(conversation_message)
            detail_answers: list[str] = []
            detail_contexts: list[dict[str, Any] | None] = []
            requirement_message = (f"avoid {similarity_exclusion['value']}, show me more"
                                   if similarity_exclusion else
                                   requirement_from_facets(similarity_resolution['facets'], chosen_facets, extra_message)
                                   if similarity_resolution else
                                   ', '.join(part for part in (
                                       f"I prefer {comparison_resolution['value']}",
                                       comparison_resolution['extra_message'],
                                       None if comparison_resolution.get('kind') == 'decision_tradeoff' else 'show me more') if part)
                                   if comparison_resolution else
                                   ', '.join(part for part in (
                                       f"I prefer {comfort_resolution['value']}",
                                       comfort_resolution['extra_message'], 'show me more') if part)
                                   if comfort_resolution else
                                   comfort_decline_extra if comfort_decline_extra else
                                   compound.requirement_message if compound and compound.requirement_message else conversation_message)
            compound_focus = session.focused_product_id
            if compound:
                for detail_control in compound.details:
                    targets = [p for p in session.last_products if
                               (p['parent_asin'] == compound_focus if detail_control.uses_focus
                                else p['rank'] in detail_control.ranks)]
                    if len(targets) == 1:
                        target = targets[0]
                        compound_focus = target['parent_asin']
                        source = self.agent.get_catalog_product(target['parent_asin']) or {}
                        answer = answer_product_question(source, target['rank'], detail_control.attribute, 'en')
                        list_label = 'previous' if compound.requirement_message else 'current'
                        detail_answers.append(f'About "{target["title"]}" in the {list_label} list: {answer}')
                        detail_contexts.append({'parent_asin': target['parent_asin'], 'rank': target['rank'],
                                                'attribute': detail_control.attribute})
                    else:
                        compound_focus = None
                        list_label = 'previous' if compound.requirement_message else 'current'
                        detail_answers.append(f'I could not identify that item in the {list_label} list. Please use a displayed rank.')
                        detail_contexts.append(None)
            if compound and compound.requirement_message is None:
                return self._control_turn(session_id, session, message, turn, ControlIntent('multi_detail'),
                                          detail_answers, detail_contexts, compound_focus)
            if compound and compound_focus:
                previous_result_key = tuple(product['parent_asin'] for product in session.last_products)
                session.focused_by_result_set[previous_result_key] = compound_focus
            planning_message = comfort_decline_extra or conversation_message
            rejection_similarity = (plan_rejection_and_similarity(planning_message)
                                    if compound is None and not similarity_resolution and not similarity_exclusion
                                    else None)
            if rejection_similarity:
                rejected, reference = rejection_similarity
                by_rank = {int(product['rank']): product for product in session.last_products}
                valid = (not reference.uses_focus and len(reference.ranks) == 1
                         and reference.ranks[0] in by_rank
                         and all(rank in by_rank for rank in rejected.ranks)
                         and all(by_rank[rank]['parent_asin'] !=
                                 by_rank[reference.ranks[0]]['parent_asin'] for rank in rejected.ranks))
                if not valid:
                    return self._control_turn(session_id, session, message, turn,
                                              ControlIntent('mixed_request'))
                return self._control_turn(session_id, session, message, turn,
                                          reference, shortlist_actions=(rejected,))
            similarity_cheaper = (plan_similarity_and_cheaper(planning_message)
                                  if compound is None and not similarity_resolution and not similarity_exclusion else None)
            if similarity_cheaper and not similarity_cheaper[2]:
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('mixed_request'))
            similarity_plan = (plan_similarity_and_requirements(planning_message)
                               if compound is None and not similarity_resolution and not similarity_exclusion
                               and not similarity_cheaper else None)
            similarity_mixed = None
            if similarity_plan or similarity_cheaper:
                reference, requirement_message = (similarity_plan if similarity_plan else
                                                  (similarity_cheaper[0], similarity_cheaper[1] or ''))
                by_rank = {int(product['rank']): product for product in session.last_products}
                targets = ([product for product in by_rank.values()
                            if product['parent_asin'] == session.focused_product_id]
                           if reference.uses_focus else
                           [by_rank[rank] for rank in reference.ranks if rank in by_rank])
                if len(targets) != 1:
                    return self._control_turn(session_id, session, message, turn, reference)
                anchor = targets[0]
                baseline_price = None
                cap = None
                requested_cap = (Decimal(str(similarity_cheaper[3]))
                                 if similarity_cheaper and similarity_cheaper[3] is not None else None)
                if similarity_cheaper:
                    baseline_price = _known_catalog_price(anchor)
                    cap = (baseline_price.quantize(Decimal('0.01'), rounding=ROUND_FLOOR)
                           if baseline_price is not None else None)
                    if cap is not None and cap == baseline_price:
                        cap -= Decimal('0.01')
                    if requested_cap is not None:
                        price_clause = re.compile(
                            r'(?:under|below|less than|at most|up to|maximum of|no more than)'
                            r'\s*\$?\s*\d+(?:\.\d+)?', re.I)
                        requirement_message = ', '.join(
                            part.strip() for part in re.split(r'\s+(?:and|but)\s+|,', requirement_message)
                            if part.strip() and not price_clause.fullmatch(part.strip()))
                    effective_cap = (min(valid_caps) if (valid_caps := [value for value in (cap, requested_cap)
                                                                          if value is not None and value >= 0])
                                     else None)
                    if effective_cap is None:
                        reason = 'cheaper_unpriced' if cap is None else 'cheaper_no_lower_price'
                        if not requirement_message:
                            return self._control_turn(
                                session_id, session, message, turn,
                                ControlIntent('similar', reference.ranks, reason, reference.uses_focus))
                        requirement_message += ', show me more'
                    else:
                        requirement_message = (f'under ${effective_cap:.2f}, '
                                               + requirement_message + ', show me more')
                from intent_router.turn_router import TurnIntentRouter
                parsed_requirement = TurnIntentRouter().understand_turn(requirement_message)
                updated_slots = {update.slot for update in parsed_requirement.slot_updates}
                source = self.agent.get_catalog_product(anchor['parent_asin']) or {}
                supported = {key: value for key, value in supported_facets(source).items()
                             if value['slot'] not in updated_slots}
                similarity_mixed = {'parent_asin': anchor['parent_asin'],
                                    'source_rank': int(anchor['rank']),
                                    'supported_facets': supported,
                                    'updated_slots': sorted(updated_slots),
                                    'requirements_message': requirement_message}
                if similarity_cheaper:
                    similarity_mixed['relative_price'] = {
                        'baseline': float(baseline_price) if baseline_price is not None else None,
                        'strict_cap': float(cap) if cap is not None and cap >= 0 else None,
                        'requested_cap': float(requested_cap) if requested_cap is not None else None,
                        'applied_cap': float(effective_cap) if effective_cap is not None else None,
                        'source': 'catalog_price' if baseline_price is not None else 'unpriced_catalog',
                    }
                old_key = tuple(product['parent_asin'] for product in session.last_products)
                session.focused_by_result_set[old_key] = anchor['parent_asin']
            shortlist_actions = (plan_shortlist_actions(planning_message)
                                 if compound is None and not similarity_resolution and not similarity_exclusion and not similarity_mixed else None)
            if shortlist_actions:
                session.pending_detail_attribute = None
                ranks = tuple(dict.fromkeys(rank for step in shortlist_actions for rank in step.ranks))
                return self._control_turn(session_id, session, message, turn,
                                          ControlIntent('multi_selection', ranks),
                                          shortlist_actions=shortlist_actions)
            selection_plan = (plan_selection_and_requirements(planning_message)
                              if compound is None and not similarity_resolution and not similarity_exclusion and not similarity_mixed else None)
            selection_target = None
            if selection_plan:
                selection_control, requirement_message = selection_plan
                from intent_router.turn_router import TurnIntentRouter
                parsed_requirement = TurnIntentRouter().understand_turn(requirement_message)
                safe_selection = (
                    selection_control.action in {'select', 'reject'}
                    and bool(selection_control.ranks)
                    and not any(update.slot == 'category' for update in parsed_requirement.slot_updates)
                    and not parsed_requirement.decision_evidence.get('requirement_control')
                    and (selection_control.action != 'reject' or
                         bool(parsed_requirement.slot_updates or parsed_requirement.decision_evidence.get('requested_results')))
                )
                if safe_selection:
                    by_rank = {int(product['rank']): product for product in session.last_products}
                    selection_target = [(rank, by_rank.get(rank)) for rank in selection_control.ranks]
                    if selection_control.action == 'select' and any(
                        product and product['parent_asin'] in session.rejected_asins
                        for _, product in selection_target
                    ):
                        safe_selection = False
                if not safe_selection:
                    session.pending_detail_attribute = None
                    return self._control_turn(session_id, session, message, turn, ControlIntent('mixed_request'))
            if compound is None and selection_plan is None and not similarity_exclusion and not similarity_mixed and has_unhandled_shortlist_mix(planning_message):
                session.pending_detail_attribute = None
                return self._control_turn(session_id, session, message, turn, ControlIntent('mixed_request'))
            control = None if compound or similarity_resolution or similarity_exclusion or comparison_resolution else (parse_detail_reference(planning_message, session.pending_detail_attribute) or parse_control_intent(planning_message))
            if selection_plan or similarity_mixed:
                control = None
            similarity_browse = None
            if control is not None and control.action == 'similar':
                by_rank = {int(product['rank']): product for product in session.last_products}
                targets = ([product for product in by_rank.values()
                            if product['parent_asin'] == session.focused_product_id]
                           if control.uses_focus else
                           [by_rank[rank] for rank in control.ranks if rank in by_rank])
                if len(targets) == 1:
                    anchor = targets[0]
                    source = self.agent.get_catalog_product(anchor['parent_asin']) or {}
                    supported = supported_facets(source)
                    if supported and not facets_not_in_preferences(supported, session.previous_state):
                        similarity_browse = {'parent_asin': anchor['parent_asin'],
                                             'source_rank': int(anchor['rank']),
                                             'already_known_facets': supported}
                        session.focused_product_id = anchor['parent_asin']
                        old_key = tuple(product['parent_asin'] for product in session.last_products)
                        session.focused_by_result_set[old_key] = anchor['parent_asin']
                        requirement_message = 'show me more'
                        control = None
            if control is not None:
                if control.action != 'detail':
                    session.pending_detail_attribute = None
                return self._control_turn(session_id, session, message, turn, control)
            session.pending_detail_attribute = None
            feedback_asins = tuple(dict.fromkeys(
                product['parent_asin'] for _, product in selection_target or ()
                if product and product['parent_asin'] not in session.rejected_asins
            )) if selection_plan and selection_control.action == 'reject' else ()
            if feedback_asins:
                response = self.agent.respond(session_id, requirement_message, turn, 10,
                                              product_feedback={'rejected_asins': feedback_asins})
            else:
                response = self.agent.respond(session_id, requirement_message, turn, 10)
            get_trace = getattr(self.agent, "get_trace", None)
            trace = (
                get_trace(session_id, turn)
                if callable(get_trace)
                else self.agent.trace[-1]  # Test doubles and legacy adapters.
            )
            if trace.get("session_id") != session_id or trace.get("turn") != turn:
                raise RuntimeError("Agent trace did not match the completed turn.")
            feedback_applied = bool(_event_map(trace).get('0_product_feedback')) if feedback_asins else False
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
            if term_request:
                receipt['term_question'] = {
                    'read_only_explanation': term_request[0],
                    'applied_request': term_request[1],
                }
            if receipt.get('search_reset_scope') == 'everything' or receipt.get('category_changed'):
                if pre_full_snapshot is None:
                    raise RuntimeError('Search transition was not captured before the agent turn.')
                session.reset_snapshots[turn] = pre_full_snapshot
            if receipt.get('pre_reason') == 'conversation_unsure_category':
                pending = self.agent.memory.snapshot(session_id).pending_question
                if pending and pending.get('target_slot') == 'category':
                    receipt['suggested_replies'] = [
                        pending.get('option_labels', {}).get(value, value)
                        for value in pending.get('options', ())]
            if comparison_resolution:
                receipt['preference_comparison_answer'] = {
                    **comparison_resolution,
                    'source': 'immediately preceding verified comparison question',
                }
            if comfort_resolution:
                receipt['comfort_followup_answer'] = {
                    **comfort_resolution,
                    'source': 'immediately preceding catalog-grounded comfort question',
                }
            if comfort_decline_extra:
                receipt['comfort_followup_decline'] = {
                    'source': 'immediately preceding catalog-grounded comfort question',
                    'additional_requirements': comfort_decline_extra,
                }
            if compound:
                receipt['compound_request'] = {'detail_context': detail_contexts[0] if len(detail_contexts) == 1 else None,
                                               'detail_contexts': detail_contexts,
                                               'requirements_message': requirement_message}
            if similarity_resolution:
                receipt['similarity_refinement'] = {
                    'parent_asin': similarity_resolution['parent_asin'],
                    'source_rank': similarity_resolution['rank'],
                    'chosen_facets': list(chosen_facets),
                    'source_evidence': {key: similarity_resolution['facets'][key] for key in chosen_facets},
                    'additional_requirements': extra_message,
                    'requirements_message': requirement_message,
                }
            if similarity_browse:
                receipt['similarity_browse'] = similarity_browse
            if similarity_exclusion:
                receipt['similarity_exclusion'] = {
                    **similarity_exclusion, 'requirements_message': requirement_message,
                }
            if similarity_mixed:
                receipt['similarity_mixed_request'] = similarity_mixed
            elif selection_plan:
                receipt['compound_request'] = {
                    'selection_context': [{'rank': rank, 'parent_asin': product['parent_asin'] if product else None}
                                          for rank, product in selection_target],
                    'requirements_message': requirement_message,
                }
            restored_result = _event_map(trace).get('5_restore_results')
            if restored_result:
                receipt['display_mode'] = 'restored_previous_results'
                receipt['restored_from_turn'] = restored_result['source_turn']
            if (receipt.get('pre_action') == 'acknowledge' and
                    (receipt.get('conversation_act') or receipt.get('pre_reason') in {
                        'conversation_focus_attention_limit', 'conversation_focus_already_covered'})):
                products = deepcopy(session.last_products)
                receipt['display_mode'] = 'retained_previous_results'
            elif (receipt.get('pre_reason') in {'confirm_requirement_change', 'choose_category_alternative'}
                    and _state_snapshot(receipt) == session.previous_state):
                products = deepcopy(session.last_products)
                receipt['display_mode'] = 'retained_previous_results'
            if receipt.get('conversation_act') in {'category_both', 'deferred_item_edit'}:
                pending = self.agent.memory.pending.get(session_id) or {}
                if pending.get('reason') == 'choose_category_alternative':
                    receipt['suggested_replies'] = [pending.get('option_labels', {}).get(value, value)
                                                    for value in pending.get('options', ())]
            if (receipt.get('requested_more') and not receipt.get('requirements_changed')
                    and not receipt.get('negative_feedback') and session.last_products and products
                    and not receipt.get('category_changed') and not receipt.get('search_reset')
                    and not session.rejection_future
                    and not any(product['parent_asin'] in
                                (session.rejected_asins | set(feedback_asins) |
                                 set(receipt.get('rejected_asins') or ()))
                                for product in session.last_products)
                    and all(product['parent_asin'] in
                            session.shown_by_intent.get(session.intent_version, set())
                            for product in products)):
                products = deepcopy(session.last_products)
                receipt['display_mode'] = 'retained_previous_results'
                receipt['more_exhausted'] = True
                retain_page = getattr(self.agent, 'retain_previous_result_page', None)
                if callable(retain_page):
                    receipt['retained_from_turn'] = retain_page(session_id, turn)
            reset_transition = receipt.get('reset_history_action')
            reset_snapshot = None
            if reset_transition:
                reset_snapshot = session.reset_snapshots.get(reset_transition['turn'])
                if reset_snapshot is None:
                    raise RuntimeError('Cannot restore a search transition without its session snapshot.')
                if reset_transition['action'] == 'undo':
                    products = deepcopy(reset_snapshot['last_products'])
                    receipt['display_mode'] = 'restored_previous_results'
                    receipt['restored_from_turn'] = reset_transition['turn'] - 1
                else:
                    products = []
                    receipt['display_mode'] = 'reset_results'
            was_finalized = bool(session.finalized_at_utc)
            category = str(receipt.get("hard", {}).get("category") or "").strip().casefold()
            intent_reset = bool(receipt.get("intent_changed") or receipt.get('search_reset') or
                                reset_transition and reset_transition['action'] == 'redo')
            if session.previous_category and category and category != session.previous_category:
                intent_reset = True
            if intent_reset:
                session.intent_version += 1
            selection_reset = bool(intent_reset and session.selections and
                                   receipt.get('search_reset_scope') != 'search_only')
            if selection_reset:
                session.selections.clear()
                session.comparison_cache_key = None
                session.comparison_cache = None
                session.finalized_at_utc = None
                session.finalized_turn = None
                session.finalized_asins = ()
            if intent_reset:
                if receipt.get('search_reset_scope') != 'search_only':
                    session.rejected_asins.clear()
                    session.rejection_history.clear()
                    session.rejection_future.clear()
            if category:
                session.previous_category = category
            elif receipt.get('search_reset') or reset_transition and reset_transition['action'] == 'redo':
                session.previous_category = None

            current_state = _state_snapshot(receipt)
            receipt["state_changes"] = _state_changes(session.previous_state, current_state)
            if receipt['state_changes'] or intent_reset:
                session.selection_history.clear()
                session.selection_future.clear()
            session.previous_state = current_state
            finalization_invalidated = bool(was_finalized and receipt["state_changes"])
            if finalization_invalidated:
                session.comparison_cache_key = None
                session.comparison_cache = None
                session.finalized_at_utc = None
                session.finalized_turn = None
                session.finalized_asins = ()

            selection_prefix = None
            if selection_plan:
                if intent_reset:
                    selection_prefix = ("I changed the search, but didn't keep an item from the previous list "
                                        "because the product category changed. Please select it separately if you still want it.")
                elif selection_control.action == 'reject':
                    rejected_ranks = [rank for rank, product in selection_target
                                      if product and product['parent_asin'] in feedback_asins]
                    unavailable = [rank for rank, product in selection_target if product is None]
                    if feedback_applied:
                        session.rejected_asins.update(feedback_asins)
                        session.rejection_history.append(feedback_asins)
                        del session.rejection_history[:-20]
                        session.rejection_future.clear()
                        for asin in feedback_asins:
                            session.selections.pop(asin, None)
                        session.selection_history.clear()
                        session.selection_future.clear()
                        session.comparison_cache_key = None
                        session.comparison_cache = None
                        session.finalized_at_utc = None
                        session.finalized_turn = None
                        session.finalized_asins = ()
                        receipt['rejected_asins'] = sorted(session.rejected_asins)
                        receipt['rejected_from_previous_list'] = rejected_ranks
                        receipt['can_undo_rejection'] = True
                        receipt['can_redo_rejection'] = False
                        selection_prefix = f"I'll leave {', '.join(f'#{rank}' for rank in rejected_ranks)} out of future results."
                    elif feedback_asins:
                        selection_prefix = "I couldn't apply that product feedback, so I haven't excluded the item."
                    if unavailable:
                        missing = f"I couldn't find {', '.join(f'#{rank}' for rank in unavailable)} in the previous list."
                        selection_prefix = f'{selection_prefix} {missing}'.strip() if selection_prefix else missing
                else:
                    previous_selection = deepcopy(session.selections)
                    added, already, unavailable, capped = [], [], [], []
                    for rank, product in selection_target:
                        if product is None:
                            unavailable.append(rank)
                        elif product['parent_asin'] in session.selections:
                            already.append(rank)
                        elif len(session.selections) >= MAX_SELECTIONS:
                            capped.append(rank)
                        else:
                            session.selections[product['parent_asin']] = {
                                'selected_at_turn': turn, 'intent_version': session.intent_version,
                                'reason': f'Selected from previous list at rank {rank} during search refinement',
                            }
                            added.append(rank)
                    if added:
                        self._remember_selection(session, previous_selection)
                        session.comparison_cache_key = None
                        session.comparison_cache = None
                        session.finalized_at_utc = None
                        session.finalized_turn = None
                        session.finalized_asins = ()
                    receipt['selection_count'] = len(session.selections)
                    receipt['selection_added_from_previous_list'] = added
                    statements = []
                    if added:
                        statements.append(f"Kept {', '.join(f'#{rank}' for rank in added)} from the previous list.")
                    if already:
                        statements.append(f"{', '.join(f'#{rank}' for rank in already)} was already in your shortlist.")
                    if unavailable:
                        statements.append(f"I couldn't find {', '.join(f'#{rank}' for rank in unavailable)} in the previous list.")
                    if capped:
                        statements.append(f"The shortlist is full, so I couldn't add {', '.join(f'#{rank}' for rank in capped)}.")
                    selection_prefix = ' '.join(statements)

            if reset_transition and reset_transition['action'] == 'undo':
                for name in ('selections', 'selection_history', 'selection_future',
                             'rejected_asins', 'rejection_history', 'rejection_future',
                             'focused_product_id', 'focused_by_result_set',
                             'finalized_at_utc', 'finalized_turn', 'finalized_asins'):
                    setattr(session, name, deepcopy(reset_snapshot[name]))
                session.comparison_cache_key = None
                session.comparison_cache = None
                receipt['rejected_asins'] = sorted(session.rejected_asins)
                receipt['selection_count'] = len(session.selections)
                receipt['can_undo_rejection'] = bool(session.rejection_history)
                receipt['can_redo_rejection'] = bool(session.rejection_future)
            elif reset_transition and reset_transition['action'] == 'redo':
                receipt['rejected_asins'] = []
                receipt['selection_count'] = 0
                receipt['can_undo_rejection'] = False
                receipt['can_redo_rejection'] = False

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
                product["shopper_notes"] = describe(product, catalog_product)
            receipt["result_quality"] = summarize_explanations(
                product["match"] for product in products
            )
            session.last_products = deepcopy(products)
            if compound:
                session.focused_product_id = compound_focus
            if session.focused_product_id not in {p['parent_asin'] for p in products}:
                session.focused_product_id = None
            result_key = tuple(product['parent_asin'] for product in products)
            if restored_result:
                session.focused_product_id = session.focused_by_result_set.get(result_key)
            if session.focused_product_id:
                session.focused_by_result_set[result_key] = session.focused_product_id
                while len(session.focused_by_result_set) > 16:
                    session.focused_by_result_set.pop(next(iter(session.focused_by_result_set)))
            receipt['focused_product_id'] = session.focused_product_id
            receipt['can_undo_rejection'] = bool(session.rejection_history)
            receipt['can_redo_rejection'] = bool(session.rejection_future)
            if similarity_mixed and products:
                remaining_facets = facets_not_in_preferences(
                    similarity_mixed['supported_facets'], session.previous_state)
                if remaining_facets:
                    session.pending_similarity = {
                        'parent_asin': similarity_mixed['parent_asin'],
                        'rank': similarity_mixed['source_rank'],
                        'facets': remaining_facets,
                    }
                    receipt['question'] = {
                        'target_slot': 'similarity_attribute', 'options': list(remaining_facets),
                        'evidence': {'parent_asin': similarity_mixed['parent_asin'],
                                     'facets': remaining_facets},
                    }
                    receipt['similarity_reference'] = {
                        'parent_asin': similarity_mixed['parent_asin'],
                        'rank': similarity_mixed['source_rank'],
                        'facets': remaining_facets,
                    }

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
            if receipt.get('search_reset'):
                if receipt.get('search_reset_scope') == 'everything':
                    prefix = ("I cleared this session's search, shortlist, and hidden-product choices. "
                              "You can undo that reset. ")
                else:
                    prefix = ("I cleared this session's search requirements and results. "
                              "You can undo that reset. " if receipt.get('state_changes') else
                              "Your search requirements are already clear. ")
                if receipt.get('search_reset_scope') == 'search_only':
                    prefix += "Your saved and hidden products are still kept. "
                assistant['message'] = prefix + assistant['message']
            if reset_transition:
                if reset_transition.get('kind') == 'category_switch' and reset_transition['action'] == 'undo':
                    assistant['message'] = (
                        "I brought back your earlier search, shortlist, and hidden-product choices. "
                        "We can keep comparing those options."
                    )
                elif reset_transition.get('kind') == 'category_switch':
                    assistant['message'] = (
                        "I switched back to the newer product search. "
                        "Your earlier shortlist and hidden-product choices are still available through Undo. "
                        + assistant['message']
                    )
                elif reset_transition['action'] == 'undo':
                    assistant['message'] = (
                        "I restored your earlier search, shortlist, and hidden-product choices. "
                        "You can pick up where you left off."
                    )
                else:
                    assistant['message'] = (
                        "I cleared the search, shortlist, and hidden-product choices again. "
                        + assistant['message']
                    )
            if session.locale == 'zh' and products and not response.get('ask_attribute') and not receipt.get('conversation_act'):
                assistant['message'] = build_shopping_guide(products)['intro']
            if products and not response.get('ask_attribute') and not receipt.get('conversation_act'):
                tradeoff = preference_tradeoff(products, session.locale)
                if tradeoff:
                    assistant['message'] += ' ' + tradeoff
            if session.locale == 'zh' and receipt.get('requirement_undo'):
                prefix = '已撤销刚才的需求修改。' if receipt['requirement_undo'] == 'applied' else '还没有可以撤销的需求修改。'
                if receipt.get('display_mode') == 'restored_previous_results':
                    assistant['message'] = f'之前的 {len(products)} 款已按原顺序恢复，可以接着比较。'
                assistant['message'] = prefix + assistant['message']
            if session.locale == 'zh' and receipt.get('requirement_redo'):
                prefix = '已恢复刚才撤销的需求修改。' if receipt['requirement_redo'] == 'applied' else '目前没有可以重做的需求修改。'
                if receipt.get('display_mode') == 'restored_previous_results':
                    assistant['message'] = f'之前的 {len(products)} 款已按原顺序恢复，可以接着比较。'
                assistant['message'] = prefix + assistant['message']
            if detail_answers:
                assistant['message'] = '\n\n'.join(detail_answers) + '\n\n' + assistant['message']
            if comparison_resolution:
                if comparison_resolution.get('kind') == 'decision_tradeoff':
                    assistant['message'], receipt['decision_followup'] = decision_followup(
                        products, comparison_resolution, self.agent.get_catalog_product)
                else:
                    assistant['message'] = (
                        f"Got it — I'll favor listings that explicitly support "
                        f"{comparison_resolution['value']}. You can change that preference later.\n\n"
                        + assistant['message']
                    )
            if similarity_resolution:
                labels = ', '.join(similarity_resolution['facets'][key]['value'] for key in chosen_facets)
                assistant['message'] = (f"Got it—{labels} is what you want to carry over from "
                                        f"#{similarity_resolution['rank']}. I'll treat that as a preference "
                                        "and check which listings actually support it.\n\n" + assistant['message'])
            if similarity_exclusion:
                assistant['message'] = (
                    f"Got it—I’ll avoid listings explicitly marked {similarity_exclusion['value']} "
                    f"like #{similarity_exclusion['source_rank']}. "
                    "Listings without a clear fit or color label may still appear. You can undo this preference.\n\n"
                    + assistant['message']
                )
            if similarity_browse:
                if receipt['new_product_count']:
                    preface = (f"You already told me the verified details of #{similarity_browse['source_rank']}, "
                               f"so I kept those preferences and found {receipt['new_product_count']} "
                               "more catalog options. These are not guaranteed to be identical to that item.")
                else:
                    preface = (f"You already told me the verified details of #{similarity_browse['source_rank']}. "
                               "I couldn't find a new verified option under those preferences, "
                               "so these are the closest catalog results I can show for now.")
                assistant['message'] = preface + '\n\n' + assistant['message']
            if similarity_mixed:
                price_context = similarity_mixed.get('relative_price')
                source_rank = similarity_mixed['source_rank']
                if price_context and price_context['strict_cap'] is not None:
                    price_note = (f"#{source_rank} is listed at ${price_context['baseline']:g}, "
                                  f"so I kept the search under ${price_context['applied_cap']:.2f}" +
                                  ("—your own limit is the tighter one"
                                   if price_context['requested_cap'] is not None
                                   and price_context['requested_cap'] < price_context['strict_cap'] else "") +
                                  ". Those are catalog prices, not live offers. ")
                elif price_context and price_context['baseline'] is None:
                    price_note = (f"#{source_rank} has no catalog price, so I can't "
                                  "verify cheaper options. " +
                                  (f"I kept your ${price_context['requested_cap']:g} cap, but this catalog "
                                   "cannot verify unpriced items against it. "
                                   if price_context['requested_cap'] is not None else
                                   "I applied your other detail without inventing a price limit. "))
                elif price_context:
                    price_note = (f"#{source_rank}'s catalog price leaves no lower "
                                  "nonnegative cent amount to search. I applied your other detail. ")
                else:
                    price_note = ''
                changed_color = (receipt.get('hard', {}).get('color')
                                 if 'color' in similarity_mixed['updated_slots'] else None)
                color_label = f" {changed_color}" if isinstance(changed_color, str) else ''
                if products and session.pending_similarity:
                    choices = ', '.join(session.pending_similarity['facets'])
                    assistant['message'] = (
                        price_note + f"I found {len(products)}{color_label} options. "
                        f"What else did you like about #{source_rank}—its {choices}? "
                        "Pick one or more, or keep browsing."
                    )
                elif products:
                    assistant['message'] = (
                        price_note + f"I found {len(products)}{color_label} options. "
                        f"I haven't assumed which other part of #{source_rank} you liked. "
                        "If a particular detail matters, tell me; we can also compare these as they are."
                    )
                elif (price_context and price_context['baseline'] is None
                      and price_context['requested_cap'] is not None
                      and (receipt.get('question') or {}).get('target_slot') == 'budget'):
                    assistant['message'] = (
                        f"#{source_rank} has no catalog price, so I can't verify what's cheaper. "
                        f"I kept your ${price_context['requested_cap']:g} limit"
                        + (f" and {changed_color}" if isinstance(changed_color, str) else "") +
                        ", but these listings are unpriced too; none can be verified under your limit. "
                        "Would you like to see unpriced ideas while keeping that price preference, "
                        "or keep the strict limit?"
                    )
                elif price_note:
                    assistant['message'] = price_note + assistant['message']
            if not selection_plan and not similarity_mixed:
                correction_note = _correction_acknowledgement(message, receipt)
                if correction_note:
                    assistant['message'] = correction_note + '\n\n' + assistant['message']
            priority_shift = receipt.get('preference_priority_shift') or {}
            prior_slot = priority_shift.get('from')
            focus_label = {'style': 'fit', 'material': 'fabric', 'use_case': 'intended use'}.get(
                priority_shift.get('to'), priority_shift.get('to', 'that detail'))
            if prior_slot in (receipt.get('hard') or {}):
                prior_value = receipt['hard'][prior_slot]
                if isinstance(prior_value, (list, tuple)):
                    prior_value = ' or '.join(str(value) for value in prior_value)
                assistant['message'] = (
                    f"I kept {prior_value} as a must-have. If you meant it to be optional while we "
                    f"focus on {focus_label}, say '{prior_value} is optional.'\n\n"
                    + assistant['message']
                )
            elif prior_slot in (receipt.get('soft') or {}):
                prior_value = ' or '.join(str(value) for value in receipt['soft'][prior_slot])
                assistant['message'] = (
                    f"I'll focus on {focus_label} and keep {prior_value} as a secondary preference. "
                    "You can change that priority or undo it.\n\n" + assistant['message']
                )
            if selection_prefix:
                assistant['message'] = selection_prefix + '\n\n' + assistant['message']
            if term_request:
                assistant['message'] = term_request[0] + '\n\n' + assistant['message']
            self._prune_reset_snapshots(session_id, session)
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
            "remaining_turns": None if getattr(self.agent, 'max_turns', 10) is None else max(0, getattr(self.agent, 'max_turns', 10) + 1 - session.next_turn),
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

"""State-aware adapter over module 2's real indexes.

Strict mode filters and truncates a variable pool. The submitted recall-compatible
mode preserves module 2's deterministic Top-50 candidate contract.
"""
from dataclasses import asdict, dataclass, replace
import math
import re

from techjam_agent.contracts import Candidate, PRODUCT_FIELDS, Requirements
from techjam_agent.contracts_v2 import RetrievalResultV2, RetrievalStats
from techjam_agent.query import tokenize, parse_text
from techjam_agent.retrieval import LiteTop50CandidateGenerator, build_retrieval_plan, _text
from state_memory.contracts import StateSnapshotV2


def sequence(value):
    return value if isinstance(value, (list, tuple)) else (value,)


def _singular(word):
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") and len(word) > 3 else word


def terms(value):
    # Small lexical normalization, not a semantic or attribute classifier.
    return {_singular(t) for t in tokenize(_text(value))}


CATEGORY_TAXONOMY_LABELS = {
    "dress": {"dress", "gown"},
    "shirt": {"shirt", "dress shirt", "casual button down shirt", "blouse"},
    "t shirt": {"t shirt", "tshirt", "tee"},
    "jersey": {"jersey"},
    "jacket": {"jacket", "coat", "hoodie", "blazer"},
    "pant": {"pant", "jean", "legging", "trouser"},
    "short": {"short"},
    "skirt": {"skirt"},
    "shoe": {"shoe", "sneaker", "boot", "sandal", "heel", "loafer"},
    "earring": {"earring", "hoop"},
    "necklace": {"necklace", "pendant"},
    "ring": {"ring"},
    "bracelet": {"bracelet", "bangle"},
    "bag": {"bag", "handbag", "backpack", "purse"},
}


def _normalized_label(value):
    if re.fullmatch(r't[ -]?shirts?', str(value).strip(), re.I):
        return 't shirt'
    return " ".join(_singular(token) for token in tokenize(_text(value)))


def category_matches(product, value):
    """Return taxonomy-first category evidence without substring collisions."""
    raw_categories = product.get("categories") or ()
    if isinstance(raw_categories, str):
        raw_categories = (raw_categories,)
    labels = [(_normalized_label(label), str(label)) for label in raw_categories if str(label).strip()]
    requested = _normalized_label(value)
    accepted = CATEGORY_TAXONOMY_LABELS.get(requested)
    if accepted is not None and labels:
        for normalized, original in labels:
            # Dress and shorts have common modifier collisions ("dress shirt",
            # "short sleeve"), so require an exact taxonomy node for them.
            # Other families may legitimately appear in compound nodes such as
            # "Handbags & Wallets" or "Coats, Jackets & Vests".
            exact_only = requested in {"dress", "short"}
            if normalized in accepted or (
                not exact_only
                and any(terms(alias) and terms(alias) <= terms(normalized) for alias in accepted)
            ):
                return True, f"catalog taxonomy: {original}"
        return False, "requested category absent from catalog taxonomy"
    category_terms = terms(raw_categories)
    if category_terms:
        required = terms(value)
        specific = required - {"clothing", "shoe", "jewelry"}
        return (specific or required) <= category_terms, "catalog taxonomy"
    required = terms(value)
    title_terms = terms(product.get("title"))
    if requested == 't shirt':
        matched = bool(re.search(r'(?<!\w)(?:t[ -]?shirts?|tees?)(?!\w)', str(product.get('title') or ''), re.I))
    else:
        matched = any(terms(alias) <= title_terms for alias in accepted) if accepted else bool(required) and required <= title_terms
    return matched, "title fallback (taxonomy unavailable)"


def requirements_from_state(state, *, relax_soft=False):
    hard = state.hard_constraints
    return Requirements(
        category=str(hard.get("category", "")),
        hard_constraints=tuple(str(v) for key, value in hard.items() if key not in {"category", "price_min", "price_max"} for v in sequence(value)),
        soft_preferences=() if relax_soft else tuple(str(v) for prefs in state.soft_preferences.values() for p in prefs for v in sequence(p.value)),
    )


@dataclass(frozen=True)
class RetrievalRequest:
    state: StateSnapshotV2
    strategy: str
    route_weights: dict
    candidate_limit: int = 50
    route_depth: int = 300
    relax_soft: bool = False
    attempt: int = 1

    def __post_init__(self):
        if not isinstance(self.state, StateSnapshotV2):
            raise ValueError("Retrieval requires a versioned full-state snapshot")
        if type(self.candidate_limit) is not int or not 1 <= self.candidate_limit <= 50:
            raise ValueError("candidate_limit must be between 1 and 50")
        if type(self.route_depth) is not int or self.route_depth < 1 or self.attempt not in (1, 2):
            raise ValueError("invalid retrieval depth or retry budget")
        if self.strategy not in {"buying", "browsing", "balanced"}:
            raise ValueError("unsupported strategy")
        if any(not math.isfinite(w) or w < 0 for w in self.route_weights.values()):
            raise ValueError("weights must be finite and nonnegative")

    @classmethod
    def from_state(cls, state):
        buying = state.intent == "buying"
        browsing = state.intent == "browsing"
        return cls(state, "buying" if buying else "browsing" if browsing else "balanced",
                   {"color_normalized_full_and": 3.0 if buying else 1.0,
                    "category_gate": 2.0, "category_or": 2.0 if browsing else 0.5,
                    "evidence": 3.0, "dense": 2.0 if browsing else 1.0})

    def retry(self):
        return replace(self, relax_soft=True, route_depth=self.route_depth * 3, attempt=self.attempt + 1)

    def to_dict(self):
        return {**asdict(self), "state": self.state.to_dict()}


class StateAwareRetriever:
    def __init__(self, catalog_path, *, backend=None, mode="strict"):
        if mode not in {"strict", "recall_compat"}:
            raise ValueError("retrieval mode must be strict or recall_compat")
        self.mode = mode
        self.backend = backend or LiteTop50CandidateGenerator(catalog_path)
        self.products = self.backend.fts.products

    def satisfies(self, asin, state):
        if not state.hard_constraints.get("category"):
            return False
        product = self.products[asin]
        product_terms = terms(" ".join(_text(product.get(f)) for f in PRODUCT_FIELDS))
        # Taxonomy is stronger category evidence than title text: "jersey"
        # can describe a fabric in pajama titles without making the item a
        # sports jersey. Fall back to title only for catalog rows with no
        # category evidence at all.
        for name, value in state.hard_constraints.items():
            if name in {"price_min", "price_max"}:
                try:
                    price = float(str(product.get("price", "")).replace("$", "").replace(",", ""))
                except (TypeError, ValueError):
                    return False  # Unknown price cannot certify a hard budget.
                if not math.isfinite(price) or (name == "price_min" and price < float(value)) or (name == "price_max" and price > float(value)):
                    return False
                continue
            # Alternatives within a slot are OR; different slots are AND.
            def matches(v):
                required = terms(v)
                if name == "category":
                    return category_matches(product, v)[0]
                if name == "brand":
                    brand_terms = terms(product.get("store")) | terms(product.get("details"))
                    return bool(required) and required <= brand_terms
                if name.startswith("feature_"):
                    # Strip metadata labels with the same module-2 query parser.
                    required = terms(" ".join(parse_text(str(v)).retrieval_terms))
                return bool(required) and required <= product_terms
            if not any(matches(v) for v in sequence(value)):
                return False
        for name, values in state.exclusions.items():
            if any(terms(v) and terms(v) <= product_terms for v in values):
                return False
        return True

    def generate(self, request):
        state = request.state
        requirements = requirements_from_state(state, relax_soft=request.relax_soft)
        if self.mode == "recall_compat":
            legacy = self.backend.generate(requirements, session_id=state.session_id, turn=state.turn)
            result = RetrievalResultV2.from_legacy(
                legacy, state_version=state.state_version, state_snapshot=state.to_dict()
            )
            blocked = set(state.rejected_asins)
            if not blocked:
                return result
            kept = [candidate for candidate in result.candidates if candidate.parent_asin not in blocked]
            candidates = tuple(
                Candidate(row.parent_asin, rank, row.source_ranks, row.product)
                for rank, row in enumerate(kept, 1)
            )
            return RetrievalResultV2(
                candidate_set_id=result.candidate_set_id,
                session_id=result.session_id,
                turn=result.turn,
                state_version=result.state_version,
                candidate_limit=result.candidate_limit,
                candidates=candidates,
                stats=result.stats,
                state_snapshot=result.state_snapshot,
                legacy_requirements=result.legacy_requirements,
                warnings=(*result.warnings, f"Explicit feedback excluded {len(result.candidates) - len(candidates)} product(s)."),
            )
        plan = build_retrieval_plan(requirements)
        routes = {name: self.backend.fts.rank(expression, depth=request.route_depth)
                  for name, expression in plan["expressions"].items()}
        routes["evidence"] = self.backend.evidence.rank(requirements, limit=request.route_depth)
        warnings = ["Hard textual constraints use lexical evidence; unknown hard prices are rejected."]
        if hasattr(self.backend, "_dense_ranking"):
            try:
                routes["dense"] = self.backend._dense_ranking(plan["full_query"], depth=min(request.route_depth, len(self.products)))
            except Exception as exc:
                warnings.append(f"Dense failed; lexical routes retained: {type(exc).__name__}")
        else:
            warnings.append("CPU lexical mode: Dense inference is disabled.")
        provenance = {}
        for name, ids in routes.items():
            for rank, asin in enumerate(ids, 1):
                provenance.setdefault(asin, {})[name] = rank
        blocked = set(state.rejected_asins)
        eligible = [asin for asin in provenance if asin not in blocked and self.satisfies(asin, state)]
        hard_filtered_count = len(eligible)
        if blocked:
            warnings.append(f"Explicit feedback excluded {len(set(provenance) & blocked)} product(s).")
        seen = set(state.shown_asins)
        if seen and eligible:
            unseen = [asin for asin in eligible if asin not in seen]
            if unseen:
                eligible = unseen
                warnings.append(
                    f"Novelty filter excluded {hard_filtered_count - len(unseen)} shown product(s); "
                    f"{len(unseen)} unseen eligible product(s) remain."
                )
            else:
                warnings.append(
                    "Novelty pool exhausted; reusing shown products instead of returning an empty result."
                )
        def score(asin):
            return sum(request.route_weights.get(name, 1.0) / (60 + rank) for name, rank in provenance[asin].items())
        eligible.sort(key=lambda asin: (-score(asin), asin))
        candidates = tuple(Candidate(asin, rank, provenance[asin], {f: self.products[asin].get(f) for f in PRODUCT_FIELDS})
                           for rank, asin in enumerate(eligible[:request.candidate_limit], 1))
        return RetrievalResultV2(
            candidate_set_id=f"{state.session_id}:{state.turn}:v{state.state_version}:a{request.attempt}",
            session_id=state.session_id, turn=state.turn, state_version=state.state_version,
            candidate_limit=request.candidate_limit, candidates=candidates,
            stats=RetrievalStats(len(provenance), len(eligible)),
            state_snapshot=state.to_dict(), legacy_requirements=requirements, warnings=tuple(warnings))

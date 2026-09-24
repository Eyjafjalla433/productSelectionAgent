"""Qwen3-Reranker-0.6B sequence-classification adapter.

The competition catalog and dialogue are text only, so this component uses a
text reranker rather than the visual-language Qwen3-VL-Reranker-2B. The default
checkpoint is a sequence-classification conversion of
``Qwen/Qwen3-Reranker-0.6B`` so it can be loaded with the ordinary
``AutoModelForSequenceClassification`` API and trained with a standard binary
relevance head.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from techjam_agent.contracts import Candidate, Requirements
from ranking_pipeline.context import compact_profile, compact_requirements
from ranking_pipeline.prompt import LLMRankResult


DEFAULT_MODEL = "tomaarsen/Qwen3-Reranker-0.6B-seq-cls"
DEFAULT_INSTRUCTION = (
    "Rank products by how well they satisfy the user's requirements and preferences."
)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def product_text(candidate: Candidate, *, max_chars: int = 700) -> str:
    parts: list[str] = []
    for field in ("title", "categories", "features", "details", "store", "description"):
        value = candidate.product.get(field)
        if value not in (None, "", [], {}):
            parts.append(_text(value))
    text = re.sub(r"\s+", " ", " | ".join(parts)).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "..."
    return text or str(candidate.parent_asin)


def query_text(
    requirements: Requirements,
    *,
    user_profile: Mapping[str, Any] | None = None,
) -> str:
    profile = compact_profile(user_profile)
    return (
        f"{compact_requirements(requirements)}; "
        f"profile: {profile or 'none'}"
    )


def format_pair(
    query: str,
    document: str,
    *,
    instruction: str = DEFAULT_INSTRUCTION,
) -> str:
    """Render the seq-cls checkpoint's expected instruction/delimiters."""

    prefix = (
        '<|im_start|>system\n'
        'Judge whether the Document meets the requirements based on the Query '
        'and the Instruct provided. Note that the answer can only be "yes" or "no".'
        '<|im_end|>\n<|im_start|>user\n'
    )
    suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    return (
        f"{prefix}<Instruct>: {instruction}\n"
        f"<Query>: {query}\n"
        f"<Document>: {document}{suffix}"
    )


class Qwen3Reranker:
    """Lazy, offline-first pointwise scorer using Qwen3-Reranker-0.6B."""

    def __init__(
        self,
        model_name_or_path: str | None = None,
        *,
        device: str | None = None,
        max_length: int = 1024,
        batch_size: int = 8,
        clarification_threshold: float = 0.45,
    ) -> None:
        self.model_name_or_path = (
            model_name_or_path
            or os.environ.get("TECHJAM_RERANKER_MODEL")
            or DEFAULT_MODEL
        )
        self.device = device or os.environ.get("TECHJAM_RERANKER_DEVICE")
        self.max_length = max_length
        self.batch_size = batch_size
        self.clarification_threshold = clarification_threshold
        self._model: Any | None = None
        self._tokenizer: Any | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    def load(self) -> None:
        if self.loaded:
            return
        import torch
        from peft import PeftModel
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        model_path = Path(self.model_name_or_path)
        adapter_config_path = model_path / "adapter_config.json"
        is_adapter = model_path.is_dir() and adapter_config_path.exists()
        if is_adapter:
            adapter_config = json.loads(adapter_config_path.read_text(encoding="utf-8"))
            base_model_name = adapter_config.get("base_model_name_or_path") or DEFAULT_MODEL
        else:
            base_model_name = self.model_name_or_path

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_name_or_path,
            padding_side="left",
        )
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token
        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        model_kwargs: dict[str, Any] = {}
        if self.device == "cuda":
            model_kwargs["torch_dtype"] = torch.float16
        base_model = AutoModelForSequenceClassification.from_pretrained(
            base_model_name,
            **model_kwargs,
        )
        if is_adapter:
            self._model = PeftModel.from_pretrained(base_model, str(model_path))
        else:
            self._model = base_model
        self._model.to(self.device)
        self._model.eval()

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """Return sigmoid relevance scores for formatted query/document pairs."""

        self.load()
        import torch

        assert self._model is not None and self._tokenizer is not None
        scores: list[float] = []
        for start in range(0, len(pairs), self.batch_size):
            batch = [format_pair(query, document) for query, document in pairs[start : start + self.batch_size]]
            inputs = self._tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation="longest_first",
                max_length=self.max_length,
            )
            inputs = {name: value.to(self.device) for name, value in inputs.items()}
            with torch.inference_mode():
                logits = self._model(**inputs).logits
            batch_scores = torch.sigmoid(logits).squeeze(-1).float().tolist()
            if isinstance(batch_scores, float):
                batch_scores = [batch_scores]
            scores.extend(batch_scores)
        return scores

    def score_candidates(
        self,
        requirements: Requirements,
        candidates: Sequence[Candidate],
        *,
        user_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        """Return pointwise relevance scores for every candidate."""

        if not candidates:
            return {}
        query = query_text(requirements, user_profile=user_profile)
        pairs = [(query, product_text(candidate)) for candidate in candidates]
        scores = self.score_pairs(pairs)
        return {
            candidate.parent_asin: float(score)
            for candidate, score in zip(candidates, scores)
        }

    def rank_candidates(
        self,
        requirements: Requirements,
        candidates: Sequence[Candidate],
        *,
        user_profile: Mapping[str, Any] | None = None,
        top_k: int,
    ) -> LLMRankResult:
        """Score the provided candidates and return a strict JSON-safe result."""

        if not candidates:
            raise ValueError("candidates must not be empty")
        score_by_id = self.score_candidates(
            requirements,
            candidates,
            user_profile=user_profile,
        )
        scores = [score_by_id[candidate.parent_asin] for candidate in candidates]
        candidate_by_id = {candidate.parent_asin: candidate for candidate in candidates}
        allowed = {candidate.parent_asin for candidate in candidates}
        ordered = sorted(
            enumerate(candidates),
            key=lambda item: (-scores[item[0]], item[1].parent_asin),
        )
        ranked_ids: list[str] = []
        for index, candidate in ordered:
            if candidate.parent_asin not in allowed or candidate.parent_asin in ranked_ids:
                continue
            ranked_ids.append(candidate.parent_asin)
            if len(ranked_ids) >= top_k:
                break
        if not ranked_ids:
            raise ValueError("no valid candidates were scored")
        top_score = max((score_by_id[candidate_id] for candidate_id in ranked_ids), default=0.0)
        confidence = max(0.0, min(1.0, float(top_score)))
        ranked_scores = tuple(score_by_id[candidate_id] for candidate_id in ranked_ids)
        ranked_reasons = tuple(
            f"relevance {ranked_scores[rank_index]:.3f}; {product_text(candidate_by_id[candidate_id], max_chars=90)}"
            for rank_index, candidate_id in enumerate(ranked_ids)
        )
        return LLMRankResult(
            ranked_ids=tuple(ranked_ids),
            constraint_conflicts=(),
            confidence=confidence,
            need_clarification=confidence < self.clarification_threshold,
            scores=ranked_scores,
            reasons=ranked_reasons,
        )


__all__ = ["Qwen3Reranker", "query_text", "format_pair", "product_text"]

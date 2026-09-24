"""Test package bootstrap for the sibling ranking_pipeline package."""

from __future__ import annotations

import sys
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_RETRIEVAL_ROOT = _REPOSITORY_ROOT / "retrieval-and-reranking"
for path in (_REPOSITORY_ROOT, _RETRIEVAL_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

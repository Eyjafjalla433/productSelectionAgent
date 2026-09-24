"""Integrated 1 -> 3 -> 4A -> 2 -> 4B agent (legacy entry points unchanged)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for relative in ("retrieval-and-reranking", "intent-recognition", "conversation-state-memory/src"):
    path = str(ROOT / relative)
    if path not in sys.path:
        sys.path.insert(0, path)

from .agent import FinalAgent

# A legacy sibling also contains agent.py. Keep the official root entry first
# after sibling packages have installed their import paths.
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

__all__ = ["FinalAgent"]

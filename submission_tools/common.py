from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "competition_kit"
ARCHIVE_URL = "https://github.com/TechJam2026/techjam-conversational-search/releases/download/participant-kit/techjam-participant-kit.zip"
ARCHIVE_SHA256 = "b3d7e283b835343b42c4919ea2ca90f2fb5a2aa2b10537f14dcf42f03e5b38ae"
OFFICIAL_HASHES = {
    "data/catalog.jsonl": "da979b05a68af864cb0dcf9ee6a81c010c7e66a57978ad286c7a2e005fc69a67",
    "data/public_set.jsonl": "857259f7a438e6188ac63e18995b6ff4489bfcfc4a716a798b9a2aa0ee8f7579",
    "evaluator/local_evaluator.py": "79a5ea06f9a1b8c5036f30efa85dc1f36b8f6b06eb8feb8f545dfa767bc45564",
    "evaluator/__init__.py": "c597e982409b24fe5411298cfe033aeb287eafcf26c33e34b8c43294cff0a917",
    "starter/agent.py": "cd0fdade2d743aaf220b93a6cd3bfa7fb1b9b9065d2fbd174128ed2b0f1b812d",
    "starter/__init__.py": "03c49004df458e7fb767d172cc896fb5dd08a2aa00686d322248befdc2d7f5d4",
    "docs/agent_api_contract.json": "635563741dd71c273d540722913eccdc595b4af9b47ade79f38cd42ae45c8822",
}
CODE_FOLDERS = (
    "shopping_agent", "ranking_pipeline", "intent-recognition/intent_router",
    "conversation-state-memory/src/state_memory", "retrieval-and-reranking/techjam_agent",
    "submission_tools",
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_hashes():
    paths = [ROOT / "agent.py"] + [p for folder in CODE_FOLDERS for p in (ROOT / folder).glob("*.py")]
    return {str(p.relative_to(ROOT)): sha256(p) for p in sorted(paths)}


def check_kit(kit=KIT):
    for relative, expected in OFFICIAL_HASHES.items():
        path = kit / relative
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"Missing or modified official artifact: {relative}; run prepare_data")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

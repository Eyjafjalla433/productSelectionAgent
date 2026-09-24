"""Build a deterministic source-only ZIP from an explicit allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from .common import ROOT, CODE_FOLDERS, sha256

DOCUMENTS = (
    ".gitignore", "README.md", "README_ZH.md", "requirements.txt", "DATA_ATTRIBUTION.md",
    "docs/submission/TECHNICAL_REPORT.md", "docs/submission/DEVPOST.md",
    "docs/submission/DEVPOST_ABOUT_PROJECT.md", "docs/submission/VIDEO_NARRATION_EN.txt",
    "docs/submission/YOUTUBE_PLAN_ZH.md", "docs/submission/YOUTUBE_DESCRIPTION.md",
    "docs/submission/CONTRIBUTIONS.md", "docs/submission/RELEASE_CHECKLIST.md",
    "docs/submission/public200.json", "docs/submission/reproduction.json",
    "docs/integration/QUESTION_LIMIT_ABLATION_2026-09-01.md",
    "ranking_pipeline/results/question-limit-2-lite.json",
    "ranking_pipeline/results/question-limit-1-lite.json",
    "ranking_pipeline/results/question-limit-ablation-lite.json",
    "scripts/reproduce_question_limit_ablation.py",
    "submission_tools/mux_narration.m",
)


def selected_files():
    files = {ROOT / name for name in DOCUMENTS} | {ROOT / "agent.py"}
    for folder in CODE_FOLDERS:
        files.update((ROOT / folder).glob("*.py"))
    files.update((ROOT / "shopping_agent/tests").glob("*.py"))
    files.update((ROOT / "submission_tools/tests").glob("*.py"))
    return sorted(files)


def check_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing, symlinked or non-file source: {path.relative_to(ROOT)}")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(f"Unexpected large file: {path.name}")
    text = path.read_text(encoding="utf-8")
    checks = (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
        r"sk-[A-Za-z0-9_-]{32,}",
        r"/Users/[A-Za-z0-9._ -]+/", r"/home/[A-Za-z0-9._ -]+/",
    )
    if any(re.search(pattern, text) for pattern in checks):
        # Deliberately do not print matching content: it may be sensitive.
        raise ValueError(f"Potential credential or machine-specific path in {path.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/shopping-copilot-submission.zip")
    args = parser.parse_args()
    paths = selected_files()
    for path in paths:
        check_file(path)
    manifest = {"format_version": 1, "entry": "agent.Agent",
                "configuration": "fixed-two evidence warm-up + dynamic 4B + recall-compatible Top-50 + locked CPU ranking; no LLM",
                "files": {str(path.relative_to(ROOT)): sha256(path) for path in paths}}
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in [(str(p.relative_to(ROOT)), p.read_bytes()) for p in paths] + [("submission_manifest.json", manifest_bytes)]:
            item = zipfile.ZipInfo(name, date_time=(2026, 9, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data)
    digest = sha256(args.output)
    args.output.with_suffix(".zip.sha256").write_text(f"{digest}  {args.output.name}\n")
    print(f"Created {args.output}\nFiles: {len(paths) + 1}; bytes: {args.output.stat().st_size}\nSHA256: {digest}")
    print("No catalog, labels, model weights, .git history, private documents, caches or credentials included.")


if __name__ == "__main__":
    main()

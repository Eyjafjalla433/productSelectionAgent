"""Read-only environment and package integrity checks."""
import argparse
import json
import sqlite3
import sys

from .common import ROOT, check_kit, sha256


def verify_manifest():
    manifest = ROOT / "submission_manifest.json"
    if not manifest.exists():
        return None  # Source checkout; built archives always contain a manifest.
    data = json.loads(manifest.read_text())
    for relative, expected in data["files"].items():
        path = ROOT / relative
        if not path.is_file() or path.is_symlink() or sha256(path) != expected:
            raise ValueError(f"Package integrity check failed: {relative}")
    return len(data["files"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-only", action="store_true", help="Verify source package before downloading data")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise SystemExit("Python >=3.10 required; reference version is 3.12.13")
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
    connection.close()
    count = verify_manifest()
    print(f"Python {sys.version.split()[0]}; SQLite FTS5 available; manifest: {count if count else 'source checkout'}")
    if not args.code_only:
        check_kit()
        print("Frozen catalog, public set, starter and evaluator: hashes verified")


if __name__ == "__main__":
    main()

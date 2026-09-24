"""Verify bounded Show Me Your Agent audit exports.

The SHA-256 chain detects accidental or post-export edits and reordering. It is
deliberately unsigned and therefore does not prove who created an export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "show-me-your-agent.audit.v1"
GENESIS_DIGEST = "0" * 64


def chain_digest(previous_digest: str, record: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"previous_sha256": previous_digest, "record": record},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_audit(document: object) -> list[str]:
    """Return human-readable validation errors; an empty list means valid."""
    if not isinstance(document, dict):
        return ["Audit root must be a JSON object."]

    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"Unsupported schema_version; expected {SCHEMA_VERSION}.")

    turns = document.get("turns")
    if not isinstance(turns, list):
        return [*errors, "turns must be a JSON array."]
    if document.get("turn_count") != len(turns):
        errors.append("turn_count does not match the number of turns.")

    previous = GENESIS_DIGEST
    for index, stored in enumerate(turns, 1):
        if not isinstance(stored, dict):
            errors.append(f"Turn {index} must be a JSON object.")
            continue
        if stored.get("turn") != index:
            errors.append(f"Turn position {index} has unexpected turn number {stored.get('turn')!r}.")
        integrity = stored.get("integrity")
        if not isinstance(integrity, dict):
            errors.append(f"Turn {index} has no integrity record.")
            continue
        linked = integrity.get("previous_sha256")
        if linked != previous:
            errors.append(f"Turn {index} does not link to the preceding digest.")
        record = {key: value for key, value in stored.items() if key != "integrity"}
        expected = chain_digest(previous, record)
        actual = integrity.get("sha256")
        if actual != expected:
            errors.append(f"Turn {index} digest does not match its content.")
        previous = str(actual or expected)

    export_integrity = document.get("integrity")
    if not isinstance(export_integrity, dict):
        errors.append("Export has no integrity summary.")
    else:
        if export_integrity.get("algorithm") != "sha256-chain":
            errors.append("Unsupported integrity algorithm.")
        if export_integrity.get("head") != previous:
            errors.append("Export head does not match the final turn digest.")
        if export_integrity.get("signed") is not False:
            errors.append("This schema requires signed=false; the chain is not an identity signature.")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Show Me Your Agent audit JSON export.")
    parser.add_argument("audit", type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.audit.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"INVALID: could not read audit JSON: {exc}")
        return 2
    errors = verify_audit(document)
    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"VALID: {document['turn_count']} turn(s), head {document['integrity']['head']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

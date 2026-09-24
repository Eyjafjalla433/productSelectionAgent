"""Download, verify, and install the exact-mode runtime asset bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "asset-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as handle:
        for member in handle.infolist():
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"unsafe archive path: {member.filename}")
        handle.extractall(destination)


def download_archive(manifest: dict, destination: Path, url: str) -> None:
    """Download a Release asset, with authenticated GitHub CLI fallback."""
    try:
        print(f"Downloading {url}")
        with urllib.request.urlopen(url) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
        return
    except Exception as direct_error:
        release = manifest.get("github_release", {})
        repository = release.get("repository")
        tag = release.get("tag")
        if not repository or not tag or shutil.which("gh") is None:
            raise RuntimeError(
                "Direct download failed. For this private repository, install and "
                "authenticate GitHub CLI, or download the Release asset in the "
                "browser and pass its path with --archive."
            ) from direct_error
        print("Using authenticated GitHub CLI for the private Release asset.")
        subprocess.run(
            [
                "gh",
                "release",
                "download",
                str(tag),
                "--repo",
                str(repository),
                "--pattern",
                str(manifest["archive_name"]),
                "--dir",
                str(destination.parent),
                "--clobber",
            ],
            check=True,
        )
        if not destination.is_file():
            raise RuntimeError("GitHub CLI completed without downloading the asset")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--url", help="GitHub Release asset URL")
    source.add_argument("--archive", type=Path, help="Existing local asset ZIP")
    parser.add_argument("--force", action="store_true", help="Replace installed assets")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    url = args.url or os.environ.get("TECHJAM_ASSET_URL") or manifest.get("download_url")
    archive_path = args.archive
    temporary: Path | None = None
    if archive_path is None:
        if not url:
            raise SystemExit("Pass --url/--archive or set TECHJAM_ASSET_URL")
        temporary = Path(tempfile.mkdtemp(prefix="techjam-assets-"))
        archive_path = temporary / manifest["archive_name"]
        download_archive(manifest, archive_path, str(url))
    archive_path = archive_path.resolve()
    actual = sha256(archive_path)
    expected = str(manifest["archive_sha256"]).lower()
    if actual.lower() != expected:
        raise SystemExit(f"asset SHA256 mismatch: expected {expected}, got {actual}")
    targets = (ROOT / "data/catalog.jsonl", ROOT / "resources")
    if not args.force and any(target.exists() for target in targets):
        raise SystemExit("assets already exist; pass --force to replace them")
    safe_extract(archive_path, ROOT)
    for relative, expected_hash in manifest["critical_files"].items():
        installed = ROOT / relative
        if not installed.is_file() or sha256(installed).lower() != expected_hash.lower():
            raise SystemExit(f"installed file failed verification: {relative}")
    if temporary is not None:
        shutil.rmtree(temporary)
    print("Exact-mode assets installed and verified.")


if __name__ == "__main__":
    main()

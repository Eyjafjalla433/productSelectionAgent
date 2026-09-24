"""Install the pinned public participant kit; no private/team credentials used."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import urllib.request
import zipfile

from .common import ARCHIVE_SHA256, ARCHIVE_URL, KIT, check_kit, sha256


def install_archive(archive, destination=KIT):
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("Participant kit SHA-256 mismatch; refusing installation")
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        entries = []
        for entry in bundle.infolist():
            name = PurePosixPath(entry.filename)
            if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != "techjam-conversational-search":
                raise ValueError("Unsafe or unexpected archive path")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Archive symlinks are not permitted")
            if entry.is_dir():
                continue
            path = destination.joinpath(*name.parts[1:])
            if not path.resolve().is_relative_to(destination):
                raise ValueError("Destination contains an escaping symlink")
            data = bundle.read(entry)
            # Preflight ALL conflicts before writing any file. No force overwrite.
            if path.exists() and (not path.is_file() or path.read_bytes() != data):
                raise ValueError(f"Existing artifact differs: {path}; use a clean destination")
            entries.append((path, data))
        for path, data in entries:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    check_kit(destination)
    for name in ("catalog.jsonl", "public_set.jsonl"):
        path = destination / "data" / name
        path.chmod(path.stat().st_mode & ~0o222)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use an already-downloaded official ZIP; works offline")
    args = parser.parse_args()
    if args.archive:
        install_archive(args.archive)
    else:
        with tempfile.TemporaryDirectory(prefix="shopping-kit-") as temporary:
            archive = Path(temporary) / "participant-kit.zip"
            print(f"Downloading official public kit: {ARCHIVE_URL}", flush=True)
            request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "ShoppingCopilot-submission"})
            try:
                with urllib.request.urlopen(request, timeout=90) as response, archive.open("wb") as output:
                    shutil.copyfileobj(response, output)
            except Exception as exc:
                raise SystemExit("Download failed. Download the participant-kit ZIP from the official Release page and use --archive /path/to/file.zip.") from exc
            install_archive(archive)
    print(f"Official data and evaluator verified in {KIT}")


if __name__ == "__main__":
    main()

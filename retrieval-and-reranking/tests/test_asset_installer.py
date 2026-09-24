from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.install_assets import download_archive


class AssetInstallerTests(unittest.TestCase):
    def test_private_release_falls_back_to_authenticated_github_cli(self) -> None:
        manifest = {
            "archive_name": "runtime.zip",
            "github_release": {
                "repository": "owner/private-repository",
                "tag": "runtime-v1",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "runtime.zip"

            def create_download(command: list[str], *, check: bool) -> None:
                self.assertTrue(check)
                self.assertEqual(command[:3], ["gh", "release", "download"])
                destination.write_bytes(b"verified fixture")

            with (
                patch("scripts.install_assets.urllib.request.urlopen", side_effect=OSError),
                patch("scripts.install_assets.shutil.which", return_value="gh"),
                patch("scripts.install_assets.subprocess.run", side_effect=create_download),
            ):
                download_archive(manifest, destination, "https://example.invalid/runtime.zip")

            self.assertEqual(destination.read_bytes(), b"verified fixture")


if __name__ == "__main__":
    unittest.main()

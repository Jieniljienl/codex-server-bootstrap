"""Check the release contract and actual distribution, without installing it."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def metadata_parser(script):
    """Run the shipped validation code, rather than duplicating its rules."""
    text = (ROOT / script).read_text(encoding="utf-8")
    match = re.search(r"release_values=\".*?<<'PY'\n(.*?)\nPY\n\)\"", text, re.DOTALL)
    if not match:
        raise AssertionError(f"Cannot locate the release.json reader in {script}")
    return match.group(1)


def run_parser(script, value):
    with tempfile.TemporaryDirectory(prefix="release-metadata-test-") as directory:
        path = Path(directory) / "release.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return subprocess.run([sys.executable, "-", str(path)], input=metadata_parser(script),
                              text=True, capture_output=True)


class ReleaseMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))

    def test_metadata_identifies_a_fixed_repository_version_and_payload(self):
        meta = self.metadata
        self.assertEqual(meta["schema_version"], 1)
        self.assertEqual(meta["repository"], "Jieniljienl/codex-server-bootstrap")
        self.assertRegex(meta["tag"], r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?$")
        self.assertEqual(meta["asset"], "ai-proxy-hub-1.0.0.zip")
        self.assertEqual(meta["root"], "ai-proxy-hub-1.0.0")
        self.assertRegex(meta["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(meta["sha256"], "0" * 64)

    def test_both_installers_read_the_digest_from_metadata(self):
        # A different valid digest must reach each shell's parsed values. This
        # catches an embedded stale digest after a release is rebuilt.
        sentinel = "ab" * 32
        value = dict(self.metadata, sha256=sentinel)
        for script in ("install.sh", "server-bootstrap.sh"):
            with self.subTest(script=script):
                result = run_parser(script, value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[-1], sentinel)
                text = (ROOT / script).read_text(encoding="utf-8")
                self.assertNotIn(self.metadata["sha256"], text)

    def test_both_installers_reject_wrong_repository_unsafe_paths_and_bad_hashes(self):
        changes = [{"schema_version": 2}, {"repository": "other/project"}, {"tag": "../../latest"},
                   {"asset": "../payload.zip"}, {"root": "../home"},
                   {"sha256": "not-a-sha256"}, {"sha256": None}]
        for script in ("install.sh", "server-bootstrap.sh"):
            for change in changes:
                with self.subTest(script=script, invalid=change):
                    result = run_parser(script, dict(self.metadata, **change))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")


class ReleaseArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))
        cls.archive_path = Path(os.environ.get(
            "CODEX_BOOTSTRAP_RELEASE_ARCHIVE", str(ROOT / ".dist" / cls.metadata["asset"])))
        if not cls.archive_path.is_file():
            raise unittest.SkipTest("Build the release or set CODEX_BOOTSTRAP_RELEASE_ARCHIVE to validate its contents")
        cls.prefix = cls.metadata["root"] + "/"

    def test_release_digest_matches_the_actual_zip(self):
        with self.archive_path.open("rb") as source:
            self.assertEqual(hashlib.file_digest(source, "sha256").hexdigest(), self.metadata["sha256"])

    def test_release_has_no_runtime_data_links_or_unsafe_paths(self):
        forbidden = {"data", "backups", ".ssh", ".codex", ".venv", ".git", "__pycache__"}
        with zipfile.ZipFile(self.archive_path) as archive:
            seen = set()
            for entry in archive.infolist():
                with self.subTest(path=entry.filename):
                    path = PurePosixPath(entry.filename)
                    self.assertTrue(entry.filename.startswith(self.prefix))
                    self.assertNotIn(entry.filename, seen)
                    seen.add(entry.filename)
                    self.assertFalse(path.is_absolute())
                    self.assertNotIn("..", path.parts)
                    self.assertNotIn("\\", entry.filename)
                    self.assertFalse(forbidden.intersection(path.parts))
                    self.assertNotIn(path.name, {"auth.json", ".env", "id_ed25519", "id_rsa"})
                    self.assertIn(stat.S_IFMT(entry.external_attr >> 16), (0, stat.S_IFREG, stat.S_IFDIR))

    def test_release_preserves_complete_codex_resources_and_the_reviewed_ssh_helper(self):
        with zipfile.ZipFile(self.archive_path) as archive:
            manifest = json.loads(archive.read(self.prefix + "deploy/assets-manifest.json"))
            codex = next(item for item in manifest["assets"] if item["name"] == "codex")
            package_root = codex["package_root"] + "/"
            members = {entry["path"].removeprefix(package_root) for entry in codex["package_files"]}
            self.assertTrue({"bin/codex", "bin/codex-code-mode-host", "codex-package.json",
                             "codex-path/rg", "codex-resources/bwrap"}.issubset(members))
            self.assertTrue(any(name.startswith("codex-resources/voice/") for name in members))
            for entry in codex["package_files"]:
                with self.subTest(resource=entry["path"]):
                    info = archive.getinfo(self.prefix + entry["path"])
                    self.assertEqual(info.file_size, entry["size"])
                    with archive.open(info) as source:
                        self.assertEqual(hashlib.file_digest(source, "sha256").hexdigest(), entry["sha256"])
                    if entry.get("executable"):
                        self.assertTrue((info.external_attr >> 16) & 0o111)
            helper = "deploy/setup-desktop-ssh.py"
            self.assertEqual(archive.read(self.prefix + helper),
                             (ROOT / "payload" / helper).read_bytes().replace(b"\r\n", b"\n"))
            for attribution in ("THIRD_PARTY.md", "bin/linux-amd64/mihomo.LICENSE",
                                "bin/linux-amd64/cli-proxy-api.LICENSE", "bin/linux-amd64/codex.LICENSE",
                                "bin/linux-amd64/codex.NOTICE", "source/mihomo-v1.19.32.tar.gz",
                                "source/codex-rust-v0.163.0.tar.gz"):
                self.assertIn(self.prefix + attribution, archive.namelist())


if __name__ == "__main__":
    unittest.main()

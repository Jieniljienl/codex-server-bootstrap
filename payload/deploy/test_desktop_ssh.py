"""Isolated SSH setup checks. No server connection, model request or real auth."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request


DIRECTORY = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), DIRECTORY / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


desktop = load("setup-desktop-ssh.py")


@contextlib.contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix="desktop-test-", dir=DIRECTORY) as folder:
        base = Path(folder).resolve()
        root = base / "hub project"
        home = base / "user"
        bundle = base / "bundle"
        for path in (root / "data/codex", home, bundle):
            path.mkdir(parents=True)
        (root / "data/runtime.json").write_text(json.dumps({"proxy_port": 17890, "api_key": "private-test-key"}), encoding="utf-8")
        (root / "data/codex/auth.json").write_text("existing-test-authorization", encoding="utf-8")
        profile = b"# keep profile\nexport EXISTING_SETTING=kept\n"
        bashrc = b"# keep bashrc\ncase $- in *i*) ;; *) return;; esac\nprintf SHOULD_NOT_RUN\n"
        (home / ".profile").write_bytes(profile)
        (home / ".bashrc").write_bytes(bashrc)
        fake_codex = (
            '#!/usr/bin/env bash\nprintf "%s\\0" "$PWD" "$CODEX_HOME" "$HTTP_PROXY" "$HTTPS_PROXY" "$ALL_PROXY" '
            '"$http_proxy" "$https_proxy" "$all_proxy" "$NO_PROXY" "$no_proxy" "$@" > "$DESKTOP_TRACE"\n'
            'printf "APP_SERVER_PROTOCOL\\n"\nexit 23\n'
        ).encode("utf-8")
        contents = {"bin/codex": fake_codex, "bin/codex-code-mode-host": b"code host", "codex-path/rg": b"search",
                    "codex-resources/bwrap": b"sandbox", "codex-package.json": b"{}"}
        prefix = "bin/linux-amd64/codex-package"
        asset = {"name": "codex", "version": "rust-v0.163.0", "path": prefix + "/bin/codex", "package_root": prefix,
                 "archive_member": "bin/codex", "url": "https://github.com/openai/codex/releases/download/rust-v0.163.0/test.tar.gz",
                 "archive_sha256": "0" * 64,
                 "package_files": [{"path": prefix + "/" + name, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                                    "executable": name != "codex-package.json"} for name, body in contents.items()]}
        for name, body in contents.items():
            path = root / prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            path.chmod(0o700)
        (bundle / "assets-manifest.json").write_text(json.dumps({"architecture": "linux-amd64", "assets": [{"name": "mihomo", "must_not_be_restored": True}, asset]}), encoding="utf-8")
        fetch = load("fetch-binaries.py")
        yield {"base": base, "root": root, "home": home, "bundle": bundle, "fetch": fetch, "asset": asset,
               "contents": contents, "profile": profile, "bashrc": bashrc}


def setup(values):
    return desktop.setup(values["root"], home=values["home"], bundle_dir=values["bundle"], fetch=values["fetch"])


def make_archive(path, contents):
    with tarfile.open(path, "w:gz") as archive:
        for name, body in contents.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))


class DesktopSshTests(unittest.TestCase):
    def test_existing_package_profiles_and_backups_are_idempotent(self):
        with fixture() as values, patch.object(desktop, "proxy_download") as download:
            result = setup(values)
            self.assertFalse(result["restored"])
            self.assertEqual(result["changed"], 3)
            home = values["home"]
            self.assertTrue((home / ".profile").read_bytes().endswith(values["profile"]))
            self.assertTrue((home / ".bashrc").read_bytes().endswith(values["bashrc"]))
            self.assertEqual((home / ".profile").read_text(encoding="utf-8").count(desktop.START), 1)
            backups = list(home.glob("*.ai-hub-desktop-*.bak")) + list(home.glob(".*.ai-hub-desktop-*.bak"))
            backups = set(backups)
            self.assertEqual(len(backups), 2)
            self.assertEqual({path.read_bytes() for path in backups}, {values["profile"], values["bashrc"]})
            result = setup(values)
            self.assertEqual(result["changed"], 0)
            self.assertEqual(set(home.glob(".*.ai-hub-desktop-*.bak")), backups)
            download.assert_not_called()
            self.assertEqual((values["root"] / "data/codex/auth.json").read_text(), "existing-test-authorization")

    def test_foreign_wrapper_refused_before_resource_or_profile_changes(self):
        with fixture() as values:
            wrapper = values["home"] / ".local/bin/codex"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("another program", encoding="utf-8")
            with patch.object(desktop, "ensure_complete_package") as resources, self.assertRaisesRegex(SystemExit, "其他程序"):
                setup(values)
            resources.assert_not_called()
            self.assertEqual(wrapper.read_text(), "another program")
            self.assertEqual((values["home"] / ".profile").read_bytes(), values["profile"])
            self.assertEqual((values["home"] / ".bashrc").read_bytes(), values["bashrc"])
            self.assertEqual(list(values["home"].glob(".*.bak")), [])

    def test_wrapper_link_is_refused(self):
        with fixture() as values:
            wrapper = values["home"] / ".local/bin/codex"
            original = Path.is_symlink
            with patch.object(Path, "is_symlink", lambda path: path == wrapper or original(path)):
                with patch.object(desktop, "ensure_complete_package") as resources, self.assertRaisesRegex(SystemExit, "链接"):
                    setup(values)
            resources.assert_not_called()
            self.assertFalse(wrapper.exists())

    def test_other_installation_profile_block_is_refused(self):
        with fixture() as values:
            foreign = desktop.profile_content("# original\n", desktop.OWNER + "/another/hub")
            (values["home"] / ".profile").write_text(foreign, encoding="utf-8")
            with patch.object(desktop, "ensure_complete_package") as resources, self.assertRaisesRegex(SystemExit, "其他安装"):
                setup(values)
            resources.assert_not_called()
            self.assertEqual((values["home"] / ".profile").read_text(encoding="utf-8"), foreign)

    def test_wrapper_is_quiet_and_preserves_app_server_args_cwd_and_proxy(self):
        bash = os.environ.get("AI_HUB_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required")
        with fixture() as values:
            setup(values)
            cwd = values["base"] / "work directory"
            cwd.mkdir()
            expected_cwd = subprocess.check_output([bash, "-c", 'cd -- "$1" && pwd -P', "path", cwd.as_posix()]).decode().strip()
            trace = values["base"] / "trace.bin"
            arguments = ["app-server", "--listen", "stdio://", "argument with spaces", "", "literal $HOME ; &"]
            env = dict(os.environ, DESKTOP_TRACE=trace.as_posix(), HTTP_PROXY="http://wrong.invalid", CODEX_HOME="/wrong")
            result = subprocess.run([bash, (values["home"] / ".local/bin/codex").as_posix(), *arguments], cwd=cwd, env=env, capture_output=True)
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertEqual(result.stdout, b"APP_SERVER_PROTOCOL\n")
            self.assertEqual(result.stderr, b"")
            recorded = trace.read_bytes().decode("utf-8").split("\0")[:-1]
            self.assertEqual(recorded[0], expected_cwd)
            self.assertEqual(recorded[1], (values["root"] / "data/codex").as_posix())
            self.assertEqual(recorded[2:8], ["http://127.0.0.1:17890"] * 6)
            self.assertEqual(recorded[8:10], ["localhost,127.0.0.1,::1"] * 2)
            self.assertEqual(recorded[10:], arguments)
            self.assertNotIn("--no-daemon", recorded)

    def test_bashrc_path_block_precedes_noninteractive_return(self):
        bash = os.environ.get("AI_HUB_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required")
        with fixture() as values:
            setup(values)
            home = subprocess.check_output([bash, "-c", 'cd -- "$1" && pwd -P', "path", values["home"].as_posix()]).decode().strip()
            result = subprocess.run([bash, "-c", 'PATH=/usr/bin:/bin; . "$HOME/.bashrc"; command -v codex'], env=dict(os.environ, HOME=home), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.decode().strip(), home + "/.local/bin/codex")
            self.assertEqual(result.stderr, b"")

    def test_missing_resource_restores_only_codex_before_installing_entry(self):
        with fixture() as values:
            (values["root"] / values["asset"]["package_root"] / "codex-resources/bwrap").unlink()
            archive = values["base"] / "complete.tar.gz"
            make_archive(archive, values["contents"])
            def download(fetch, asset, destination, port):
                self.assertEqual(asset["name"], "codex")
                self.assertEqual(port, 17890)
                self.assertFalse((values["home"] / ".local/bin/codex").exists())
                self.assertEqual((values["home"] / ".profile").read_bytes(), values["profile"])
                shutil.copyfile(archive, destination)
            with patch.object(desktop, "proxy_download", side_effect=download) as mocked:
                result = setup(values)
            self.assertTrue(result["restored"])
            mocked.assert_called_once()
            self.assertTrue(values["fetch"].package_is_valid(values["asset"]))
            self.assertEqual(len(list((values["root"] / "bin/linux-amd64").glob("codex-package.before-desktop-*"))), 1)
            self.assertEqual((values["root"] / "data/codex/auth.json").read_text(), "existing-test-authorization")

    def test_existing_later_path_entry_is_moved_to_front_once(self):
        bash = os.environ.get("AI_HUB_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required")
        with fixture() as values:
            setup(values)
            home = subprocess.check_output([bash, "-c", 'cd -- "$1" && pwd -P', "path", values["home"].as_posix()]).decode().strip()
            script = 'PATH=/usr/bin:"$HOME/.local/bin"; . "$HOME/.bashrc"; printf "%s\\n" "$PATH"; . "$HOME/.bashrc"; printf "%s\\n" "$PATH"'
            result = subprocess.run([bash, "-c", script], env=dict(os.environ, HOME=home), capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            first, second = result.stdout.decode().splitlines()
            self.assertEqual(first.split(":")[0], home + "/.local/bin")
            self.assertEqual(first, second)

    def test_bad_resource_archive_leaves_wrapper_profiles_and_old_package_alone(self):
        with fixture() as values:
            package = values["root"] / values["asset"]["package_root"]
            (package / "codex-resources/bwrap").unlink()
            original = (package / "bin/codex").read_bytes()
            archive = values["base"] / "incomplete.tar.gz"
            make_archive(archive, {"bin/codex": values["contents"]["bin/codex"]})
            with patch.object(desktop, "proxy_download", side_effect=lambda _f, _a, destination, _p: shutil.copyfile(archive, destination)):
                with self.assertRaisesRegex(SystemExit, "缺少"):
                    setup(values)
            self.assertFalse((values["home"] / ".local/bin/codex").exists())
            self.assertEqual((values["home"] / ".profile").read_bytes(), values["profile"])
            self.assertEqual((package / "bin/codex").read_bytes(), original)
            self.assertEqual(list(package.parent.glob("codex-package.before-desktop-*")), [])

    def test_download_uses_explicit_local_proxy_even_with_no_proxy_wildcard(self):
        seen = []
        class RejectingProxy(BaseHTTPRequestHandler):
            def do_CONNECT(self):
                seen.append(self.path)
                self.send_error(502, "test proxy does not forward")
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), RejectingProxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with fixture() as values, patch.dict(os.environ, {"NO_PROXY": "*", "no_proxy": "*"}):
                original_open = urllib.request.urlopen
                original_bypass = urllib.request.proxy_bypass
                with self.assertRaises(urllib.error.URLError):
                    desktop.proxy_download(values["fetch"], values["asset"], values["base"] / "download.tar.gz", server.server_address[1])
                self.assertEqual(seen, ["github.com:443"])
                self.assertIs(urllib.request.urlopen, original_open)
                self.assertIs(urllib.request.proxy_bypass, original_bypass)
                self.assertEqual(os.environ["NO_PROXY"], "*")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

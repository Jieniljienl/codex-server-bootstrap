"""Local guardrail checks; does not start services, execute binaries or use network."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


def load_script(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load_script("preflight.py")
services = load_script("make-services.py")
fetch = load_script("fetch-binaries.py")


def package_fixture():
    contents = {"bin/codex": b"test CLI", "bin/codex-code-mode-host": b"test code host", "codex-path/rg": b"test search",
                "codex-resources/bwrap": b"test sandbox", "codex-package.json": b"{}"}
    prefix = "bin/linux-amd64/codex-package"
    asset = {"name": "codex", "path": prefix + "/bin/codex", "package_root": prefix, "archive_member": "bin/codex",
             "package_files": [{"path": prefix + "/" + name, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
                                "executable": name != "codex-package.json"} for name, body in contents.items()]}
    return asset, contents


def write_package_archive(path, contents, extra=None):
    with tarfile.open(path, "w:gz") as archive:
        for name, body in contents.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
        if extra is not None:
            archive.addfile(extra, io.BytesIO(b""))


class DeploymentSafetyTests(unittest.TestCase):
    def test_package_manifest_cannot_omit_required_resources(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            asset, _ = package_fixture()
            asset["package_files"] = [item for item in asset["package_files"] if not item["path"].endswith("/codex-path/rg")]
            with patch.object(fetch, "ROOT", Path(folder)), self.assertRaisesRegex(SystemExit, "必需"):
                fetch.package_is_valid(asset)

    def test_codex_package_needs_sandbox_and_metadata_not_just_main_binary(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            asset, contents = package_fixture()
            with patch.object(fetch, "ROOT", root):
                for name, body in contents.items():
                    path = root / asset["package_root"] / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                self.assertTrue(fetch.package_is_valid(asset))
                (root / asset["package_root"] / "codex-resources/bwrap").unlink()
                self.assertFalse(fetch.package_is_valid(asset))
                archive = root / "archive.tar.gz"
                write_package_archive(archive, contents)
                staging = root / "staging"
                staging.mkdir()
                fetch.restore_package(asset, archive, staging)
                self.assertTrue(fetch.package_is_valid(asset))

    def test_package_archive_path_traversal_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            asset, contents = package_fixture()
            archive = root / "archive.tar.gz"
            write_package_archive(archive, contents, tarfile.TarInfo("../../outside"))
            staging = root / "staging"
            staging.mkdir()
            with patch.object(fetch, "ROOT", root), self.assertRaisesRegex(SystemExit, "越界"):
                fetch.restore_package(asset, archive, staging)
            self.assertFalse((root / asset["path"]).exists())
            self.assertFalse((root.parent / "outside").exists())

    def test_package_archive_links_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
                root = Path(folder)
                asset, contents = package_fixture()
                link = tarfile.TarInfo("linked-file")
                link.type = kind
                link.linkname = "/outside"
                archive = root / "archive.tar.gz"
                write_package_archive(archive, contents, link)
                staging = root / "staging"
                staging.mkdir()
                with patch.object(fetch, "ROOT", root), self.assertRaisesRegex(SystemExit, "链接"):
                    fetch.restore_package(asset, archive, staging)
                self.assertFalse((root / asset["path"]).exists())

    def test_missing_package_resource_never_updates_existing_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            asset, contents = package_fixture()
            target = root / asset["path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"keep previous file")
            archive = root / "archive.tar.gz"
            write_package_archive(archive, {"bin/codex": contents["bin/codex"]})
            staging = root / "staging"
            staging.mkdir()
            with patch.object(fetch, "ROOT", root), self.assertRaisesRegex(SystemExit, "缺少"):
                fetch.restore_package(asset, archive, staging)
            self.assertEqual(target.read_bytes(), b"keep previous file")

    def test_install_links_codex_to_complete_package_entry(self):
        bash = os.environ.get("AI_HUB_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required for the link check")
        install = Path(__file__).with_name("install.sh").read_text(encoding="utf-8")
        begin = install.index("link_project_binaries() {")
        end = install.index("\n}\n", begin) + 3
        script = 'set -euo pipefail\nAI_HUB_ROOT=/test/project\nln() { printf "%s\\0" "$@"; }\n' + install[begin:end] + '\nlink_project_binaries\n'
        arguments = subprocess.check_output([bash, "-c", script]).decode("utf-8").split("\0")[:-1]
        self.assertEqual(arguments[-4:], ["-sfn", "--", "linux-amd64/codex-package/bin/codex", "/test/project/bin/codex"])

    def test_codex_wrapper_preserves_arguments_cwd_and_private_proxy_environment(self):
        bash = os.environ.get("AI_HUB_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required for the wrapper check")
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder).resolve() / "installation"
            for relative in ("data/codex", "deploy", "bin", ".venv/bin"):
                (root / relative).mkdir(parents=True, exist_ok=True)
            (root / "data/runtime.json").write_text(json.dumps({"proxy_port": 17890}), encoding="utf-8")
            auth = root / "data/codex/auth.json"
            auth.write_text("existing test authorization", encoding="utf-8")
            shutil.copyfile(Path(__file__).with_name("common.sh"), root / "deploy/common.sh")
            python_stub = root / ".venv/bin/python"
            python_stub.write_text('#!/usr/bin/env bash\nprintf "17890\\n"\n', encoding="utf-8", newline="\n")
            python_stub.chmod(0o700)
            codex_stub = root / "bin/codex"
            codex_stub.write_text(
                '#!/usr/bin/env bash\nprintf "%s\\0" "$PWD" "$CODEX_HOME" '
                '"$HTTP_PROXY" "$HTTPS_PROXY" "$ALL_PROXY" "$http_proxy" "$https_proxy" "$all_proxy" '
                '"$NO_PROXY" "$no_proxy" "$@"\nexit 37\n', encoding="utf-8", newline="\n")
            codex_stub.chmod(0o700)
            home = Path(folder).resolve() / "user"
            home.mkdir()
            with patch.object(services, "ROOT", root), patch.object(Path, "home", return_value=home), contextlib.redirect_stdout(io.StringIO()):
                services.main()
            cwd = Path(folder).resolve() / "work project"
            cwd.mkdir()
            bash_paths = subprocess.check_output(
                [bash, "-c", 'cd -- "$1"; pwd -P; cd -- "$2"; pwd -P', "paths", root.as_posix(), cwd.as_posix()]
            ).decode("utf-8").splitlines()
            environment_before = dict(os.environ)
            child_environment = dict(os.environ, HTTP_PROXY="http://wrong.invalid", CODEX_HOME="/not-this-project")
            for arguments in ([], ["login", "--device-auth"], ["resume", "--last", "question with spaces", ""],
                              ["fork", "--last"], ["--no-daemon", "resume", "--last"], ["--", "literal $HOME & x;?"], ["--", "--no-daemon"]):
                result = subprocess.run([bash, (root / "codex-vpn").as_posix(), *arguments], cwd=cwd, env=child_environment, capture_output=True)
                self.assertEqual(result.returncode, 37, result.stderr)
                values = result.stdout.decode("utf-8").split("\0")[:-1]
                self.assertEqual(values[0], bash_paths[1])
                self.assertEqual(values[1], bash_paths[0] + "/data/codex")
                self.assertEqual(values[2:8], ["http://127.0.0.1:17890"] * 6)
                self.assertEqual(values[8:10], ["localhost,127.0.0.1,::1"] * 2)
                options = arguments[:arguments.index("--")] if "--" in arguments else arguments
                expected = arguments if "--no-daemon" in options else ["--no-daemon", *arguments]
                self.assertEqual(values[10:], expected)
            self.assertEqual(dict(os.environ), environment_before)
            self.assertEqual(auth.read_text(encoding="utf-8"), "existing test authorization")

    def test_partial_venv_with_python_but_no_pip_is_repaired_in_place(self):
        bash = os.environ.get("AI_HUB_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required for the venv recovery check")
        install = Path(__file__).with_name("install.sh").read_text(encoding="utf-8")
        begin = install.index("ensure_project_venv() {")
        end = install.index("\n}\n", begin) + 3
        helper = install[begin:end]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            (root / ".venv/bin").mkdir(parents=True)
            (root / "data").mkdir()
            sentinel = root / "data/keep.txt"
            sentinel.write_text("keep existing user data", encoding="utf-8")
            interpreter = root / ".venv/bin/python"
            interpreter.write_text('#!/usr/bin/env bash\n[[ -f "$(dirname -- "$0")/pip-ready" ]]\n', encoding="utf-8", newline="\n")
            interpreter.chmod(0o700)
            script = (
                'set -euo pipefail\nAI_HUB_ROOT="$1"\n'
                'die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
                'python3() { [[ "$1" == -m && "$2" == venv && "$3" == "$AI_HUB_ROOT/.venv" ]]; '
                'printf "repair\\n" >> "$AI_HUB_ROOT/repair-count"; touch "$AI_HUB_ROOT/.venv/bin/pip-ready"; }\n'
                + helper + '\nensure_project_venv\nensure_project_venv\n'
            )
            subprocess.run([bash, "-c", script, "venv-test", root.as_posix()], check=True, capture_output=True)
            self.assertEqual((root / "repair-count").read_text().splitlines(), ["repair"])
            self.assertEqual(sentinel.read_text(), "keep existing user data")

    def test_shipping_report_port_is_always_reserved(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            with patch("sys.argv", ["preflight", "--root", folder, "--web-port", "8080"]):
                with self.assertRaisesRegex(SystemExit, "8080"):
                    preflight.main()

    def test_duplicate_ports_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            with patch("sys.argv", ["preflight", "--root", folder, "--web-port", "18317"]):
                with self.assertRaisesRegex(SystemExit, "互不相同"):
                    preflight.main()

    def test_zero_port_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            with patch("sys.argv", ["preflight", "--root", folder, "--web-port", "0"]):
                with self.assertRaisesRegex(SystemExit, "1024"):
                    preflight.main()

    def test_saved_port_cannot_be_silently_changed_by_reinstall(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            (root / "data").mkdir()
            (root / "data/runtime.json").write_text(json.dumps({"web_port": 8090}), encoding="utf-8")
            with patch("sys.argv", ["preflight", "--root", folder, "--web-port", "8091"]):
                with self.assertRaisesRegex(SystemExit, "与参数不同"):
                    preflight.main()

    def test_saved_bind_cannot_be_silently_changed_by_reinstall(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            (root / "data").mkdir()
            (root / "data/runtime.json").write_text(json.dumps({"web_bind": "0.0.0.0"}), encoding="utf-8")
            with patch("sys.argv", ["preflight", "--root", folder, "--web-bind", "127.0.0.1"]):
                with self.assertRaisesRegex(SystemExit, "与参数不同"):
                    preflight.main()

    def test_foreign_busy_port_is_rejected_without_stopping_anything(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            with patch("sys.argv", ["preflight", "--root", folder]):
                with patch.object(preflight.socket, "socket") as sock, patch.object(preflight, "owned_listener", return_value=False):
                    sock.return_value.__enter__.return_value.bind.side_effect = OSError("busy")
                    with self.assertRaisesRegex(SystemExit, "未停止任何现有程序"):
                        preflight.main()

    def test_existing_listener_requires_matching_install_and_pid(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder).resolve()
            unit = root / "owned.service"
            unit.write_text(f"# AI_PROXY_HUB_ROOT={root}\n", encoding="utf-8")
            fields = f"FragmentPath={unit}\nMainPID=1234\n"
            with patch.object(preflight.subprocess, "check_output", side_effect=[fields, 'users:(("mihomo",pid=1234,fd=7))']):
                self.assertTrue(preflight.owned_listener(root, 17890, "ai-hub-mihomo.service"))
            with patch.object(preflight.subprocess, "check_output", side_effect=[fields, 'users:(("foreign",pid=9999,fd=7))']):
                self.assertFalse(preflight.owned_listener(root, 17890, "ai-hub-mihomo.service"))

    def test_foreign_unit_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            target = Path(folder) / "unit.service"
            target.write_text("other application\n")
            with self.assertRaisesRegex(SystemExit, "拒绝覆盖"):
                services.write_unit(target, "replacement", "our marker")
            self.assertEqual(target.read_text(), "other application\n")

    def test_service_generation_and_wrapper_are_scoped(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder).resolve() / "installation with%specifier"
            root.mkdir()
            (root / "data").mkdir()
            (root / "data/runtime.json").write_text(json.dumps({"proxy_port": 17890}))
            home = Path(folder) / "user"
            home.mkdir()
            with patch.object(services, "ROOT", root), patch.object(Path, "home", return_value=home), contextlib.redirect_stdout(io.StringIO()):
                services.main()
            units = home / ".config/systemd/user"
            self.assertEqual({entry.name for entry in units.iterdir()}, set(services.UNIT_NAMES))
            cli = (units / "ai-hub-cliproxy.service").read_text(encoding="utf-8")
            web = (units / "ai-hub-web.service").read_text(encoding="utf-8")
            self.assertIn("WRITABLE_PATH=/dev/null", cli)
            self.assertIn("http://127.0.0.1:17890", cli)
            self.assertNotIn("HTTP_PROXY", web)
            self.assertIn("StandardOutput=null", cli)
            self.assertIn("StandardError=null", cli)
            # WorkingDirectory is a scalar path, unlike ExecStart/Environment:
            # systemd does not shell-unquote it before checking absoluteness.
            scalar = next(line.split("=", 1)[1] for line in web.splitlines() if line.startswith("WorkingDirectory="))
            decoded = scalar.replace("%%", "%")
            self.assertTrue(Path(decoded).is_absolute())
            self.assertEqual(Path(decoded), root)
            self.assertNotIn('"', scalar)
            self.assertIn("%%specifier", scalar)
            command = next(line.split("=", 1)[1] for line in cli.splitlines() if line.startswith("ExecStart="))
            self.assertTrue(command.startswith('"'))
            self.assertEqual(shlex.split(command)[0].replace("%%", "%"), str(root / "bin/cli-proxy-api"))
            proxy_setting = next(line.split("=", 1)[1] for line in cli.splitlines() if line.startswith("Environment=\"HTTP_PROXY="))
            self.assertEqual(shlex.split(proxy_setting), ["HTTP_PROXY=http://127.0.0.1:17890"])
            self.assertIn('export CODEX_HOME="$AI_HUB_ROOT/data/codex"', (root / "codex-vpn").read_text(encoding="utf-8"))
            self.assertFalse((home / ".bashrc").exists())


if __name__ == "__main__":
    unittest.main()

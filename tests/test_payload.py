"""Exercise desktop SSH integration without a server, account, or model request."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "payload" / "deploy"


def load_module(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), DEPLOY / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


desktop = load_module("setup-desktop-ssh.py")


def find_bash():
    candidates = [os.environ.get("BASH_PATH"), os.environ.get("AI_HUB_TEST_BASH"), shutil.which("bash")]
    if os.name == "nt":
        candidates.extend([
            str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"),
        ])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            check = subprocess.run([candidate, "--noprofile", "--norc", "-c", "exit 0"],
                                   capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode == 0:
            return candidate
    return None


BASH = find_bash()


@contextlib.contextmanager
def installation():
    with tempfile.TemporaryDirectory(prefix="codex-bootstrap-test-") as temporary:
        base = Path(temporary).resolve()
        root, home, bundle = base / "hub with spaces", base / "user", base / "bundle"
        for directory in (root / "data/codex", home, bundle):
            directory.mkdir(parents=True)
        (root / "data/runtime.json").write_text(json.dumps({"proxy_port": 17891}), encoding="utf-8")
        authorization = b"synthetic-test-authorization-must-be-preserved"
        (root / "data/codex/auth.json").write_bytes(authorization)
        profile = b"# existing profile\nexport EXISTING_SETTING=kept\n"
        bashrc = b"# existing bashrc\ncase $- in *i*) ;; *) return;; esac\nprintf UNEXPECTED_OUTPUT\n"
        (home / ".profile").write_bytes(profile)
        (home / ".bashrc").write_bytes(bashrc)
        fake_program = (
            '#!/usr/bin/env bash\n'
            'printf "%s\\0" "$PWD" "$CODEX_HOME" "$HTTP_PROXY" "$HTTPS_PROXY" "$ALL_PROXY" '
            '"$http_proxy" "$https_proxy" "$all_proxy" "$NO_PROXY" "$no_proxy" "$@" > "$DESKTOP_TRACE"\n'
            'printf "APP_SERVER_PROTOCOL\\n"\nexit 23\n'
        ).encode("utf-8")
        members = {"bin/codex": fake_program, "bin/codex-code-mode-host": b"synthetic helper",
                   "codex-path/rg": b"synthetic search", "codex-resources/bwrap": b"synthetic sandbox",
                   "codex-package.json": b"{}"}
        prefix = "bin/linux-amd64/codex-package"
        asset = {"name": "codex", "path": prefix + "/bin/codex", "package_root": prefix,
                 "package_files": []}
        for name, content in members.items():
            destination = root / prefix / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            asset["package_files"].append({"path": prefix + "/" + name,
                                          "sha256": hashlib.sha256(content).hexdigest(),
                                          "size": len(content), "executable": name != "codex-package.json"})
        (bundle / "assets-manifest.json").write_text(
            json.dumps({"architecture": "linux-amd64", "assets": [asset]}), encoding="utf-8")
        fetch = load_module("fetch-binaries.py")
        yield {"base": base, "root": root, "home": home, "bundle": bundle, "fetch": fetch,
               "profile": profile, "bashrc": bashrc, "authorization": authorization}


def configure(values):
    return desktop.setup(values["root"], home=values["home"],
                         bundle_dir=values["bundle"], fetch=values["fetch"])


def bash_path(path):
    result = subprocess.run([BASH, "--noprofile", "--norc", "-c", 'cd -- "$1" && pwd -P',
                             "path", path.as_posix()], capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


class DesktopPayloadTests(unittest.TestCase):
    def test_setup_preserves_auth_and_profiles_and_is_idempotent(self):
        with installation() as values, patch.object(desktop, "proxy_download") as download:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                first = configure(values)
            self.assertEqual(output.getvalue(), "")
            self.assertFalse(first["restored"])
            self.assertEqual(first["changed"], 3)
            home = values["home"]
            for name, original in ((".profile", values["profile"]), (".bashrc", values["bashrc"])):
                content = (home / name).read_bytes()
                self.assertTrue(content.endswith(original))
                self.assertEqual(content.decode().count(desktop.START), 1)
            backups = {p: p.read_bytes() for p in home.glob(".*.ai-hub-desktop-*.bak")}
            self.assertEqual(set(backups.values()), {values["profile"], values["bashrc"]})
            before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
            second = configure(values)
            self.assertEqual(second["changed"], 0)
            self.assertEqual({p: p.read_bytes() for p in home.rglob("*") if p.is_file()}, before)
            self.assertEqual((values["root"] / "data/codex/auth.json").read_bytes(), values["authorization"])
            download.assert_not_called()

    @unittest.skipUnless(BASH, "Bash is needed to execute the generated SSH integration")
    def test_noninteractive_profile_load_is_silent_and_finds_codex_before_return(self):
        with installation() as values:
            configure(values)
            home = bash_path(values["home"])
            trace = values["base"] / "path-trace.txt"
            script = ('PATH=/usr/bin:/bin; . "$HOME/.profile"; . "$HOME/.bashrc"; '
                      'command -v codex > "$DESKTOP_TRACE"; '
                      'first=$PATH; . "$HOME/.bashrc"; test "$first" = "$PATH"')
            result = subprocess.run([BASH, "--noprofile", "--norc", "-c", script],
                                    env=dict(os.environ, HOME=home, DESKTOP_TRACE=trace.as_posix()),
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            self.assertEqual(trace.read_text(encoding="utf-8").strip(), home + "/.local/bin/codex")

    @unittest.skipUnless(BASH, "Bash is needed to execute the generated Codex wrapper")
    def test_wrapper_preserves_protocol_arguments_exit_status_cwd_and_proxy(self):
        with installation() as values:
            configure(values)
            cwd = values["base"] / "working directory"
            cwd.mkdir()
            trace = values["base"] / "arguments.bin"
            arguments = ["app-server", "--listen", "stdio://", "argument with spaces", "", "literal $HOME ; &"]
            env = dict(os.environ, DESKTOP_TRACE=trace.as_posix(), CODEX_HOME="/wrong",
                       HTTP_PROXY="http://wrong.invalid", NO_PROXY="*")
            wrapper = values["home"] / ".local/bin/codex"
            result = subprocess.run([BASH, wrapper.as_posix(), *arguments],
                                    cwd=cwd, env=env, capture_output=True)
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertEqual(result.stdout, b"APP_SERVER_PROTOCOL\n")
            self.assertEqual(result.stderr, b"")
            recorded = trace.read_bytes().decode("utf-8").split("\0")[:-1]
            self.assertEqual(recorded[0], bash_path(cwd))
            self.assertEqual(recorded[1], (values["root"] / "data/codex").as_posix())
            self.assertEqual(recorded[2:8], ["http://127.0.0.1:17891"] * 6)
            self.assertEqual(recorded[8:10], ["localhost,127.0.0.1,::1"] * 2)
            self.assertEqual(recorded[10:], arguments)
            self.assertNotIn("--no-daemon", recorded[10:])

    def test_unknown_wrapper_is_refused_before_resources_or_profiles_change(self):
        with installation() as values:
            wrapper = values["home"] / ".local/bin/codex"
            wrapper.parent.mkdir(parents=True)
            original = b"#!/bin/sh\nprintf foreign-program\n"
            wrapper.write_bytes(original)
            with patch.object(desktop, "ensure_complete_package") as resources, self.assertRaises(SystemExit):
                configure(values)
            resources.assert_not_called()
            self.assertEqual(wrapper.read_bytes(), original)
            self.assertEqual((values["home"] / ".profile").read_bytes(), values["profile"])
            self.assertEqual((values["home"] / ".bashrc").read_bytes(), values["bashrc"])
            self.assertEqual(list(values["home"].glob(".*.bak")), [])

    def test_linked_wrapper_parent_or_profile_is_refused_before_any_change(self):
        # Mock the filesystem's link report so this also runs without Windows
        # developer-mode privileges; the actual target validation is unchanged.
        for relative in (".local", ".local/bin", ".local/bin/codex", ".profile", ".bashrc"):
            with self.subTest(path=relative), installation() as values:
                target = values["home"] / relative
                original = Path.is_symlink
                with patch.object(Path, "is_symlink", lambda p: p == target or original(p)):
                    with patch.object(desktop, "ensure_complete_package") as resources, self.assertRaises(SystemExit):
                        configure(values)
                resources.assert_not_called()
                self.assertFalse((values["home"] / ".local/bin/codex").exists())
                self.assertEqual((values["home"] / ".profile").read_bytes(), values["profile"])
                self.assertEqual((values["home"] / ".bashrc").read_bytes(), values["bashrc"])


if __name__ == "__main__":
    unittest.main()

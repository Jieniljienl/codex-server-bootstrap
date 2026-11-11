"""Create only this installation's three systemd user service files."""
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIT_NAMES = ("ai-hub-mihomo.service", "ai-hub-cliproxy.service", "ai-hub-web.service")


def quote(value: object) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        raise SystemExit("安装路径不能包含换行符。")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def write_unit(path: Path, text: str, marker: str) -> None:
    if path.exists() and marker not in path.read_text(encoding="utf-8").splitlines():
        raise SystemExit(f"拒绝覆盖其他安装的服务：{path.name}")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> None:
    config = json.loads((ROOT / "data/runtime.json").read_text(encoding="utf-8"))
    directory = Path.home() / ".config/systemd/user"
    directory.mkdir(parents=True, exist_ok=True)
    marker = f"# AI_PROXY_HUB_ROOT={ROOT}"
    proxy = f"http://127.0.0.1:{config['proxy_port']}"
    commands = {
        "ai-hub-mihomo.service": [ROOT / "bin/mihomo", "-d", ROOT / "data/mihomo", "-f", ROOT / "data/mihomo/config.yaml"],
        "ai-hub-cliproxy.service": [ROOT / "bin/cli-proxy-api", "--config", ROOT / "data/cliproxy/config.yaml"],
        "ai-hub-web.service": [ROOT / ".venv/bin/python", "-m", "hub.manage", "serve", "--data-dir", ROOT / "data"],
    }
    for unit in UNIT_NAMES:
        dependencies = ""
        env_lines = ""
        if unit == "ai-hub-cliproxy.service":
            dependencies = "After=ai-hub-mihomo.service\nRequires=ai-hub-mihomo.service\n"
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env_lines += f"Environment={quote(key + '=' + proxy)}\n"
            for key in ("NO_PROXY", "no_proxy"):
                env_lines += f"Environment={quote(key + '=localhost,127.0.0.1,::1')}\n"
            # Upstream disabled request logs still otherwise store error-* bodies.
            # A non-directory path prevents that logger and its auth-dir fallback.
            env_lines += "Environment=WRITABLE_PATH=/dev/null\n"
        text = (
            f"{marker}\n[Unit]\nDescription=AI Proxy Hub — {unit.removeprefix('ai-hub-').removesuffix('.service')}\n"
            f"{dependencies}StartLimitIntervalSec=60\nStartLimitBurst=5\n\n"
            f"[Service]\nType=simple\nWorkingDirectory={str(ROOT).replace('%', '%%')}\n"
            f"ExecStart={' '.join(quote(arg) for arg in commands[unit])}\n"
            f"{env_lines}Environment=PYTHONUNBUFFERED=1\nUMask=0077\n"
            "Restart=on-failure\nRestartSec=3\nNoNewPrivileges=true\n"
            "Nice=10\nCPUWeight=20\nMemoryMax=384M\n"
            "StandardOutput=null\nStandardError=null\n\n[Install]\nWantedBy=default.target\n"
        )
        write_unit(directory / unit, text, marker)
    wrapper = ROOT / "codex-vpn"
    wrapper.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        'source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/deploy/common.sh"\n'
        'require_install\nexport CODEX_HOME="$AI_HUB_ROOT/data/codex"\n'
        'mkdir -p -- "$CODEX_HOME"\nchmod 700 -- "$CODEX_HOME"\n'
        'codex_flags=(--no-daemon)\n'
        'for codex_arg in "$@"; do\n'
        '  if [[ "$codex_arg" == -- ]]; then break; fi\n'
        '  if [[ "$codex_arg" == --no-daemon ]]; then codex_flags=(); break; fi\n'
        'done\n'
        'run_with_hub_proxy "$AI_HUB_ROOT/bin/codex" "${codex_flags[@]}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    wrapper.chmod(0o700)
    print("已生成本目录专用服务及 codex-vpn 入口。")


if __name__ == "__main__":
    main()

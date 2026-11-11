"""Refuse busy ports, except ports demonstrably owned by this exact install."""
import argparse
import json
import re
import socket
import subprocess
import sys
from pathlib import Path


def owned_listener(root: Path, port: int, unit: str) -> bool:
    try:
        fields = subprocess.check_output(
            ["systemctl", "--user", "show", unit, "--property=FragmentPath,MainPID"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        values = dict(line.split("=", 1) for line in fields.splitlines() if "=" in line)
        unit_path = Path(values.get("FragmentPath", ""))
        marker = f"# AI_PROXY_HUB_ROOT={root}"
        if not unit_path.is_file() or marker not in unit_path.read_text(encoding="utf-8").splitlines():
            return False
        pid = int(values.get("MainPID", "0"))
        if pid <= 0:
            return False
        listeners = subprocess.check_output(
            ["ss", "-H", "-lntp", f"( sport = :{port} )"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        pids = {int(found) for found in re.findall(r"pid=(\d+)", listeners)}
        return bool(pids) and pids == {pid}
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--web-port", type=int)
    parser.add_argument("--proxy-port", type=int)
    parser.add_argument("--controller-port", type=int)
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--web-bind")
    args = parser.parse_args()
    root = args.root.resolve()
    config_path = root / "data/runtime.json"
    saved = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    for key in ("web_port", "proxy_port", "controller_port", "api_port", "web_bind"):
        supplied = getattr(args, key)
        if saved and supplied is not None and supplied != saved.get(key):
            sys.exit(f"已有安装的 {key} 与参数不同；本次未修改任何配置。请先停止本项目并单独维护端口配置，不能通过重复安装静默改动。")
    def port_value(key: str, fallback: int) -> int:
        supplied = getattr(args, key)
        return supplied if supplied is not None else saved.get(key, fallback)

    ports = [
        ("网页", port_value("web_port", 8090), "0.0.0.0", "ai-hub-web.service"),
        ("代理", port_value("proxy_port", 17890), "127.0.0.1", "ai-hub-mihomo.service"),
        ("节点控制", port_value("controller_port", 19090), "127.0.0.1", "ai-hub-mihomo.service"),
        ("模型 API", port_value("api_port", 18317), "127.0.0.1", "ai-hub-cliproxy.service"),
    ]
    numbers = [entry[1] for entry in ports]
    if any(not isinstance(port, int) or port < 1024 or port > 65535 or port == 8080 for port in numbers):
        sys.exit("端口必须为 1024–65535，且不能使用出货报告预留端口 8080。")
    if len(set(numbers)) != len(numbers):
        sys.exit("本项目的四个端口必须互不相同。")
    for label, port, bind_address, unit in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((bind_address, port))
            except OSError:
                if owned_listener(root, port, unit):
                    continue
                sys.exit(f"{label}端口 {port} 已占用；未停止任何现有程序。请为本项目指定其他端口。")
    print("端口检查通过；不会改动其他服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import threading

import yaml
from werkzeug.security import generate_password_hash

VERSION = "1.0.0"
GROUP = "AI"
RESERVED = {"AI", "DIRECT", "REJECT", "GLOBAL", "COMPATIBLE"}
LOCK = threading.RLock()


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".new")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def write_json(path: Path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def load(data_dir: Path):
    return read_json(data_dir / "runtime.json")


def mihomo_config(config):
    return {
        "port": config["proxy_port"], "allow-lan": False,
        "bind-address": "127.0.0.1", "mode": "rule", "log-level": "silent",
        "ipv6": False, "tun": {"enable": False},
        "external-controller": f"127.0.0.1:{config['controller_port']}",
        "secret": config["mihomo_secret"],
        "profile": {"store-selected": True},
        "proxy-providers": {"subscription": {"type": "file", "path": "./subscription.yaml"}},
        "proxy-groups": [{"name": GROUP, "type": "select", "proxies": ["REJECT"], "use": ["subscription"]}],
        "rules": ["MATCH,AI"],
    }


def initialize(data_dir: Path, password: str, bind="0.0.0.0", port=8090,
               proxy_port=17890, controller_port=19090, api_port=18317):
    data_dir = data_dir.resolve()
    if (data_dir / "runtime.json").exists():
        return load(data_dir)
    if not 12 <= len(password) <= 256:
        raise ValueError("网页密码需要12到256个字符。")
    ports = [int(port), int(proxy_port), int(controller_port), int(api_port)]
    if len(set(ports)) != 4 or any(not 1024 <= p <= 65535 for p in ports):
        raise ValueError("四个端口必须不同，且位于1024到65535之间。")
    data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(data_dir, 0o700)
    for folder in ["mihomo", "cliproxy", "cliproxy/auths"]:
        (data_dir / folder).mkdir(mode=0o700, parents=True, exist_ok=True)
    config = {
        "version": VERSION, "web_bind": bind, "web_port": ports[0],
        "proxy_port": ports[1], "controller_port": ports[2], "api_port": ports[3],
        "password_hash": generate_password_hash(password),
        "session_secret": secrets.token_hex(32), "auth_generation": secrets.token_hex(16),
        "mihomo_secret": secrets.token_hex(32), "api_key": secrets.token_hex(32),
    }
    atomic_write(data_dir / "mihomo/config.yaml", yaml.safe_dump(mihomo_config(config), allow_unicode=True, sort_keys=False))
    atomic_write(data_dir / "mihomo/subscription.yaml", "proxies: []\n")
    proxy_api = {
        "config-version": 8,
        "server": {"host": "127.0.0.1", "port": config["api_port"], "discovery": {"enabled": False}},
        "management": {"allow-remote": False, "secret-key": "", "disable-control-panel": True},
        "access": {"api-keys": [config["api_key"]]},
        "requests": {"proxy-url": f"http://127.0.0.1:{config['proxy_port']}"},
        "oauth": {"auth-dir": str(data_dir / "cliproxy/auths")},
        "observability": {"logs": {"debug": False, "logging-to-file": False, "request-log": False},
                          "usage": {"usage-statistics-enabled": False}},
    }
    atomic_write(data_dir / "cliproxy/config.yaml", yaml.safe_dump(proxy_api, allow_unicode=True, sort_keys=False))
    write_json(data_dir / "state.json", {"selected": "REJECT", "updated_at": None})
    write_json(data_dir / "runtime.json", config)
    return config

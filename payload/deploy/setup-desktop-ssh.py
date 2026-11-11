"""Prepare this hub's complete Codex package for quiet desktop SSH app-server use."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import shlex
import stat
import tempfile
import urllib.request
import uuid

START = "# BEGIN AI_PROXY_HUB_DESKTOP_PATH"
END = "# END AI_PROXY_HUB_DESKTOP_PATH"
OWNER = "# AI_PROXY_HUB_DESKTOP_ROOT="


def load_fetch(directory: Path):
    spec = importlib.util.spec_from_file_location("ai_hub_desktop_fetch", directory / "fetch-binaries.py")
    if spec is None or spec.loader is None:
        raise SystemExit("缺少同目录的 fetch-binaries.py。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_target(path: Path, home: Path) -> None:
    if not path.is_relative_to(home):
        raise SystemExit("接入文件必须位于当前用户的主目录内。")
    current = home
    for component in path.relative_to(home).parts:
        current = current / component
        if current.is_symlink():
            raise SystemExit("接入目标或其父目录是链接，未覆盖任何文件。")
        if current.exists() and current != path and not current.is_dir():
            raise SystemExit("接入目标父路径不是目录，未覆盖任何文件。")
    if path.exists() and not path.is_file():
        raise SystemExit("接入目标不是普通文件，未覆盖任何文件。")


def profile_content(source: str, marker: str) -> str:
    block = (
        f"{START}\n{marker}\n"
        'case "${PATH-}" in\n'
        '  "$HOME/.local/bin"|"$HOME/.local/bin":*) ;;\n'
        '  *) export PATH="$HOME/.local/bin${PATH:+:$PATH}" ;;\n'
        f"esac\n{END}\n"
    )
    if START in source or END in source:
        if source.count(START) != 1 or source.count(END) != 1:
            raise SystemExit("已有 PATH 接入块格式异常，未覆盖任何文件。")
        begin = source.index(START)
        finish = source.index(END, begin) + len(END)
        if begin and source[begin - 1] != "\n":
            raise SystemExit("已有 PATH 接入块格式异常，未覆盖任何文件。")
        if marker not in source[begin:finish].splitlines():
            raise SystemExit("已有 PATH 接入块属于其他安装目录，未覆盖任何文件。")
        if finish < len(source) and source[finish] == "\n":
            finish += 1
        source = source[:begin] + source[finish:]
    # Put this before Ubuntu's noninteractive .bashrc return, without output.
    return block + source


def wrapper_content(marker: str, codex_home: Path, executable: Path, proxy_port: int) -> bytes:
    proxy = f"http://127.0.0.1:{proxy_port}"
    values = {"CODEX_HOME": codex_home.as_posix()}
    values.update({key: proxy for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")})
    values.update({key: "localhost,127.0.0.1,::1" for key in ("NO_PROXY", "no_proxy")})
    assignments = " ".join(shlex.quote(key + "=" + value) for key, value in values.items())
    # No --no-daemon: the desktop app supplies app-server and protocol arguments.
    return (f"#!/usr/bin/env bash\n{marker}\nexec env {assignments} {shlex.quote(executable.as_posix())} \"$@\"\n").encode("utf-8")


def planned_files(home: Path, root: Path, codex_home: Path, executable: Path, proxy_port: int) -> list[dict]:
    marker = OWNER + str(root)
    wrapper = home / ".local/bin/codex"
    validate_target(wrapper, home)
    original = wrapper.read_bytes() if wrapper.exists() else None
    if original is not None:
        try:
            owned = marker in original.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            owned = False
        if not owned:
            raise SystemExit("~/.local/bin/codex 已属于其他程序或安装目录，未修改任何文件。")
    plans = [{"path": wrapper, "before": original, "after": wrapper_content(marker, codex_home, executable, proxy_port), "mode": 0o700, "backup": False}]
    for name in (".profile", ".bashrc"):
        path = home / name
        validate_target(path, home)
        before = path.read_bytes() if path.exists() else None
        try:
            after = profile_content(before.decode("utf-8") if before is not None else "", marker).encode("utf-8")
        except UnicodeDecodeError:
            raise SystemExit(f"{name} 不是 UTF-8 文本，未覆盖任何文件。") from None
        plans.append({"path": path, "before": before, "after": after,
                      "mode": stat.S_IMODE(path.stat().st_mode) if before is not None else 0o600, "backup": True})
    return plans


def proxy_download(fetch, asset: dict, destination: Path, proxy_port: int) -> None:
    proxy = f"http://127.0.0.1:{proxy_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    original_open = fetch.urllib.request.urlopen
    original_bypass = fetch.urllib.request.proxy_bypass
    try:
        # urllib otherwise honors inherited NO_PROXY=* even with ProxyHandler.
        # Limit this override to this one download in this setup process.
        fetch.urllib.request.proxy_bypass = lambda _host: False
        fetch.urllib.request.urlopen = opener.open
        fetch.download_archive(asset, destination)
    finally:
        fetch.urllib.request.urlopen = original_open
        fetch.urllib.request.proxy_bypass = original_bypass


def ensure_complete_package(fetch, asset: dict, root: Path, proxy_port: int) -> bool:
    fetch.ROOT = root
    if fetch.package_is_valid(asset):
        return False
    parent = root / "bin/linux-amd64"
    parent.mkdir(parents=True, exist_ok=True)
    package = fetch.project_path(asset["package_root"])
    if package.exists() and not package.is_dir():
        raise SystemExit("Codex 资源目标不是目录，未覆盖任何文件。")
    with tempfile.TemporaryDirectory(prefix=".desktop-codex-stage-", dir=parent) as folder:
        staging = Path(folder)
        if staging.resolve().parent != parent.resolve():
            raise SystemExit("Codex 临时目录越界。")
        archive = staging / "codex-package.tar.gz"
        print("正在通过现有本地代理下载 Codex 完整资源包（约160MB）……", flush=True)
        proxy_download(fetch, asset, archive, proxy_port)
        print("下载校验通过，正在验证并安装 Codex 配套文件……", flush=True)
        isolated = staging / "installation"
        isolated.mkdir()
        try:
            fetch.ROOT = isolated
            fetch.restore_package(asset, archive, staging)
            if not fetch.package_is_valid(asset):
                raise SystemExit("完整 Codex 资源包验证未通过，未安装接入文件。")
            ready = fetch.project_path(asset["package_root"])
        finally:
            fetch.ROOT = root
        # Existing resources are retained as a backup; runtime data is outside it.
        backup = None
        if package.exists():
            backup = parent / ("codex-package.before-desktop-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
            os.replace(package, backup)
        try:
            os.replace(ready, package)
        except BaseException:
            if backup is not None:
                os.replace(backup, package)
            raise
    return True


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    fd, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install_plans(plans: list[dict], home: Path) -> int:
    changed = [plan for plan in plans if plan["before"] != plan["after"]]
    for plan in changed:
        path = plan["path"]
        validate_target(path, home)
        current = path.read_bytes() if path.exists() else None
        if current != plan["before"]:
            raise SystemExit("接入文件在检查后被其他程序修改，已停止以避免覆盖。")
    for plan in changed:
        path = plan["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        validate_target(path, home)
        if plan["backup"] and plan["before"] is not None:
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            backup = path.with_name(path.name + ".ai-hub-desktop-" + suffix + ".bak")
            atomic_write(backup, plan["before"], 0o600)
        atomic_write(path, plan["after"], plan["mode"])
    return len(changed)


def setup(root: Path, *, home: Path | None = None, bundle_dir: Path | None = None, fetch=None) -> dict:
    root = root.expanduser().resolve(strict=True)
    home = (home or Path.home()).expanduser().resolve(strict=True)
    if "\n" in str(root) or "\r" in str(root):
        raise SystemExit("项目路径不能包含换行符。")
    try:
        runtime = json.loads((root / "data/runtime.json").read_text(encoding="utf-8"))
        proxy_port = runtime["proxy_port"]
    except (OSError, ValueError, KeyError):
        raise SystemExit("未找到有效的本项目 runtime.json；未安装接入文件。") from None
    if type(proxy_port) is not int or not 1 <= proxy_port <= 65535:
        raise SystemExit("本项目代理端口无效。")
    codex_home = Path(runtime.get("codex_home", "data/codex"))
    if not codex_home.is_absolute():
        codex_home = root / codex_home
    codex_home = codex_home.resolve(strict=True)
    if not codex_home.is_relative_to(root) or not codex_home.is_dir():
        raise SystemExit("CODEX_HOME 必须是本项目内既有的授权目录；未改动原 ~/.codex。")
    directory = bundle_dir or Path(__file__).resolve().parent
    manifest = json.loads((directory / "assets-manifest.json").read_text(encoding="utf-8"))
    assets = [asset for asset in manifest["assets"] if asset["name"] == "codex"]
    if len(assets) != 1 or manifest["architecture"] != "linux-amd64":
        raise SystemExit("完整 Codex 资源清单无效。")
    asset = assets[0]
    executable = root / asset["path"]
    plans = planned_files(home, root, codex_home, executable, proxy_port)
    fetch = fetch or load_fetch(directory)
    restored = ensure_complete_package(fetch, asset, root, proxy_port)
    changed = install_plans(plans, home)
    return {"restored": restored, "changed": changed, "wrapper": home / ".local/bin/codex"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare quiet Codex app-server over SSH without changing hub services.")
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise SystemExit("请在 Ubuntu x86_64 服务器上运行这个接入助手。")
    if os.geteuid() == 0:
        raise SystemExit("请使用既有 ubuntu 账号运行，不要加 sudo。")
    result = setup(args.root)
    print("桌面 SSH 接入已就绪；原有授权资料及后台服务保持原样。")
    print("请重新建立 SSH 连接，让该账号加载新的 PATH。")
    print(f"入口：{result['wrapper']}")


if __name__ == "__main__":
    main()

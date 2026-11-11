"""Verify pinned official assets, including the complete Codex local package."""
import gzip
import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
MAX_ARCHIVE = 768 * 1024 * 1024
MAX_FILE = 768 * 1024 * 1024
MAX_PACKAGE = 2 * 1024 * 1024 * 1024


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        raise SystemExit("资源路径无效；已拒绝提取。")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
        raise SystemExit("资源路径越界；已拒绝提取。")
    return path


def project_path(relative: str) -> Path:
    path = ROOT
    for component in safe_relative(relative).parts:
        path = path / component
        if path.is_symlink():
            raise SystemExit("资源路径包含符号链接；已拒绝提取。")
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise SystemExit("资源路径指向项目目录之外；已拒绝提取。")
    return path


def package_files(asset: dict) -> list[tuple[dict, Path, PurePosixPath]]:
    if asset.get("package_root") != "bin/linux-amd64/codex-package":
        raise SystemExit("Codex 完整资源目录清单不匹配。")
    base_relative = safe_relative(asset["package_root"])
    base = project_path(asset["package_root"])
    if base.is_symlink():
        raise SystemExit("Codex 完整资源目录不能是符号链接。")
    entries = asset.get("package_files")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("Codex 清单缺少完整 package_files；不能仅使用主程序。")
    result = []
    seen = set()
    for entry in entries:
        relative = safe_relative(entry["path"])
        if not relative.is_relative_to(base_relative):
            raise SystemExit("Codex 文件清单越界；已拒绝提取。")
        inside = relative.relative_to(base_relative)
        target = project_path(entry["path"])
        if not inside.parts or str(inside) in seen or target.is_symlink():
            raise SystemExit("Codex 文件清单重复、无效或包含符号链接。")
        if not isinstance(entry["size"], int) or not 0 <= entry["size"] <= MAX_FILE:
            raise SystemExit("Codex 文件大小清单无效。")
        seen.add(str(inside))
        result.append((entry, target, inside))
    if sum(entry["size"] for entry, _, _ in result) > MAX_PACKAGE:
        raise SystemExit("Codex 资源清单总大小异常。")
    required = {"bin/codex", "bin/codex-code-mode-host", "codex-package.json", "codex-path/rg", "codex-resources/bwrap"}
    if not required.issubset(seen):
        raise SystemExit("Codex 清单缺少必需的完整包资源；不能仅使用主程序。")
    entrypoint = safe_relative(asset["path"])
    if entrypoint != base_relative / "bin/codex" or str(entrypoint.relative_to(base_relative)) not in seen:
        raise SystemExit("Codex 入口未包含在完整文件清单中。")
    return result


def package_is_valid(asset: dict) -> bool:
    entries = package_files(asset)
    for entry, target, _ in entries:
        if not target.is_file() or target.stat().st_size != entry["size"] or file_checksum(target) != entry["sha256"]:
            return False
    for entry, target, _ in entries:
        target.chmod(0o700 if entry.get("executable") else 0o600)
    return True


def restore_package(asset: dict, archive_path: Path, staging: Path) -> None:
    entries = package_files(asset)
    entry_inside = safe_relative(asset["path"]).relative_to(safe_relative(asset["package_root"]))
    archive_entry = safe_relative(asset["archive_member"])
    count = len(entry_inside.parts)
    if archive_entry.parts[-count:] != entry_inside.parts:
        raise SystemExit("Codex 发行包入口映射不匹配。")
    prefix = archive_entry.parts[:-count]
    expected = {str(PurePosixPath(*prefix, *inside.parts)): (entry, target, inside) for entry, target, inside in entries}
    staged = staging / "package"
    staged.mkdir()
    found = set()
    total = 0
    with tarfile.open(archive_path, mode="r:gz") as bundle:
        for member in bundle:
            name = str(safe_relative(member.name))
            if member.isdir():
                continue
            if not member.isfile() or member.issym() or member.islnk():
                raise SystemExit("Codex 发行包包含链接或特殊文件；已拒绝提取。")
            if name not in expected or name in found:
                raise SystemExit("Codex 发行包包含未列入清单或重复的文件；已拒绝提取。")
            entry, _, inside = expected[name]
            if member.size != entry["size"] or member.size > MAX_FILE:
                raise SystemExit("Codex 发行包文件大小不匹配。")
            total += member.size
            if total > MAX_PACKAGE:
                raise SystemExit("Codex 发行包解压大小异常。")
            output_path = staged.joinpath(*inside.parts)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit("Codex 发行包文件无法读取。")
            with source, output_path.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if file_checksum(output_path) != entry["sha256"]:
                raise SystemExit("Codex 发行包文件 SHA-256 不匹配。")
            found.add(name)
    if found != set(expected):
        raise SystemExit("Codex 发行包缺少清单资源；未更新已有文件。")
    # Publish only after every resource passed validation. No runtime data is touched.
    for entry, target, inside in entries:
        target.parent.mkdir(parents=True, exist_ok=True)
        output_path = staged.joinpath(*inside.parts)
        output_path.chmod(0o700 if entry.get("executable") else 0o600)
        os.replace(output_path, target)


def download_archive(asset: dict, destination: Path) -> None:
    url = asset["url"]
    parsed = urlparse(url)
    repositories = {"mihomo": "MetaCubeX/mihomo", "cli-proxy-api": "router-for-me/CLIProxyAPI", "codex": "openai/codex"}
    expected_prefix = f"/{repositories[asset['name']]}/releases/download/{asset['version']}/"
    if parsed.scheme != "https" or parsed.hostname != "github.com" or not parsed.path.startswith(expected_prefix):
        raise SystemExit("只允许清单中的 GitHub 官方固定版本发行包地址。")
    digest = hashlib.sha256()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": "ai-proxy-hub/1.0.0"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > MAX_ARCHIVE:
                raise SystemExit("发行包下载大小异常；已拒绝运行。")
            output.write(block)
            digest.update(block)
    if digest.hexdigest() != asset["archive_sha256"]:
        raise SystemExit(f"{asset['name']} 发行包 SHA-256 不匹配；已拒绝运行。")


def restore_binary(asset: dict, archive_path: Path, staging: Path) -> None:
    target = project_path(asset["path"])
    if target.is_symlink():
        raise SystemExit("发行包二进制目标不能是符号链接。")
    output_path = staging / "binary"
    member_name = asset.get("archive_member")
    if member_name:
        with tarfile.open(archive_path, mode="r:gz") as bundle:
            member = bundle.getmember(member_name)
            safe_relative(member.name)
            if not member.isfile() or member.size > MAX_FILE:
                raise SystemExit("发行包目标文件异常。")
            source = bundle.extractfile(member)
            assert source is not None
            with source, output_path.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    else:
        with gzip.open(archive_path, "rb") as source, output_path.open("wb") as output:
            size = 0
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > MAX_FILE:
                    raise SystemExit("二进制解压大小异常。")
                output.write(block)
    if file_checksum(output_path) != asset["binary_sha256"]:
        raise SystemExit(f"{asset['name']} 二进制 SHA-256 不匹配；已拒绝运行。")
    target.parent.mkdir(parents=True, exist_ok=True)
    output_path.chmod(0o700)
    os.replace(output_path, target)


def main() -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise SystemExit("本部署包目标平台是 Ubuntu 24.04 x86_64；拒绝自动安装其他架构。")
    manifest = json.loads((ROOT / "deploy/assets-manifest.json").read_text(encoding="utf-8"))
    if manifest["architecture"] != "linux-amd64":
        raise SystemExit("二进制清单架构不匹配。")
    destination = ROOT / "bin/linux-amd64"
    destination.mkdir(parents=True, exist_ok=True)
    for asset in manifest["assets"]:
        name = asset["name"]
        if name not in {"mihomo", "cli-proxy-api", "codex"}:
            raise SystemExit("二进制清单包含未知文件。")
        if name == "codex":
            valid = package_is_valid(asset)
        else:
            if asset["path"] != f"bin/linux-amd64/{name}":
                raise SystemExit("二进制目标目录不匹配。")
            target = project_path(asset["path"])
            valid = target.is_file() and not target.is_symlink() and file_checksum(target) == asset["binary_sha256"]
            if valid:
                target.chmod(0o700)
        if valid:
            print(f"{name}: {'完整资源' if name == 'codex' else '本地'}校验通过")
            continue
        print(f"下载 {name} {asset['version']} 官方发行包……")
        with tempfile.TemporaryDirectory(prefix=f".{name}-restore-", dir=destination) as folder:
            staging = Path(folder)
            if staging.resolve().parent != destination.resolve():
                raise SystemExit("临时恢复目录越界。")
            archive = staging / "asset.tar.gz"
            download_archive(asset, archive)
            if name == "codex":
                restore_package(asset, archive, staging)
            else:
                restore_binary(asset, archive, staging)
        print(f"{name}: 下载并校验通过")


if __name__ == "__main__":
    main()

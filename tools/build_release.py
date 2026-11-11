"""Overlay reviewed source on a clean upstream runtime archive; emit a pinned release."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "ai-proxy-hub-1.0.0"
FORBIDDEN = {"data", "backups", ".venv", "__pycache__", ".ssh", ".codex", ".git"}
PRIVATE_MARKERS = (b"PRIVATE KEY-----", b"ghp_", b"gho_")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-archive", required=True, type=Path)
    parser.add_argument("--tag", default="v0.1.0")
    args = parser.parse_args()
    output = ROOT / ".dist" / (PREFIX + ".zip")
    output.parent.mkdir(exist_ok=True)
    overlay = {
        path.relative_to(ROOT / "payload").as_posix(): path
        for path in (ROOT / "payload").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    if "deploy/setup-desktop-ssh.py" not in overlay:
        raise SystemExit("Desktop SSH helper must be included in the reviewed source.")
    seen = set()
    with zipfile.ZipFile(args.base_archive) as source, zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED, compresslevel=6
    ) as target:
        for entry in source.infolist():
            name = PurePosixPath(entry.filename)
            if name.is_absolute() or ".." in name.parts or name.parts[0] != PREFIX:
                raise SystemExit("Unsafe archive path.")
            if any(part in FORBIDDEN for part in name.parts):
                raise SystemExit("Runtime/private material must not enter a release.")
            if stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise SystemExit("Archive links and special files are forbidden.")
            relative = name.relative_to(PREFIX).as_posix()
            if relative.startswith("tests/") or relative in overlay:
                continue
            if entry.is_dir():
                continue
            if entry.filename in seen:
                raise SystemExit("Duplicate archive entry.")
            seen.add(entry.filename)
            with source.open(entry) as incoming, target.open(entry, "w") as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        for relative, path in sorted(overlay.items()):
            content = path.read_bytes().replace(b"\r\n", b"\n")
            if any(marker in content for marker in PRIVATE_MARKERS):
                raise SystemExit("Private deployment material found in " + relative)
            info = zipfile.ZipInfo(PREFIX + "/" + relative)
            info.create_system = 3
            info.external_attr = (0o100700 if relative.endswith(".sh") else 0o100600) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, content)
    manifest = json.loads((ROOT / "payload/deploy/assets-manifest.json").read_text(encoding="utf-8"))
    entries = manifest["assets"] + manifest["wheels"] + manifest.get("licenses", [])
    entries += [item for asset in manifest["assets"] for item in asset.get("package_files", [])]
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise SystemExit("Archive CRC validation failed.")
        for entry in entries:
            with archive.open(PREFIX + "/" + entry["path"]) as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != entry["sha256"]:
                    raise SystemExit("Upstream artifact hash mismatch: " + entry["path"])
        for entry in archive.infolist():
            if Path(entry.filename).suffix in {".md", ".py", ".sh", ".json", ".html", ".js", ".css", ".txt"} and entry.file_size < 2_000_000:
                if any(marker in archive.read(entry) for marker in PRIVATE_MARKERS):
                    raise SystemExit("Private marker in " + entry.filename)
    digest = sha256(output)
    release = {
        "schema_version": 1, "repository": "Jieniljienl/codex-server-bootstrap",
        "tag": args.tag, "asset": output.name, "sha256": digest, "root": PREFIX,
    }
    (ROOT / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    print(json.dumps({"asset": output.name, "bytes": output.stat().st_size, "sha256": digest}))


if __name__ == "__main__":
    main()

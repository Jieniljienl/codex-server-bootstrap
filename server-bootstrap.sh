#!/usr/bin/env bash
# Root provisions an unprivileged service account; Codex never runs as root.
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RELEASE_NAME=''
RELEASE_SHA256=''
release_file="$SCRIPT_DIR/release.json"
offline=false
mode=''; archive=''; supplied_sha=''; wheels_dir="$SCRIPT_DIR/wheels-ubuntu2604"; service_user='codexsvc'
account_exists=false; existing=false
python_bin='/usr/bin/python3'
user_path='/usr/bin:/bin:/usr/local/bin'
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage:
  bash server-bootstrap.sh --check [--service-user codexsvc]
  bash server-bootstrap.sh --install --archive /absolute/path/ai-proxy-hub-1.0.0.zip [--sha256 SHA256]
       [--wheels-dir /absolute/path/wheels-ubuntu2604] [--service-user codexsvc] [--offline]
Supports Ubuntu 24.04 / CPython 3.12 and Ubuntu 26.04 / CPython 3.14, x86_64.
Ubuntu 26.04 requires the supplied, checksum-pinned CPython 3.14 wheels.
Root creates codexsvc without sudo or copied SSH keys. A normal user installs in
their own home and uses sudo for prerequisites. --check makes no system changes.
USAGE
}
while (($#)); do
  case "$1" in
    --check|--install)
      [[ -z "$mode" ]] || die 'Specify exactly one mode.'; mode="$1"; shift;;
    --archive|--sha256|--wheels-dir|--service-user)
      [[ $# -ge 2 ]] || die "Missing value for $1."
      case "$1" in
        --archive) archive="$2";; --sha256) supplied_sha="$2";;
        --wheels-dir) wheels_dir="$2";; --service-user) service_user="$2";;
      esac
      shift 2;;
    --offline) offline=true; shift;;
    -h|--help) usage; exit 0;;
    *) usage; die "Unknown option: $1";;
  esac
done
[[ -n "$mode" ]] || { usage; exit 2; }

# Read-only preflight.
printf 'Kernel: %s; architecture: %s; SSH user: %s (uid %s)\n' "$(uname -s)" "$(uname -m)" "$(id -un)" "$EUID"
[[ -r /etc/os-release ]] || die 'Cannot read /etc/os-release.'
source /etc/os-release
printf 'Operating system: %s\n' "${PRETTY_NAME:-unknown}"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || die 'Requires Linux x86_64.'
[[ "${ID:-}" == ubuntu && ( "${VERSION_ID:-}" == 24.04 || "${VERSION_ID:-}" == 26.04 ) ]] || die 'Supports Ubuntu 24.04 and 26.04 only; the OS will not be reinstalled or upgraded.'
if [[ ! -x "$python_bin" ]]; then
  if [[ "$mode" == --check ]]; then
    printf '%s\n' 'System Python is missing; basic OS check passed. Full application checks require prerequisites from install.sh.'
    exit 0
  fi
  die 'System Python is missing; run the top-level install.sh to provision prerequisites first.'
fi
python_version="$($python_bin -c 'import sys; print("%s.%s" % sys.version_info[:2])')"
printf 'System Python: %s (%s)\n' "$python_version" "$python_bin"
release_values="$("$python_bin" - "$release_file" <<'PY'
import json, re, sys
from pathlib import Path
path = Path(sys.argv[1])
if path.is_symlink() or not path.is_file(): raise SystemExit("Missing or unsafe release.json.")
data = json.loads(path.read_text(encoding="utf-8-sig"))
if data.get("schema_version") != 1 or data.get("repository") != "Jieniljienl/codex-server-bootstrap": raise SystemExit("Unexpected release repository/schema.")
if not isinstance(data.get("tag"), str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?", data["tag"]): raise SystemExit("Invalid release tag.")
if data.get("asset") != "ai-proxy-hub-1.0.0.zip" or data.get("root") != "ai-proxy-hub-1.0.0": raise SystemExit("Invalid release asset/root.")
if not isinstance(data.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]): raise SystemExit("Invalid release SHA-256.")
print(data["root"])
print(data["sha256"])
PY
)"
mapfile -t release_fields <<< "$release_values"
RELEASE_NAME="${release_fields[0]}"
RELEASE_SHA256="${release_fields[1]}"
[[ ( "$VERSION_ID" == 24.04 && "$python_version" == 3.12 ) || ( "$VERSION_ID" == 26.04 && "$python_version" == 3.14 ) ]] || die 'Unexpected OS/Python combination; offline wheels would not match.'
for dependency in systemctl systemd-analyze loginctl sha256sum dpkg-query getent stat; do
  command -v "$dependency" >/dev/null || die "Required command missing: $dependency"
done
[[ -d /run/systemd/system ]] || die 'A systemd-based Ubuntu host is required.'
if [[ "$EUID" -eq 0 ]]; then
  [[ "$service_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ && "$service_user" != root ]] || die 'Invalid service account name.'
  for dependency in runuser useradd install; do command -v "$dependency" >/dev/null || die "Missing: $dependency"; done
  systemctl show-environment >/dev/null 2>&1 || die 'Cannot contact system systemd.'
  target_user="$service_user"; target_home="/home/$target_user"
  account_marker="/var/lib/codex-vpn-bootstrap/accounts/$target_user"
  if passwd_entry="$(getent passwd "$target_user")"; then
    account_exists=true; target_uid="$(id -u "$target_user")"
    actual_home="$(printf '%s' "$passwd_entry" | cut -d: -f6)"
    [[ "$target_uid" -ne 0 && "$actual_home" == "$target_home" ]] || die 'Existing account does not match the dedicated service account layout.'
    [[ -f "$account_marker" && ! -L "$account_marker" && "$(stat -c %u "$account_marker")" == 0 ]] || die 'Service account exists without a root-owned bootstrap marker; it will not be adopted.'
    [[ "$(cat -- "$account_marker")" == "$RELEASE_SHA256:$target_uid:$target_home" ]] || die 'Service account marker mismatch.'
    [[ -d "$target_home" && ! -L "$target_home" && "$(stat -c %u "$target_home")" == "$target_uid" ]] || die 'Service account home is missing, linked or owned by another user.'
    printf 'Managed service account: %s (uid %s)\n' "$target_user" "$target_uid"
  else
    target_uid=''
    [[ ! -e "$target_home" && ! -L "$target_home" ]] || die 'Service account home already exists; it will not be adopted.'
    [[ ! -e "$account_marker" && ! -L "$account_marker" ]] || die 'Stale account marker; inspect it before retrying.'
    printf 'Planned account: %s, home %s (no sudo or copied SSH keys).\n' "$target_user" "$target_home"
  fi
else
  command -v sudo >/dev/null || die 'The SSH user needs sudo for prerequisites and linger.'
  target_user="$(id -un)"; target_uid="$EUID"; account_exists=true
  [[ -n "${HOME:-}" && -d "$HOME" && -O "$HOME" ]] || die 'HOME must belong to the SSH user.'
  target_home="$(cd -- "$HOME" && pwd -P)"
  systemctl --user show-environment >/dev/null 2>&1 || die 'No systemd user session; log in directly as the target SSH user.'
  if sudo -n -l >/dev/null 2>&1; then printf '%s\n' 'Sudo policy query succeeded; installation may request a password.'; else printf '%s\n' 'Sudo authentication/policy confirmation is required during installation.'; fi
fi
target="$target_home/$RELEASE_NAME"; marker="$target/.bootstrap-release-sha256"
printf 'Install directory: %s\n' "$target"
as_user() {
  if [[ "$EUID" -eq 0 ]]; then
    ( cd /; runuser -u "$target_user" -- env -i HOME="$target_home" USER="$target_user" LOGNAME="$target_user" \
      PATH="$user_path" LANG=C.UTF-8 TERM="${TERM:-xterm}" XDG_RUNTIME_DIR="/run/user/$target_uid" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$target_uid/bus" "$@" )
  else env PATH="$user_path" "$@"; fi
}
privileged() { if [[ "$EUID" -eq 0 ]]; then "$@"; else sudo "$@"; fi; }
if [[ -e "$target" || -L "$target" ]]; then
  [[ -n "$target_uid" && -d "$target" && ! -L "$target" && "$(stat -c %u "$target")" == "$target_uid" ]] || die 'Target is not an ordinary directory owned by the installation account.'
  [[ -f "$marker" && ! -L "$marker" && "$(stat -c %u "$marker")" == "$target_uid" ]] || die 'Target exists without this bootstrap marker; nothing will be overwritten.'
  [[ "$(cat -- "$marker")" == "$RELEASE_SHA256" ]] || die 'Existing installation release mismatch.'
  for file in deploy/install.sh deploy/preflight.py deploy/common.sh; do
    [[ -f "$target/$file" && ! -L "$target/$file" && "$(stat -c %u "$target/$file")" == "$target_uid" ]] || die "Existing file missing or unsafe: $file"
  done
  existing=true
  printf '%s\n' 'Existing bootstrap-managed installation; source and runtime data will be preserved.'
fi
if "$account_exists"; then
  for unit in ai-hub-mihomo.service ai-hub-cliproxy.service ai-hub-web.service; do
    user_unit="$target_home/.config/systemd/user/$unit"
    if [[ -e "$user_unit" || -L "$user_unit" ]]; then
      [[ -f "$user_unit" ]] && grep -Fqx -- "# AI_PROXY_HUB_ROOT=$target" "$user_unit" || die "Service file belongs to another installation: $unit"
    fi
    if as_user systemctl --user show-environment >/dev/null 2>&1; then
      unit_path="$(as_user systemctl --user show "$unit" --property=FragmentPath --value 2>/dev/null || true)"
      load_state="$(as_user systemctl --user show "$unit" --property=LoadState --value 2>/dev/null || true)"
      if [[ -n "$unit_path" ]]; then
        [[ -f "$unit_path" ]] && grep -Fqx -- "# AI_PROXY_HUB_ROOT=$target" "$unit_path" || die "Loaded service belongs to another installation: $unit"
      elif [[ "$load_state" == loaded ]]; then die "Cannot verify loaded service: $unit"; fi
    fi
  done
fi
if "$existing"; then
  as_user "$python_bin" "$target/deploy/preflight.py" --root "$target"
else
  "$python_bin" - <<'PY'
import socket
for port in (8090, 17890, 19090, 18317):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try: sock.bind(('0.0.0.0', port))
        except OSError: raise SystemExit(f'Port {port} unavailable; no process was stopped.')
print('Default service ports are available.')
PY
fi
missing=()
for package in python3-venv unzip iproute2 ca-certificates dbus-user-session; do
  if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != 'install ok installed' ]]; then missing+=("$package"); fi
done
if ((${#missing[@]})); then printf 'Missing OS packages (installed only in --install): %s\n' "${missing[*]}"; else printf '%s\n' 'Required OS packages are installed.'; fi
if [[ "$python_version" == 3.14 ]]; then printf '%s\n' 'CPython 3.14: installation requires the pinned supplemental wheels directory.'; fi
if [[ "$mode" == --check ]]; then printf '%s\n' 'Read-only preflight passed; no users, packages, files or services were changed.'; exit 0; fi

[[ "$archive" == /* && -f "$archive" && ! -L "$archive" && -r "$archive" ]] || die '--archive must be an absolute readable regular ZIP.'
if [[ -n "$supplied_sha" ]]; then
  [[ "$supplied_sha" =~ ^[[:xdigit:]]{64}$ && "${supplied_sha,,}" == "$RELEASE_SHA256" ]] || die '--sha256 must match release.json.'
fi
actual_sha="$(sha256sum -- "$archive")"
[[ "${actual_sha%% *}" == "$RELEASE_SHA256" ]] || die 'ZIP SHA-256 mismatch.'
printf '%s\n' 'Offline release SHA-256 verified.'
"$python_bin" - "$archive" "$RELEASE_NAME" <<'PY'
import stat, sys, zipfile
from pathlib import PurePosixPath
archive, root = sys.argv[1:]; seen = set()
with zipfile.ZipFile(archive) as z:
    for item in z.infolist():
        name = item.filename; path = PurePosixPath(name); mode = item.external_attr >> 16
        if ('\\' in name or '\x00' in name or path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != root):
            raise SystemExit('Unsafe ZIP path; nothing extracted.')
        if name in seen: raise SystemExit('Duplicate ZIP entry.')
        seen.add(name)
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR): raise SystemExit('ZIP links/special files forbidden.')
        if len(path.parts) > 1 and path.parts[1] in ('data', 'backups', '.venv'): raise SystemExit('ZIP contains runtime data; use the clean release.')
    for required in ('deploy/install.sh', 'deploy/common.sh', 'deploy/preflight.py', 'deploy/setup-desktop-ssh.py'):
        if root + '/' + required not in seen: raise SystemExit('Incomplete release ZIP.')
print('ZIP paths and entry types verified.')
PY
if [[ "$python_version" == 3.14 ]]; then
  [[ "$wheels_dir" == /* && -d "$wheels_dir" && ! -L "$wheels_dir" ]] || die 'Ubuntu 26.04 requires --wheels-dir with the supplied CPython 3.14 wheels.'
  "$python_bin" - "$wheels_dir" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
expected = {
 'markupsafe-3.0.4-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl': 'a8e9f292fcda89b324f2f5c91d13f1424a153e40fc2756f38ee23b15835ff300',
 'pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl': 'c458b6d084f9b935061bc36216e8a69a7e293a2f1e68bf956dcd9e6cbcd143f5',
}
manifest = root / 'manifest.json'
if manifest.is_symlink() or not manifest.is_file(): raise SystemExit('Missing/unsafe wheel manifest.')
data = json.loads(manifest.read_text(encoding='utf-8-sig')); entries = data.get('files', [])
if not isinstance(entries, list) or len(entries) != len(expected): raise SystemExit('Unexpected manifest entries.')
if {e['filename']: e['sha256'] for e in entries} != expected: raise SystemExit('Manifest does not match pinned wheel filenames/hashes.')
for name, digest in expected.items():
    path = root / name
    if path.is_symlink() or not path.is_file(): raise SystemExit('Missing/unsafe supplemental wheel.')
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest: raise SystemExit('Supplemental wheel SHA-256 mismatch.')
print('CPython 3.14 wheel hashes verified independently of the manifest.')
PY
fi
[[ -t 0 && -t 1 ]] || die 'Installation requires an interactive terminal for sudo/web password; use ssh -t.'
if [[ "$EUID" -ne 0 ]]; then sudo -v; fi
if ((${#missing[@]})); then
  if "$offline"; then die "Offline mode: missing OS packages: ${missing[*]}"; fi
  printf '%s\n' 'Installing missing OS prerequisites (no system upgrade).'
  privileged apt-get update
  privileged apt-get install -y --no-install-recommends "${missing[@]}"
fi
if [[ "$EUID" -eq 0 && "$account_exists" == false ]]; then
  for path in /var/lib/codex-vpn-bootstrap /var/lib/codex-vpn-bootstrap/accounts; do
    [[ ! -L "$path" ]] || die 'Account registry path must not be a symlink.'
    if [[ -e "$path" ]]; then [[ -d "$path" && "$(stat -c %u "$path")" == 0 ]] || die 'Unexpected account registry ownership.'; fi
  done
  install -d -m 700 -o root -g root /var/lib/codex-vpn-bootstrap/accounts
  useradd --create-home --home-dir "$target_home" --shell /bin/bash --user-group "$target_user"
  target_uid="$(id -u "$target_user")"
  [[ -d "$target_home" && ! -L "$target_home" && "$(stat -c %u "$target_home")" == "$target_uid" ]] || die 'Created account home has unexpected ownership.'
  printf '%s\n' "$RELEASE_SHA256:$target_uid:$target_home" > "$account_marker"
  chmod 600 "$account_marker"; account_exists=true
  printf 'Created account %s without sudo permissions or copied SSH keys.\n' "$target_user"
fi
if ! "$existing"; then
  staging="$(mktemp -d "$target_home/.ai-hub-unpack.XXXXXXXX")"
  trap 'printf "Installation stopped. Staging directory, if present: %s\n" "$staging" >&2' ERR
  unzip -q "$archive" -d "$staging"
  [[ -d "$staging/$RELEASE_NAME" && ! -L "$staging/$RELEASE_NAME" ]] || die 'Unexpected extracted layout.'
  printf '%s\n' "$RELEASE_SHA256" > "$staging/$RELEASE_NAME/.bootstrap-release-sha256"
  [[ ! -e "$target" && ! -L "$target" ]] || die 'Target appeared during extraction; nothing overwritten.'
  mv -T -n -- "$staging/$RELEASE_NAME" "$target"
  [[ ! -e "$staging/$RELEASE_NAME" ]] || die 'Concurrent target creation; extracted files remain in staging.'
  rmdir -- "$staging"; staging=''; trap - ERR
  if [[ "$EUID" -eq 0 ]]; then chown -R "$target_user:$target_user" -- "$target"; fi
fi

# Exact, idempotent compatibility patch; original ZIP and runtime data stay intact.
as_user "$python_bin" - "$target" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]); path = root / 'deploy/install.sh'; text = path.read_text(encoding='utf-8')
old = '[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]]'
new = '[[ "$ID" == ubuntu && ( "$VERSION_ID" == 24.04 || "$VERSION_ID" == 26.04 ) ]]'
if old not in text and new not in text: raise SystemExit('Installer OS check differs from expected release; no patch applied.')
if old in text and text.count(old) != 1: raise SystemExit('Ambiguous OS check; no patch applied.')
text = text.replace(old, new).replace('python3.12-venv', 'python3-venv')
text = text.replace('此包目标平台是 Ubuntu 24.04 x86_64', '此包目标平台是 Ubuntu 24.04/26.04 x86_64')
text = text.replace('此包目标系统是 Ubuntu 24.04；其他系统未验证', '此包目标系统是 Ubuntu 24.04 或 26.04；其他系统未验证')
path.write_text(text, encoding='utf-8', newline='\n')
(root / '.bootstrap-ubuntu-compat-v1').write_text('Ubuntu 24.04 / Python 3.12; Ubuntu 26.04 / Python 3.14\n', encoding='utf-8')
fetch_path = root / 'deploy/fetch-binaries.py'
fetch_source = fetch_path.read_text(encoding='utf-8')
signature = 'def download_archive(asset: dict, destination: Path) -> None:\n'
guard = '    if os.environ.get("AI_HUB_OFFLINE") == "1":\n        raise SystemExit("Offline mode: a required binary is missing or corrupt; restore the verified release archive.")\n'
if guard not in fetch_source:
    if fetch_source.count(signature) != 1: raise SystemExit('Unexpected binary fetcher; offline compatibility patch stopped.')
    fetch_path.write_text(fetch_source.replace(signature, signature + guard), encoding='utf-8', newline='\n')
print('Applied/verified the Ubuntu 24.04/26.04 compatibility patch.')
PY
if [[ "$python_version" == 3.14 ]]; then
  for filename in \
    markupsafe-3.0.4-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl \
    pyyaml-6.0.3-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl; do
    [[ -d "$target/wheels" && ! -L "$target/wheels" && ! -L "$target/wheels/$filename" ]] || die 'Unsafe supplemental wheel destination.'
    case "$filename" in
      markupsafe-*) wheel_sha='a8e9f292fcda89b324f2f5c91d13f1424a153e40fc2756f38ee23b15835ff300';;
      pyyaml-*) wheel_sha='c458b6d084f9b935061bc36216e8a69a7e293a2f1e68bf956dcd9e6cbcd143f5';;
    esac
    # Root only reads the upload; all writes under the service user's tree are unprivileged.
    cat -- "$wheels_dir/$filename" | as_user "$python_bin" -c '
import hashlib, os, sys, tempfile
from pathlib import Path
dest = Path(sys.argv[1]); expected = sys.argv[2]; data = sys.stdin.buffer.read()
if hashlib.sha256(data).hexdigest() != expected: raise SystemExit("Supplemental wheel changed after validation.")
if dest.is_symlink() or dest.parent.is_symlink() or not dest.parent.is_dir(): raise SystemExit("Unsafe wheel destination.")
fd, name = tempfile.mkstemp(prefix=".wheel-", dir=dest.parent)
try:
    with os.fdopen(fd, "wb") as out: out.write(data)
    os.replace(name, dest)
finally:
    if os.path.exists(name): os.unlink(name)
' "$target/wheels/$filename" "$wheel_sha"
  done
fi
privileged loginctl enable-linger "$target_user"
if [[ "$EUID" -eq 0 ]]; then systemctl start "user@$target_uid.service"; fi
as_user systemctl --user show-environment >/dev/null 2>&1 || die 'The target systemd user bus did not become available.'
install_env=()
if "$offline"; then install_env=(env AI_HUB_OFFLINE=1 PIP_NO_INDEX=1); fi
if [[ -f "$target/data/runtime.json" ]]; then as_user "${install_env[@]}" bash "$target/deploy/install.sh";
else as_user "${install_env[@]}" bash "$target/deploy/install.sh" --bind 127.0.0.1; fi
# Installs the proxy-aware Codex launcher in the account's normal SSH PATH.
[[ -f "$target/deploy/setup-desktop-ssh.py" && ! -L "$target/deploy/setup-desktop-ssh.py" ]] || die 'Release is missing desktop SSH integration.'
# First login normally creates data/codex, but desktop SSH setup runs before login.
# Prepare only the default directory, as its owner, without changing existing auth.
as_user "$python_bin" - "$target" <<'PY-CODEX-HOME'
import json, stat, sys
from pathlib import Path

root = Path(sys.argv[1])
data = root / 'data'
for directory in (root, data):
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise SystemExit('Codex authorization parent must be an ordinary directory, not a link.')
runtime = json.loads((data / 'runtime.json').read_text(encoding='utf-8'))
configured = Path(runtime.get('codex_home', 'data/codex'))
if not configured.is_absolute():
    configured = root / configured
codex_home = data / 'codex'
if configured != codex_home:
    # Custom CODEX_HOME follows the existing helper's strict validation policy.
    raise SystemExit(0)
try:
    codex_home.mkdir(mode=0o700)
except FileExistsError:
    if not stat.S_ISDIR(codex_home.lstat().st_mode):
        raise SystemExit('Codex authorization path must be an ordinary directory, not a link.')
PY-CODEX-HOME
as_user "${install_env[@]}" "$python_bin" "$target/deploy/setup-desktop-ssh.py" --root "$target"
as_user "$target/bin/mihomo" -v
as_user "$target/bin/codex" --version
as_user "$target/.venv/bin/python" -c 'import flask, yaml, waitress; print("Python dependencies import successfully.")'
as_user systemctl --user is-active ai-hub-mihomo.service ai-hub-cliproxy.service ai-hub-web.service
printf '\n%s\n' 'Installation finished. Active services do not prove proxy connectivity or Codex authentication.'
printf 'Service user: %s; Codex launcher: %s/codex-vpn\n' "$target_user" "$target"
printf '%s\n' 'Next: use an SSH tunnel for the web UI, import your subscription and select a working node.'
if [[ "$EUID" -eq 0 ]]; then printf 'Switch to the service account: su - %s\n' "$target_user"; fi
printf 'Then run: bash %q/deploy/codex-login.sh\n' "$target"
printf 'Start Codex from your project: %q/codex-vpn\n' "$target"
printf '%s\n' 'Complete device-code authorization in your browser. CLIProxyAPI login is separate and optional for Codex CLI.'

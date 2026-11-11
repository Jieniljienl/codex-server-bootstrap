#!/usr/bin/env bash
# Public entry point. Run from a downloaded or cloned checkout using sudo/root.
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
mode=install; offline=false; archive=''; service_user=codexsvc
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Usage:
  sudo bash install.sh
  bash install.sh --check
  sudo bash install.sh --offline --archive /absolute/path/ai-proxy-hub-1.0.0.zip
Options:
  --check                 Read-only prerequisite and compatibility checks.
  --archive PATH          Use an existing release ZIP instead of downloading it.
  --offline               Do not download artifacts or use apt/pip online sources.
                          Missing OS packages must already be installed.
  --service-user NAME     Dedicated non-sudo account (default: codexsvc).
  --help                  Show this help.

Supported: Ubuntu 24.04 or 26.04 x86_64 cloud/VM images with systemd and apt.
The installer provisions Python, venv, unzip, curl, CA certificates, iproute2,
dbus user sessions, Git and SSH server packages when missing. It does not
reinstall the OS, change routes/firewall rules, or expose a Codex app-server.
USAGE
}
while (($#)); do
  case "$1" in
    --check) mode=check; shift;;
    --offline) offline=true; shift;;
    --archive|--service-user)
      [[ $# -ge 2 ]] || die "Missing value for $1."
      if [[ "$1" == --archive ]]; then archive="$2"; else service_user="$2"; fi
      shift 2;;
    --help|-h) usage; exit 0;;
    *) usage; die "Unknown option: $1";;
  esac
done
[[ "$service_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ && "$service_user" != root ]] || die 'Invalid dedicated service account name.'
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || die 'Requires Linux x86_64.'
[[ -r /etc/os-release ]] || die 'Cannot read /etc/os-release.'
source /etc/os-release
printf 'Operating system: %s\n' "${PRETTY_NAME:-unknown}"
[[ "${ID:-}" == ubuntu && ( "${VERSION_ID:-}" == 24.04 || "${VERSION_ID:-}" == 26.04 ) ]] || die 'Only Ubuntu 24.04 and 26.04 are supported.'
[[ -d /run/systemd/system ]] || die 'A running systemd host is required; this installer does not target ordinary Docker containers.'
for cmd in apt-get dpkg-query systemctl sha256sum; do command -v "$cmd" >/dev/null || die "Cloud image prerequisite missing: $cmd"; done
systemctl show-environment >/dev/null 2>&1 || die 'Cannot contact system systemd.'
[[ -f "$SCRIPT_DIR/server-bootstrap.sh" && ! -L "$SCRIPT_DIR/server-bootstrap.sh" ]] || die 'Missing core installer; download/clone the complete repository.'
[[ -f "$SCRIPT_DIR/release.json" && ! -L "$SCRIPT_DIR/release.json" ]] || die 'Missing release.json; download/clone the complete repository.'

missing=()
for package in curl ca-certificates python3 python3-venv unzip iproute2 dbus-user-session git openssh-server; do
  if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != 'install ok installed' ]]; then missing+=("$package"); fi
done
if ((${#missing[@]})); then printf 'Missing OS packages: %s\n' "${missing[*]}"; else printf '%s\n' 'Base OS packages are installed.'; fi
if [[ -x /usr/bin/python3 ]]; then
  /usr/bin/python3 -c 'import sys; print("System Python:", sys.version.split()[0])'
else
  printf '%s\n' 'System Python is absent and will be provisioned during installation.'
fi
if [[ "$mode" == check ]]; then
  if ((${#missing[@]} == 0)) && [[ "$EUID" -eq 0 ]]; then
    bash "$SCRIPT_DIR/server-bootstrap.sh" --check --service-user "$service_user"
  else
    printf 'Target account: %s. Full account/port checks run after prerequisites are available (or rerun --check with sudo).\n' "$service_user"
  fi
  printf '%s\n' 'Read-only prerequisite check complete; no users, packages, files or services were changed.'
  exit 0
fi
[[ "$EUID" -eq 0 ]] || die 'Run this installer with sudo bash install.sh (or from an existing root terminal).'
[[ -t 0 && -t 1 ]] || die 'An interactive terminal is required for the new web password; use an SSH terminal with -t.'
if "$offline" && ((${#missing[@]})); then die "Offline mode requires these packages to be installed first: ${missing[*]}"; fi
if ((${#missing[@]})); then
  printf '%s\n' 'Installing missing prerequisites; existing packages are not upgraded by an upgrade command.'
  apt-get update
  apt-get install -y --no-install-recommends "${missing[@]}"
fi
if ! systemctl is-active --quiet ssh.service && ! systemctl is-active --quiet ssh.socket; then
  printf '%s\n' 'Neither SSH service nor socket is active; enabling the existing SSH service configuration.'
  systemctl enable --now ssh.service
fi

# Treat release metadata as data. Never source it or accept an arbitrary download URL.
release_values="$(/usr/bin/python3 - "$SCRIPT_DIR/release.json" <<'PY'
import json, re, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
if data.get('schema_version') != 1 or data.get('repository') != 'Jieniljienl/codex-server-bootstrap':
    raise SystemExit('Unexpected release repository/schema.')
if not isinstance(data.get('tag'), str) or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?', data['tag']):
    raise SystemExit('Invalid release tag.')
if data.get('asset') != 'ai-proxy-hub-1.0.0.zip' or data.get('root') != 'ai-proxy-hub-1.0.0':
    raise SystemExit('Invalid release asset/root.')
if not isinstance(data.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', data['sha256']):
    raise SystemExit('Invalid release SHA-256.')
for name in ('repository', 'tag', 'asset', 'sha256'): print(data[name])
PY
)"
mapfile -t release_fields <<< "$release_values"
repository="${release_fields[0]}"; tag="${release_fields[1]}"; asset="${release_fields[2]}"; expected_sha="${release_fields[3]}"
bash "$SCRIPT_DIR/server-bootstrap.sh" --check --service-user "$service_user"

if [[ -n "$archive" ]]; then
  [[ "$archive" == /* && -f "$archive" && ! -L "$archive" ]] || die '--archive must be an absolute regular ZIP path.'
else
  cache="$SCRIPT_DIR/.cache"
  [[ ! -L "$cache" ]] || die 'The download cache must not be a symlink.'
  if [[ -e "$cache" ]]; then [[ -d "$cache" ]] || die 'Download cache is not a directory.'; fi
  archive="$cache/$asset"
  if [[ ! -f "$archive" ]]; then
    "$offline" && die 'Offline mode: release ZIP is absent. Supply --archive /absolute/path/to/the/release.zip.'
    install -d -m 700 -- "$cache"
    temp_archive="$(mktemp "$cache/.download.XXXXXXXX")"
    printf 'Downloading %s/%s/%s\n' "$repository" "$tag" "$asset"
    if ! curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
      --connect-timeout 30 --retry 3 --output "$temp_archive" \
      "https://github.com/$repository/releases/download/$tag/$asset"; then
      die "Download failed. Incomplete file retained for inspection: $temp_archive"
    fi
    actual_sha="$(sha256sum -- "$temp_archive")"
    [[ "${actual_sha%% *}" == "$expected_sha" ]] || die "Downloaded ZIP failed SHA-256 verification: $temp_archive"
    [[ ! -e "$archive" && ! -L "$archive" ]] || die 'Another download appeared in the cache; not replacing it.'
    mv -T -n -- "$temp_archive" "$archive"
    [[ ! -e "$temp_archive" ]] || die 'Concurrent cache update; download retained without overwriting.'
  fi
fi
[[ -f "$archive" && ! -L "$archive" ]] || die 'Archive must be a regular file, not a symlink.'
actual_sha="$(sha256sum -- "$archive")"
[[ "${actual_sha%% *}" == "$expected_sha" ]] || die 'Release ZIP SHA-256 mismatch; installation stopped.'
args=(--install --archive "$archive" --sha256 "$expected_sha" --service-user "$service_user" --wheels-dir "$SCRIPT_DIR/wheels-ubuntu2604")
if "$offline"; then args+=(--offline); fi
bash "$SCRIPT_DIR/server-bootstrap.sh" "${args[@]}"
printf '\n%s\n' 'The proxy-aware Codex launcher is installed for the dedicated account.'
printf '%s\n' 'From this repository on your local Windows computer, run: powershell -File ./connect.ps1 -Server SERVER'
printf 'Replace SERVER with this server address. Installed service account: %s (default: codexsvc).\n' "$service_user"
printf '%s\n' 'For manual key setup, use server-authorize-key.sh. Then add the resulting SSH host in the local Codex app.'

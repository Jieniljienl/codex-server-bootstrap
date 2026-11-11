#!/usr/bin/env bash
# Add one public key to the bootstrap-managed unprivileged account only.
set -euo pipefail
umask 077
service_user='codexsvc'; public_file=''; expected_sha=''
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf '%s\n' 'Usage: bash server-authorize-key.sh --service-user codexsvc --public-key-file /absolute/key.pub --release-sha256 SHA256'
  exit 0
fi
if [[ "$EUID" -ne 0 ]]; then
  command -v sudo >/dev/null || die 'Run through root or a sudo-capable administrator.'
  exec sudo -- bash "$0" "$@"
fi
while (($#)); do
  [[ $# -ge 2 ]] || die "Missing option value: $1"
  case "$1" in
    --service-user) service_user="$2";;
    --public-key-file) public_file="$2";;
    --release-sha256) expected_sha="$2";;
    *) die "Unknown option: $1";;
  esac
  shift 2
done
[[ "$service_user" == codexsvc ]] || die 'Only the dedicated codexsvc account is managed by this helper.'
[[ "$expected_sha" =~ ^[[:xdigit:]]{64}$ ]] || die 'A valid release SHA-256 is required.'
[[ "$public_file" == /* && -f "$public_file" && ! -L "$public_file" ]] || die 'Public key must be an absolute regular file, not a link.'
[[ "$(stat -c %s "$public_file")" -le 16384 ]] || die 'Public-key input is too large.'
for dependency in python3 runuser ssh-keygen getent stat; do command -v "$dependency" >/dev/null || die "Required command missing: $dependency"; done
account="$(getent passwd "$service_user")" || die 'Install the server first; codexsvc does not exist.'
service_uid="$(id -u "$service_user")"
service_home="$(printf '%s' "$account" | cut -d: -f6)"
[[ "$service_uid" -ne 0 && "$service_home" == /home/codexsvc ]] || die 'Unexpected service account identity or home.'
[[ -d "$service_home" && ! -L "$service_home" && "$(stat -c %u "$service_home")" == "$service_uid" ]] || die 'Service home is not an ordinary directory owned by codexsvc.'
registry='/var/lib/codex-vpn-bootstrap'
for path in "$registry" "$registry/accounts"; do
  [[ -d "$path" && ! -L "$path" && "$(stat -c %u "$path")" == 0 ]] || die 'Missing or unsafe bootstrap account registry.'
  permissions="$(stat -c %a "$path")"
  (( (8#$permissions & 8#022) == 0 )) || die 'Bootstrap account registry must not be writable by other users.'
done
marker="$registry/accounts/$service_user"
[[ -f "$marker" && ! -L "$marker" && "$(stat -c %u "$marker")" == 0 ]] || die 'Account was not provisioned by this bootstrap.'
[[ "$(cat -- "$marker")" == "${expected_sha,,}:$service_uid:$service_home" ]] || die 'Installed account marker does not match this release.'

# Parsing excludes authorized_keys options, private keys and multi-line payloads.
public_key="$(python3 - "$public_file" <<'PY'
import base64, struct, sys
from pathlib import Path
lines = Path(sys.argv[1]).read_text(encoding='ascii').splitlines()
if len(lines) != 1 or not lines[0].strip():
    raise SystemExit('Expected exactly one public-key line.')
fields = lines[0].split()
allowed = {'ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521',
           'sk-ssh-ed25519@openssh.com', 'sk-ecdsa-sha2-nistp256@openssh.com'}
if len(fields) < 2 or fields[0] not in allowed:
    raise SystemExit('Invalid public key type; private keys/options are not accepted.')
try:
    blob = base64.b64decode(fields[1], validate=True)
    size, = struct.unpack('>I', blob[:4])
    embedded = blob[4:4 + size].decode('ascii')
except Exception:
    raise SystemExit('Invalid public key blob.')
if len(blob) < 4 + size or embedded != fields[0]:
    raise SystemExit('Public key type does not match its blob.')
print(fields[0] + ' ' + fields[1])
PY
)"
ssh-keygen -l -f "$public_file" >/dev/null || die 'ssh-keygen rejected the public key.'
# Drop privileges before any write under the service account home.
printf '%s\n' "$public_key" | (
  cd /
  runuser -u "$service_user" -- env -i HOME="$service_home" PATH=/usr/bin:/bin LANG=C.UTF-8 \
    python3 -c '
import fcntl, os, shlex, stat, sys
from pathlib import Path
home = Path(sys.argv[1]); key = sys.stdin.read().strip()
kind, blob = key.split()
directory = home / ".ssh"
if directory.is_symlink(): raise SystemExit("Refusing linked .ssh directory.")
directory.mkdir(mode=0o700, exist_ok=True)
info = directory.stat()
if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
    raise SystemExit("Invalid .ssh directory ownership/type.")
directory.chmod(0o700)
path = directory / "authorized_keys"
fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4 * 1024 * 1024:
        raise SystemExit("Invalid authorized_keys ownership/type/size.")
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "r+b", closefd=False) as handle:
        existing = handle.read()
        found = False
        for line in existing.decode("utf-8", errors="surrogateescape").splitlines():
            if not line.strip() or line.lstrip().startswith("#"): continue
            try: tokens = shlex.split(line, comments=False, posix=True)
            except ValueError: continue
            for index in (0, 1):
                if len(tokens) > index + 1 and tokens[index:index + 2] == [kind, blob]:
                    found = True
        if not found:
            prefix = b"\n" if existing and not existing.endswith(b"\n") else b""
            handle.write(prefix + (key + " codex-server-bootstrap\n").encode("ascii"))
            handle.flush(); os.fsync(fd)
        print("Public key already authorized." if found else "Public key added to codexsvc.")
finally:
    os.close(fd)
' "$service_home"
)
printf '%s\n' 'Root login, account password state, sshd configuration and firewall were not changed.'

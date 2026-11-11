# Server runtime

This payload provides a loopback-only Mihomo proxy, a subscription/node control
page, Codex CLI, and the optional CLIProxyAPI integration.

Use the repository's top-level installer rather than running this payload's
legacy deploy/install.sh directly:

https://github.com/Jieniljienl/codex-server-bootstrap

The top-level installer supports Ubuntu 24.04 / Python 3.12 and Ubuntu 26.04 /
Python 3.14 on x86_64, creates an unprivileged service account when run as root,
and installs the quiet Codex SSH launcher.

Configure your own subscription through an SSH-forwarded web page, then run
deploy/codex-login.sh as the service user to authorize your ChatGPT account.
No subscription, SSH private key, account authorization, or deployment password
is included in this package. CLIProxyAPI authorization is separate and optional.

The original component versions and licenses are recorded in THIRD_PARTY.md.
The top-level release.json verifies this payload archive; deploy/assets-manifest.json
verifies the original upstream binaries. Keep all bundled license notices and
corresponding source archives when redistributing the runtime.

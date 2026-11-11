#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
require_install
printf '%s\n' '请先在网页导入订阅、选择可用节点。以下登录请求经本项目代理发送。'
exec "$AI_HUB_ROOT/codex-vpn" login --device-auth

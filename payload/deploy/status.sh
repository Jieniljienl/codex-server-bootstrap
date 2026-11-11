#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
require_install
check_user_manager
check_owned_units
for unit in "${AI_HUB_UNITS[@]}"; do
  state="$(systemctl --user is-active "$unit" 2>/dev/null || true)"
  printf '%-27s %s\n' "$unit" "$state"
done
printf '\n网页端口：%s；代理：127.0.0.1:%s；API：127.0.0.1:%s\n' "$(runtime_value web_port)" "$(runtime_value proxy_port)" "$(runtime_value api_port)"
printf '%s\n' '服务 active 只表示进程运行；请在网页中检查节点和模型实际调用。'

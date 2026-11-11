#!/usr/bin/env bash
# Shared helpers. All mutations remain inside this installation or its user units.
set -euo pipefail
AI_HUB_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
AI_HUB_UNITS=(ai-hub-mihomo.service ai-hub-cliproxy.service ai-hub-web.service)

die() { printf '%s\n' "$*" >&2; exit 1; }

check_user_manager() {
  command -v systemctl >/dev/null || die '需要 Ubuntu 的 systemd 用户服务。'
  systemctl --user show-environment >/dev/null 2>&1 || die '当前会话没有可用的 systemd 用户服务。请用 ubuntu 用户正常 SSH 登录后再运行，不要使用 sudo bash。'
}

check_owned_units() {
  local unit unit_path expected load_state
  expected="# AI_PROXY_HUB_ROOT=$AI_HUB_ROOT"
  for unit in "${AI_HUB_UNITS[@]}"; do
    unit_path="$(systemctl --user show "$unit" --property=FragmentPath --value 2>/dev/null || true)"
    load_state="$(systemctl --user show "$unit" --property=LoadState --value 2>/dev/null || true)"
    if [[ "$load_state" == loaded && -z "$unit_path" ]]; then
      die "同名服务 $unit 已加载但不属于可验证的本项目文件；已停止操作。"
    fi
    if [[ -n "$unit_path" && -f "$unit_path" ]]; then
      grep -Fqx -- "$expected" "$unit_path" || die "服务 $unit 属于其他目录；未修改它。请先使用原目录的 deploy/stop.sh，或改用原目录更新。"
    fi
    unit_path="$HOME/.config/systemd/user/$unit"
    if [[ -e "$unit_path" ]]; then
      grep -Fqx -- "$expected" "$unit_path" || die "服务文件 $unit_path 不属于本安装；已停止操作。"
    fi
  done
}

require_install() {
  [[ -x "$AI_HUB_ROOT/.venv/bin/python" && -f "$AI_HUB_ROOT/data/runtime.json" ]] || die '请先执行 bash deploy/install.sh。'
}

runtime_value() {
  "$AI_HUB_ROOT/.venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))[sys.argv[2]])' "$AI_HUB_ROOT/data/runtime.json" "$1"
}

run_with_hub_proxy() {
  local proxy_port proxy_url
  proxy_port="$(runtime_value proxy_port)"
  proxy_url="http://127.0.0.1:$proxy_port"
  exec env HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url" ALL_PROXY="$proxy_url" \
    http_proxy="$proxy_url" https_proxy="$proxy_url" all_proxy="$proxy_url" \
    NO_PROXY='localhost,127.0.0.1,::1' no_proxy='localhost,127.0.0.1,::1' "$@"
}

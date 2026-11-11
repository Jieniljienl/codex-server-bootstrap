#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
umask 077

usage() {
  cat <<'USAGE'
用法：bash deploy/install.sh [--bind 0.0.0.0] [--port 8090]
       [--proxy-port 17890] [--controller-port 19090] [--api-port 18317] [--linger]
只安装本目录的独立服务。--linger 是显式可选项，用于退出 SSH 后仍保持用户服务。
USAGE
}

ensure_project_venv() {
  local venv_python="$AI_HUB_ROOT/.venv/bin/python"
  # Failed ensurepip can leave a working interpreter without pip. Re-run venv
  # in place to complete that environment; never clear it or remove user data.
  if ! "$venv_python" -m pip --version >/dev/null 2>&1; then
    printf '%s\n' '正在创建或补齐本项目的 Python 环境……'
    python3 -m venv "$AI_HUB_ROOT/.venv" || die 'Python 环境未完成。Ubuntu 24.04 请先执行 sudo apt-get install python3.12-venv，再重新运行本安装命令；已有项目数据保持原样。'
    "$venv_python" -m pip --version >/dev/null 2>&1 || die '本项目 Python 环境仍缺少 pip。请确认已安装 python3.12-venv，然后重新运行安装；未删除现有数据。'
  fi
}

link_project_binaries() {
  local binary
  for binary in mihomo cli-proxy-api; do
    ln -sfn -- "linux-amd64/$binary" "$AI_HUB_ROOT/bin/$binary"
  done
  ln -sfn -- "linux-amd64/codex-package/bin/codex" "$AI_HUB_ROOT/bin/codex"
}

[[ "$(uname -s)" == Linux ]] || die '请上传到 Ubuntu 服务器后运行安装。'
[[ "$(uname -m)" == x86_64 ]] || die '此包目标平台是 Ubuntu 24.04 x86_64，不安装 ARM 或其他架构二进制。'
[[ "$EUID" -ne 0 ]] || die '请使用 ubuntu 用户运行，不要加 sudo；本工具安装为独立用户服务。'
[[ -f /etc/os-release ]] || die '缺少系统版本信息。'
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || die '此包目标系统是 Ubuntu 24.04；其他系统未验证，已停止安装。'
command -v python3 >/dev/null || die '缺少 Python 3。Ubuntu 24.04 请先安装 python3 和 python3.12-venv。'
command -v ss >/dev/null || die '缺少端口检查工具 ss（Ubuntu 的 iproute2 软件包）。'
check_user_manager
check_owned_units

init_args=()
port_args=()
linger=false
while (($#)); do
  case "$1" in
    --bind|--port|--proxy-port|--controller-port|--api-port)
      [[ $# -ge 2 ]] || die "缺少 $1 参数值。"
      init_args+=("$1" "$2")
      case "$1" in
        --port) port_args+=(--web-port "$2");;
        --bind) port_args+=(--web-bind "$2");;
        *) port_args+=("$1" "$2");;
      esac
      shift 2;;
    --linger) linger=true; shift;;
    --help|-h) usage; exit 0;;
    *) usage; die "未知参数：$1";;
  esac
done

python3 "$AI_HUB_ROOT/deploy/preflight.py" --root "$AI_HUB_ROOT" "${port_args[@]}"
cd -- "$AI_HUB_ROOT"
python3 "$AI_HUB_ROOT/deploy/fetch-binaries.py"
link_project_binaries

ensure_project_venv
if [[ -d "$AI_HUB_ROOT/wheels" ]] && compgen -G "$AI_HUB_ROOT/wheels/*.whl" >/dev/null; then
  if ! "$AI_HUB_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check --no-index --find-links "$AI_HUB_ROOT/wheels" -r "$AI_HUB_ROOT/requirements.txt"; then
    printf '%s\n' '离线依赖不完整，尝试使用 Python 软件源补齐……'
    "$AI_HUB_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check --find-links "$AI_HUB_ROOT/wheels" -r "$AI_HUB_ROOT/requirements.txt"
  fi
else
  "$AI_HUB_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -r "$AI_HUB_ROOT/requirements.txt"
fi

if [[ -f "$AI_HUB_ROOT/data/runtime.json" ]]; then
  backup_path="$AI_HUB_ROOT/backups/$(date +%Y%m%d-%H%M%S)-$$"
  mkdir -p -- "$backup_path"
  cp -a -- "$AI_HUB_ROOT/data" "$backup_path/data"
  printf '%s\n' "原配置已备份在：$backup_path（含敏感配置，请勿公开）"
fi
"$AI_HUB_ROOT/.venv/bin/python" -m hub.manage init --data-dir "$AI_HUB_ROOT/data" "${init_args[@]}"
"$AI_HUB_ROOT/.venv/bin/python" "$AI_HUB_ROOT/deploy/make-services.py"
"$AI_HUB_ROOT/bin/mihomo" -t -d "$AI_HUB_ROOT/data/mihomo" -f "$AI_HUB_ROOT/data/mihomo/config.yaml" >/dev/null 2>&1 || die '代理配置验证失败，未启动本项目服务。'
command -v systemd-analyze >/dev/null || die '缺少 systemd-analyze，未启用或启动本项目服务。Ubuntu 24.04 应安装 systemd 工具。'
systemd-analyze --user verify \
  "$HOME/.config/systemd/user/ai-hub-mihomo.service" \
  "$HOME/.config/systemd/user/ai-hub-cliproxy.service" \
  "$HOME/.config/systemd/user/ai-hub-web.service" || die '本项目服务文件验证失败，未启用或启动服务。请保留上方错误信息进行排查。'
systemctl --user daemon-reload
systemctl --user enable "${AI_HUB_UNITS[@]}" >/dev/null
if "$linger"; then
  sudo loginctl enable-linger "$USER"
fi
bash "$AI_HUB_ROOT/deploy/start.sh"
printf '\n%s\n' "安装完成。网页端口：$(runtime_value web_port)；Codex 入口：$AI_HUB_ROOT/codex-vpn"
printf '%s\n' '下一步：网页填写订阅、选择节点，再分别完成 Codex 和 CLIProxyAPI 登录。'
if ! "$linger"; then
  printf '%s\n' '退出 SSH 后需继续运行时，可显式执行：sudo loginctl enable-linger "$USER"'
fi

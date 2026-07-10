#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
HOST="127.0.0.1"
PORT="${AUTO_RESEARCH_READONLY_PORT:-8766}"
URL="http://${HOST}:${PORT}"
LOG_DIR="${PROJECT_ROOT}/tmp"
SERVER_LOG="${LOG_DIR}/readonly-evidence-server.log"

cd "${PROJECT_ROOT}"
mkdir -p "${LOG_DIR}"

echo "Auto Research 只读公网分享"
echo "项目目录: ${PROJECT_ROOT}"
echo "本地只读地址: ${URL}"
echo

TOKEN_CANDIDATES=()
if [[ -n "${AUTO_RESEARCH_NGROK_ENV:-}" ]]; then
  TOKEN_CANDIDATES+=("${AUTO_RESEARCH_NGROK_ENV}")
fi
TOKEN_CANDIDATES+=(
  "${PROJECT_ROOT}/.env.ngrok"
  "/Users/USER/Zotero/.env.ngrok"
  "${HOME}/.env.ngrok"
)

token_from_file=""
token_source=""
for candidate in "${TOKEN_CANDIDATES[@]}"; do
  if [[ -f "${candidate}" ]]; then
    token_from_file="$(
      awk -F= '/^NGROK_AUTHTOKEN=/{print substr($0, index($0, "=") + 1)}' "${candidate}" | tail -n 1
    )"
    if [[ -n "${token_from_file}" ]]; then
      token_source="${candidate}"
      break
    fi
  fi
done
NGROK_AUTHTOKEN="${NGROK_AUTHTOKEN:-${token_from_file:-}}"

if [[ -z "${NGROK_AUTHTOKEN}" ]]; then
  echo "尚未配置 ngrok 免费账号 token。"
  echo
  echo "请先完成一次性配置："
  echo "1. 打开 https://dashboard.ngrok.com/get-started/your-authtoken"
  echo "2. 登录免费账号并复制 Authtoken"
  echo "3. 新建 /Users/USER/Zotero/.env.ngrok，内容为："
  echo "   NGROK_AUTHTOKEN=你复制的token"
  echo
  echo ".env.ngrok 不会进入 Git。"
  exit 2
fi

if [[ -n "${token_source}" ]]; then
  echo "已读取 ngrok 配置文件: ${token_source}"
else
  echo "已从环境变量读取 ngrok token。"
fi
echo

if ! command -v npx >/dev/null 2>&1; then
  echo "未找到 npx。请先安装 Node.js，或在已有 Node.js 环境中运行本脚本。"
  exit 2
fi

existing_public_url="$(
  python3 - <<PY 2>/dev/null || true
import json
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1.5) as response:
        payload = json.load(response)
except Exception:
    payload = {}

for tunnel in payload.get("tunnels", []):
    config = tunnel.get("config") or {}
    addr = str(config.get("addr") or "")
    public_url = str(tunnel.get("public_url") or "")
    if public_url.startswith("https://") and addr.endswith(":${PORT}"):
        print(public_url)
        break
PY
)"

if [[ -n "${existing_public_url}" ]]; then
  echo "检测到已有 ngrok 公网链接："
  echo "${existing_public_url}"
  echo
  echo "你可以直接把这个链接发给导师。若要停止分享，请关闭原来的 ngrok 终端窗口。"
  echo
  read "?按 Enter 关闭此窗口。"
  exit 0
fi

server_started_by_script=0
if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "检测到只读网页服务已在运行。"
else
  echo "正在启动只读网页服务。"
  PYTHONPATH=src python3 -m auto_research.cli evidence-serve --host "${HOST}" --port "${PORT}" --read-only > "${SERVER_LOG}" 2>&1 &
  SERVER_PID=$!
  server_started_by_script=1
fi

cleanup() {
  if [[ "${server_started_by_script}" == "1" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

echo "正在检查只读模式。"
for _ in {1..30}; do
  if curl -fsS "${URL}/api/ui-mode" | grep -q '"read_only": true'; then
    break
  fi
  sleep 1
done

if ! curl -fsS "${URL}/api/ui-mode" | grep -q '"read_only": true'; then
  echo "只读网页服务未能通过检查。日志位置：${SERVER_LOG}"
  exit 1
fi

echo
echo "只读网页服务已就绪。下面 ngrok 输出中的 https://...ngrok... 地址就是可发给导师的网址。"
echo "保持本窗口打开，公网链接才会持续可用；按 Ctrl+C 可停止分享。"
echo

npx --yes ngrok http "${PORT}" --authtoken "${NGROK_AUTHTOKEN}" --log=stdout

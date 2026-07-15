#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
HOST="${AUTO_RESEARCH_EVIDENCE_HOST:-127.0.0.1}"
PORT="${AUTO_RESEARCH_EVIDENCE_PORT:-8765}"
URL="http://${HOST}:${PORT}"
CACHE_ROOT="${HOME}/Library/Caches/AutoResearchEvidence"
PYCACHE_DIR="${CACHE_ROOT}/pycache"
SERVER_LOG="${CACHE_ROOT}/editable-evidence-server.log"
PYTHON_BIN="${AUTO_RESEARCH_PYTHON:-$(command -v python3 || true)}"

cd "${PROJECT_ROOT}"
mkdir -p "${CACHE_ROOT}" "${PYCACHE_DIR}"

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  echo "未找到可用的 Python 3，无法启动本地工作台。"
  echo "请安装 Python 3，或通过 AUTO_RESEARCH_PYTHON 指定解释器路径。"
  read -k 1 "?按任意键关闭窗口。"
  echo
  exit 1
fi

echo "Auto Research 本地编辑工作台"
echo "项目目录: ${PROJECT_ROOT}"
echo "网页地址: ${URL}"
echo "模式: 本地可编辑；可校对数据、切换文章、上传文献、人工补录和调用 DeepSeek。"
echo "注意: 公网分享请使用只读链接入口，不要分享这个本地编辑地址。"
echo

ui_is_ready() {
  curl --connect-timeout 1 --max-time 2 -fsS "${URL}/api/ui-mode" 2>/dev/null \
    | grep -q '"read_only": false'
}

if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  if ui_is_ready; then
    echo "检测到本地编辑网页服务已在运行，正在打开浏览器。"
    open "${URL}" >/dev/null 2>&1 || true
    echo "如果浏览器没有自动打开，请手动访问: ${URL}"
    exit 0
  fi
  echo "端口 ${PORT} 已被占用，但不是本地编辑模式。"
  echo "请先关闭占用该端口的窗口，或改用其他 AUTO_RESEARCH_EVIDENCE_PORT。"
  exit 1
fi

echo "正在启动本地编辑网页服务。关闭此窗口或按 Ctrl+C 会停止服务。"
echo "首次启动通常需要数秒；Python 缓存使用本机目录。"
echo

env PYTHONPATH="${PROJECT_ROOT}/src" PYTHONPYCACHEPREFIX="${PYCACHE_DIR}" \
  "${PYTHON_BIN}" -u -m auto_research.cli evidence-serve \
  --host "${HOST}" --port "${PORT}" </dev/null > "${SERVER_LOG}" 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "${SERVER_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "正在等待编辑模式就绪。"
for second in {1..90}; do
  if ui_is_ready; then
    echo "本地编辑工作台已就绪，正在打开浏览器。"
    open "${URL}" >/dev/null 2>&1 || true
    echo "如果浏览器没有自动打开，请手动访问: ${URL}"
    echo
    echo "保持本窗口打开，编辑工作台才会持续可用；按 Ctrl+C 可停止服务。"
    wait "${SERVER_PID}"
    exit $?
  fi
  if ! kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    wait "${SERVER_PID}" >/dev/null 2>&1 || SERVER_STATUS=$?
    echo
    echo "本地编辑服务提前退出（状态 ${SERVER_STATUS:-0}）。"
    echo "启动日志：${SERVER_LOG}"
    sed -n '1,160p' "${SERVER_LOG}" 2>/dev/null || true
    read -k 1 "?按任意键关闭窗口。"
    echo
    exit "${SERVER_STATUS:-1}"
  fi
  if (( second % 5 == 0 )); then
    echo "  已等待 ${second} 秒，服务仍在初始化……"
  fi
  sleep 1
done

echo "本地编辑工作台未能在 90 秒内启动。日志位置：${SERVER_LOG}"
sed -n '1,160p' "${SERVER_LOG}" 2>/dev/null || true
read -k 1 "?按任意键关闭窗口。"
echo
exit 1

#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
HOST="${AUTO_RESEARCH_EVIDENCE_HOST:-127.0.0.1}"
PORT="${AUTO_RESEARCH_EVIDENCE_PORT:-8765}"
URL="http://${HOST}:${PORT}"
LOG_DIR="${PROJECT_ROOT}/tmp"
SERVER_LOG="${LOG_DIR}/editable-evidence-server.log"

cd "${PROJECT_ROOT}"
mkdir -p "${LOG_DIR}"

echo "Auto Research 本地编辑工作台"
echo "项目目录: ${PROJECT_ROOT}"
echo "网页地址: ${URL}"
echo "模式: 本地可编辑；可校对数据、切换文章、上传文献、人工补录和调用 DeepSeek。"
echo "注意: 给导师分享请使用“创建导师公网链接.command”，不要分享这个本地编辑地址。"
echo

if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  if curl -fsS "${URL}/api/ui-mode" | grep -q '"read_only": false'; then
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
echo

PYTHONPATH=src python3 -m auto_research.cli evidence-serve --host "${HOST}" --port "${PORT}" </dev/null > "${SERVER_LOG}" 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "${SERVER_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "正在等待编辑模式就绪。"
for _ in {1..45}; do
  if curl -fsS "${URL}/api/ui-mode" | grep -q '"read_only": false'; then
    echo "本地编辑工作台已就绪，正在打开浏览器。"
    open "${URL}" >/dev/null 2>&1 || true
    echo "如果浏览器没有自动打开，请手动访问: ${URL}"
    echo
    echo "保持本窗口打开，编辑工作台才会持续可用；按 Ctrl+C 可停止服务。"
    wait "${SERVER_PID}"
    exit $?
  fi
  sleep 1
done

echo "本地编辑工作台未能在 45 秒内启动。日志位置：${SERVER_LOG}"
sed -n '1,120p' "${SERVER_LOG}" 2>/dev/null || true
exit 1

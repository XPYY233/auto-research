#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
HOST="${AUTO_RESEARCH_EVIDENCE_HOST:-127.0.0.1}"
PORT="${AUTO_RESEARCH_EVIDENCE_PORT:-8765}"
URL="http://${HOST}:${PORT}"

cd "${PROJECT_ROOT}"

echo "Auto Research 实验数据校对台"
echo "项目目录: ${PROJECT_ROOT}"
echo "网页地址: ${URL}"
echo

if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "检测到本地网页服务已在运行，正在打开浏览器。"
  open "${URL}" >/dev/null 2>&1 || true
  echo "如果浏览器没有自动打开，请手动访问: ${URL}"
  exit 0
fi

echo "正在启动本地网页服务。关闭此窗口或按 Ctrl+C 会停止服务。"
echo

(
  sleep 2
  open "${URL}" >/dev/null 2>&1 || true
) &

PYTHONPATH=src python3 -m auto_research.cli evidence-serve --host "${HOST}" --port "${PORT}"

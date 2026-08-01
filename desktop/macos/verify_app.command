#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h:h}"
APP_PATH="${SCRIPT_DIR}/dist/Auto Research.app"
EXECUTABLE="${APP_PATH}/Contents/MacOS/Auto Research"
CACHE_ROOT="${HOME}/Library/Caches/AutoResearchDesktop"
PYTHON_BIN="${AUTO_RESEARCH_DESKTOP_PYTHON:-$(command -v python3 || true)}"

if [[ ! -x "${EXECUTABLE}" ]]; then
  echo "还没有找到构建好的 Auto Research.app。"
  echo "请先运行 build_app.command。"
  exit 2
fi

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  echo "没有找到 Python 3，无法建立隔离验证工作区。"
  exit 2
fi

"${PYTHON_BIN}" "${SCRIPT_DIR}/verify_candidate.py" \
  --app "${APP_PATH}" \
  --project-root "${PROJECT_ROOT}" \
  --cache-root "${CACHE_ROOT}"

codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
plutil -lint "${APP_PATH}/Contents/Info.plist"
echo "通过：应用结构、临时签名和只读数据库检查均正常。"

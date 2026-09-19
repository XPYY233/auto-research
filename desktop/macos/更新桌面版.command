#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h:h}"
PYTHON_BIN="${AUTO_RESEARCH_DESKTOP_PYTHON:-$(command -v python3 || true)}"

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  echo "没有找到 Python 3，无法执行安全更新。"
  read -k 1 "?按任意键关闭窗口。"
  echo
  exit 2
fi

cd "${PROJECT_ROOT}"
echo "Auto Research 桌面版安全更新"
echo "这一步会先检查并行改动、备份数据库、运行测试，再生成新应用。"
echo

set +e
PYTHONPATH="${PROJECT_ROOT}/src" "${PYTHON_BIN}" "${SCRIPT_DIR}/safe_update.py"
STATUS=$?
set -e

echo
if (( STATUS == 0 )); then
  echo "候选构建入口已完成；已安装的 App 尚未替换。"
else
  echo "候选构建未完成；已安装应用和原始科研记录未被替换。"
fi
read -k 1 "?按任意键关闭窗口。"
echo
exit ${STATUS}

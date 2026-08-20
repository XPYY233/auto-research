#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
MANIFEST="${SCRIPT_DIR}/dist/Auto Research.app/Contents/Resources/desktop-build-manifest.json"
PYTHON_BIN="${AUTO_RESEARCH_DESKTOP_PYTHON:-$(command -v python3 || true)}"

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  echo "没有找到 Python 3，无法读取版本说明。"
  exit 2
fi

if [[ ! -f "${MANIFEST}" ]]; then
  echo "还没有找到桌面版构建清单。"
  echo "请先运行 build_app.command。"
  read -k 1 "?按任意键关闭窗口。"
  echo
  exit 2
fi

"${PYTHON_BIN}" - "${MANIFEST}" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print("Auto Research 桌面版状态")
print(f"桌面版本：{manifest['desktop_version']}")
print(f"核心提交：{manifest['core_commit'][:12]}")
print(f"核心产品版本：{manifest['core_release']}")
print(f"构建时间：{manifest['built_at']}")
print(f"目标设备：{manifest['target']}")
print(f"产品边界：{manifest.get('product_target', '未记录')}")
print(f"内置 Python：{manifest['python_runtime']}")
print(f"构建时工作树：{'干净、可复现' if manifest['worktree_clean'] else '包含未提交开发内容'}")
print("科学数据：保留在外部工作区，没有打包进应用")
print(
    f"发布状态：macOS v{manifest['desktop_version']} "
    f"build {manifest.get('build_number', '未记录')} 课题组稳定版"
)
print("签名状态：ad-hoc 签名，未经 Apple 公证")
print("Windows 状态：暂停且未发布，当前没有 Windows 安装版")
PY

echo
read -k 1 "?按任意键关闭窗口。"
echo

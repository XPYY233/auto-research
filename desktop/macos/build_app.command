#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h:h}"
PYTHON_BIN="${AUTO_RESEARCH_DESKTOP_PYTHON:-$(command -v python3 || true)}"
CACHE_ROOT="${HOME}/Library/Caches/AutoResearchDesktop"
VENV_ROOT="${CACHE_ROOT}/build-venv"
OUTPUT_ROOT="${SCRIPT_DIR}/dist"
PREVIOUS_ROOT="${SCRIPT_DIR}/releases"
APP_PATH="${OUTPUT_ROOT}/Auto Research.app"
BUILD_STAMP="$(date '+%Y%m%d-%H%M%S')"
DESKTOP_VERSION="$(/usr/bin/plutil -extract desktop_version raw -o - "${SCRIPT_DIR}/version.json")"

pause_on_error() {
  local exit_code=$?
  if (( exit_code != 0 )) && [[ -t 0 ]]; then
    echo
    echo "构建没有完成。上面的最后几行是原因；请把它们交给 Codex。"
    read -k 1 "?按任意键关闭窗口。"
    echo
  fi
  exit ${exit_code}
}
trap pause_on_error EXIT

cd "${PROJECT_ROOT}"

if ! "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/sync_release_contract.py"; then
  echo "发布契约与平台版本或共享前端哈希不一致，请先同步后再构建。"
  exit 2
fi

echo "Auto Research macOS 课题组稳定版构建器"
echo "桌面版本: ${DESKTOP_VERSION}"
echo "目标设备: Apple Silicon Mac（arm64）"
echo "签名边界: ad-hoc 签名，未经 Apple 公证"
echo

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  echo "没有找到可用的 Python 3。"
  exit 2
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "当前 macOS 发行版只允许在 Apple Silicon（arm64）Mac 上构建。"
  exit 2
fi

DIRTY_STATE="$(git status --porcelain --untracked-files=all)"
if [[ -n "${DIRTY_STATE}" && "${AUTO_RESEARCH_ALLOW_DIRTY_BUILD:-0}" != "1" ]]; then
  echo "项目还有未提交改动，因此没有生成可能混合多个对话的桌面版本。"
  echo "请先让所有 Codex 对话完成、验证并提交，再运行“更新桌面版.command”。"
  echo
  echo "当前未完成文件："
  echo "${DIRTY_STATE}"
  exit 3
fi

mkdir -p "${CACHE_ROOT}" "${OUTPUT_ROOT}" "${PREVIOUS_ROOT}"
if [[ ! -x "${VENV_ROOT}/bin/python" ]]; then
  echo "第一次构建：正在建立独立打包环境。"
  "${PYTHON_BIN}" -m venv "${VENV_ROOT}"
fi

echo "正在准备固定版本的桌面打包工具。"
"${VENV_ROOT}/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade pip
"${VENV_ROOT}/bin/python" -m pip install --disable-pip-version-check --quiet \
  --requirement "${SCRIPT_DIR}/requirements-macos-arm64.lock"

BUILD_ROOT="$(mktemp -d "${CACHE_ROOT}/candidate-${BUILD_STAMP}.XXXXXX")"
export AUTO_RESEARCH_DESKTOP_BUILD_ROOT="${PROJECT_ROOT}"

echo "正在生成候选应用；当前证据数据库不会打包进应用，也不会被修改。"
"${VENV_ROOT}/bin/python" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "${BUILD_ROOT}/dist" \
  --workpath "${BUILD_ROOT}/work" \
  "${SCRIPT_DIR}/AutoResearch.spec"

CANDIDATE_APP="${BUILD_ROOT}/dist/Auto Research.app"
CANDIDATE_EXECUTABLE="${CANDIDATE_APP}/Contents/MacOS/Auto Research"
if [[ ! -x "${CANDIDATE_EXECUTABLE}" ]]; then
  echo "候选应用没有生成可执行入口。"
  exit 4
fi

echo "正在执行只读冒烟检查。"
"${VENV_ROOT}/bin/python" "${SCRIPT_DIR}/verify_candidate.py" \
  --app "${CANDIDATE_APP}" \
  --project-root "${PROJECT_ROOT}" \
  --cache-root "${CACHE_ROOT}"

"${VENV_ROOT}/bin/python" "${SCRIPT_DIR}/build_manifest.py" \
  --project-root "${PROJECT_ROOT}" \
  --app "${CANDIDATE_APP}"
# Downloaded, hash-pinned runtime payloads can retain Finder provenance on
# recent macOS releases.  PyInstaller has already copied and verified them;
# remove those non-product attributes from the disposable candidate before
# signing so codesign does not fail inside a nested runtime binary.
/usr/bin/xattr -cr "${CANDIDATE_APP}"
codesign --force --deep --sign - "${CANDIDATE_APP}"
codesign --verify --deep --strict "${CANDIDATE_APP}"
plutil -lint "${CANDIDATE_APP}/Contents/Info.plist"

if [[ -d "${APP_PATH}" ]]; then
  PREVIOUS_INFO="${APP_PATH}/Contents/Info.plist"
  PREVIOUS_SHORT_VERSION="$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "${PREVIOUS_INFO}" 2>/dev/null || true)"
  PREVIOUS_BUILD_NUMBER="$(/usr/bin/plutil -extract CFBundleVersion raw -o - "${PREVIOUS_INFO}" 2>/dev/null || true)"
  PREVIOUS_SHORT_VERSION="${PREVIOUS_SHORT_VERSION:-unknown-version}"
  PREVIOUS_BUILD_NUMBER="${PREVIOUS_BUILD_NUMBER:-unknown-build}"
  PREVIOUS_APP="${PREVIOUS_ROOT}/Auto Research-${PREVIOUS_SHORT_VERSION}-build${PREVIOUS_BUILD_NUMBER}-${BUILD_STAMP}.app"
  echo "正在把上一版移入可恢复目录：${PREVIOUS_APP}"
  mv "${APP_PATH}" "${PREVIOUS_APP}"
fi

ditto "${CANDIDATE_APP}" "${APP_PATH}"
if [[ -f "${BUILD_ROOT}/work/AutoResearch/warn-AutoResearch.txt" ]]; then
  ditto "${BUILD_ROOT}/work/AutoResearch/warn-AutoResearch.txt" \
    "${OUTPUT_ROOT}/pyinstaller-warnings.txt"
fi
if [[ "${BUILD_ROOT}" == "${CACHE_ROOT}"/candidate-${BUILD_STAMP}.* ]]; then
  rm -rf -- "${BUILD_ROOT}"
fi

echo
echo "构建完成：${APP_PATH}"
echo "上一版（如有）保存在：${PREVIOUS_ROOT}"
echo "现在可以打开应用；科学数据仍保留在外部工作区，不会烘焙进 App。"
echo "Windows 发行已暂停，当前没有 Windows 安装版。"

trap - EXIT
if [[ -t 0 ]]; then
  read -k 1 "?按任意键关闭窗口。"
  echo
fi

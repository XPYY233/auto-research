#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
APP_PATH="${SCRIPT_DIR}/dist/Auto Research.app"
DESKTOP_VERSION="$(/usr/bin/plutil -extract desktop_version raw -o - "${SCRIPT_DIR}/version.json")"
RELEASE_STATUS="$(/usr/bin/plutil -extract release_status raw -o - "${SCRIPT_DIR}/version.json")"
case "${RELEASE_STATUS}" in
  candidate) RELEASE_LABEL="候选版" ;;
  stable) RELEASE_LABEL="稳定版" ;;
  *) echo "version.json 的 release_status 无效。"; exit 2 ;;
esac
APP_PATH="${APP_PATH:A}"
CANDIDATE_ROOT="${APP_PATH:h}"
MANIFEST="${APP_PATH}/Contents/Resources/desktop-build-manifest.json"
CANDIDATE_ID="$(/usr/bin/plutil -extract candidate_id raw -o - "${MANIFEST}")"
DESKTOP_VERSION="$(/usr/bin/plutil -extract desktop_version raw -o - "${MANIFEST}")"
DMG_PATH="${CANDIDATE_ROOT}/Auto-Research-${DESKTOP_VERSION}-${CANDIDATE_ID}-macOS-arm64.dmg"
PYTHON_BIN="${AUTO_RESEARCH_DESKTOP_PYTHON:-$(command -v python3)}"
if [[ -e "${DMG_PATH}" ]]; then
  echo "此候选安装包已存在，不允许覆盖。"
  exit 2
fi
"${PYTHON_BIN}" "${SCRIPT_DIR}/artifact_integrity.py" verify \
  --app "${APP_PATH}" --manifest "${CANDIDATE_ROOT}/app-inventory.json"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "还没有找到构建好的 Auto Research.app。"
  exit 2
fi

STAGING_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/auto-research-dmg.XXXXXX")"
trap 'rm -rf -- "${STAGING_ROOT}"' EXIT

ditto "${APP_PATH}" "${STAGING_ROOT}/Auto Research.app"
"${PYTHON_BIN}" "${SCRIPT_DIR}/artifact_integrity.py" verify \
  --app "${STAGING_ROOT}/Auto Research.app" --manifest "${CANDIDATE_ROOT}/app-inventory.json"
ln -s /Applications "${STAGING_ROOT}/Applications"
hdiutil create \
  -volname "Auto Research" \
  -srcfolder "${STAGING_ROOT}" \
  -format UDZO \
  "${DMG_PATH}"

shasum -a 256 "${DMG_PATH}" > "${DMG_PATH}.sha256"
echo "DMG 已生成：${DMG_PATH}"
echo "这是 Apple Silicon 课题组${RELEASE_LABEL}：ad-hoc 签名，未经 Apple 公证。"
echo "首次打开如被 macOS 拦截，请在访达中按住 Control 点击 App，选择“打开”并再次确认。"
echo "Windows 发行已暂停，当前没有 Windows 安装版。"

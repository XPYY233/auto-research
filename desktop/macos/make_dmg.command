#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
APP_PATH="${SCRIPT_DIR}/dist/Auto Research.app"
DESKTOP_VERSION="$(/usr/bin/plutil -extract desktop_version raw -o - "${SCRIPT_DIR}/version.json")"
DMG_PATH="${SCRIPT_DIR}/dist/Auto-Research-${DESKTOP_VERSION}-macOS-arm64.dmg"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "还没有找到构建好的 Auto Research.app。"
  exit 2
fi

STAGING_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/auto-research-dmg.XXXXXX")"
trap 'rm -rf -- "${STAGING_ROOT}"' EXIT

ditto "${APP_PATH}" "${STAGING_ROOT}/Auto Research.app"
ln -s /Applications "${STAGING_ROOT}/Applications"
hdiutil create \
  -volname "Auto Research" \
  -srcfolder "${STAGING_ROOT}" \
  -ov \
  -format UDZO \
  "${DMG_PATH}"

echo "DMG 已生成：${DMG_PATH}"
echo "这是 Apple Silicon 课题组稳定版：ad-hoc 签名，未经 Apple 公证。"
echo "首次打开如被 macOS 拦截，请在访达中按住 Control 点击 App，选择“打开”并再次确认。"
echo "Windows 发行已暂停，当前没有 Windows 安装版。"

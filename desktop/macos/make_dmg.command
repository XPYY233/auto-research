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
  -volname "Auto Research Preview" \
  -srcfolder "${STAGING_ROOT}" \
  -ov \
  -format UDZO \
  "${DMG_PATH}"

echo "DMG 已生成：${DMG_PATH}"
echo "这是未公证的 macOS 开发预览，只用于当前开发与验证；正式用户端目标是 Windows。"

#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h:h}"
CANDIDATE="${SCRIPT_DIR}/dist/Auto Research.app"
INSTALLED="/Applications/Auto Research.app"
ROLLBACK_ROOT="${PROJECT_ROOT:h}/auto-research-backups/app-rollbacks"
EXPECTED_VERSION="$(/usr/bin/plutil -extract bundle_short_version raw -o - "${SCRIPT_DIR}/version.json")"
EXPECTED_BUILD="$(/usr/bin/plutil -extract build_number raw -o - "${SCRIPT_DIR}/version.json")"
STAMP="$(date '+%Y%m%d-%H%M%S')"
STAGED="/Applications/.Auto Research-0.9.1-build19-${STAMP}.app"
LIVE_HOLD="/Applications/.Auto Research-previous-${STAMP}.app.rollback"
COMMITTED=0
MOVED_OLD=0

restore_previous_install() {
  local exit_code=$?
  if [[ "${COMMITTED}" -eq 0 ]]; then
    rm -rf -- "${STAGED}"
    if [[ "${MOVED_OLD}" -eq 1 && -d "${LIVE_HOLD}" ]]; then
      rm -rf -- "${INSTALLED}"
      mv "${LIVE_HOLD}" "${INSTALLED}"
      codesign --verify --deep --strict "${INSTALLED}" >/dev/null 2>&1 || true
    fi
  else
    rm -rf -- "${LIVE_HOLD}"
  fi
  return "${exit_code}"
}
trap restore_previous_install EXIT

if [[ ! -d "${CANDIDATE}" ]]; then
  echo "没有找到已经验证的 0.9.1 候选 App。"
  exit 2
fi
codesign --verify --deep --strict "${CANDIDATE}"
CANDIDATE_VERSION="$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "${CANDIDATE}/Contents/Info.plist")"
CANDIDATE_BUILD="$(/usr/bin/plutil -extract CFBundleVersion raw -o - "${CANDIDATE}/Contents/Info.plist")"
if [[ "${CANDIDATE_VERSION}" != "${EXPECTED_VERSION}" || "${CANDIDATE_BUILD}" != "${EXPECTED_BUILD}" ]]; then
  echo "候选 App 的版本身份与 0.9.1/build19 契约不一致。"
  exit 3
fi

mkdir -p "${ROLLBACK_ROOT}"
ROLLBACK=""
if [[ -d "${INSTALLED}" ]]; then
  PREVIOUS_VERSION="$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "${INSTALLED}/Contents/Info.plist")"
  PREVIOUS_BUILD="$(/usr/bin/plutil -extract CFBundleVersion raw -o - "${INSTALLED}/Contents/Info.plist")"
  ROLLBACK="${ROLLBACK_ROOT}/Auto Research-${PREVIOUS_VERSION}-build${PREVIOUS_BUILD}-${STAMP}.app.rollback"
  echo "正在创建不可启动的旧版回退副本。"
  ditto "${INSTALLED}" "${ROLLBACK}"
  codesign --verify --deep --strict "${ROLLBACK}"
  if [[ "$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "${ROLLBACK}/Contents/Info.plist")" != "${PREVIOUS_VERSION}" ]]; then
    echo "旧版回退副本验证失败；没有替换当前 App。"
    rm -rf -- "${ROLLBACK}"
    exit 4
  fi
fi

rm -rf -- "${STAGED}"
ditto "${CANDIDATE}" "${STAGED}"
codesign --verify --deep --strict "${STAGED}"
if [[ -d "${INSTALLED}" ]]; then
  rm -rf -- "${LIVE_HOLD}"
  mv "${INSTALLED}" "${LIVE_HOLD}"
  MOVED_OLD=1
fi
mv "${STAGED}" "${INSTALLED}"
codesign --verify --deep --strict "${INSTALLED}"
if [[ "$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "${INSTALLED}/Contents/Info.plist")" != "${EXPECTED_VERSION}" || "$(/usr/bin/plutil -extract CFBundleVersion raw -o - "${INSTALLED}/Contents/Info.plist")" != "${EXPECTED_BUILD}" ]]; then
  echo "安装后的 App 身份校验失败；正在恢复上一版本。"
  exit 5
fi
COMMITTED=1
rm -rf -- "${LIVE_HOLD}"

echo "已安装 Auto Research ${EXPECTED_VERSION} / build ${EXPECTED_BUILD}。"
if [[ -n "${ROLLBACK}" ]]; then
  echo "上一版回退副本：${ROLLBACK}"
fi

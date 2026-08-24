from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


RELEASE_CONTRACT_SCHEMA = "auto-research-release-contract-v1"
RELEASE_CONTRACT_RELATIVE_PATH = Path("config/release-contract.json")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_WEB_ASSETS = frozenset(
    {
        "src/auto_research/evidence/web/index.html",
        "src/auto_research/evidence/web/app.css",
        "src/auto_research/evidence/web/workbench.css",
        "src/auto_research/evidence/web/ai_consent.js",
        "src/auto_research/evidence/web/document_tab_store.js",
        "src/auto_research/evidence/web/pane_layout_controller.js",
        "src/auto_research/evidence/web/fusion_ai_experience.js",
        "src/auto_research/evidence/web/fusion_review.js",
        "src/auto_research/evidence/web/codex-pet-working.webp",
    }
)


class ReleaseContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseContract:
    value: Mapping[str, Any]

    @property
    def macos_version(self) -> str:
        return str(self.value["desktop"]["macos"]["desktop_version"])

    @property
    def windows_version(self) -> str:
        return str(self.value["desktop"]["windows"]["desktop_version"])

    @property
    def official_package_version(self) -> str:
        return str(self.value["packages"]["official"]["current_version"])

    def platform_version(self, platform: str) -> Mapping[str, Any]:
        try:
            value = self.value["desktop"][platform]
        except (KeyError, TypeError) as exc:
            raise ReleaseContractError("未知桌面平台") from exc
        if not isinstance(value, Mapping):
            raise ReleaseContractError("桌面版本契约无效")
        return value


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseContractError(f"{label}必须是对象")
    return value


def _require_version(value: object, label: str) -> str:
    text = str(value or "")
    if _VERSION_RE.fullmatch(text) is None:
        raise ReleaseContractError(f"{label}版本无效")
    return text


def validate_release_contract(value: Mapping[str, Any]) -> ReleaseContract:
    if value.get("schema") != RELEASE_CONTRACT_SCHEMA:
        raise ReleaseContractError("发布契约版本无效")
    _require_version(value.get("core_version"), "核心")
    desktop = _require_mapping(value.get("desktop"), "desktop")
    macos = _require_mapping(desktop.get("macos"), "macOS")
    windows = _require_mapping(desktop.get("windows"), "Windows")
    _require_version(macos.get("desktop_version"), "macOS")
    _require_version(windows.get("desktop_version"), "Windows")
    if windows.get("installer_ready") is not False:
        raise ReleaseContractError("Windows未完成真机验收前 installer_ready 必须为 false")

    http = _require_mapping(value.get("http_contract"), "HTTP")
    if http.get("version") != 1 or http.get("result_types") != [
        "item",
        "finding",
        "table",
        "figure",
    ]:
        raise ReleaseContractError("HTTP证据类型契约无效")

    packages = _require_mapping(value.get("packages"), "packages")
    official = _require_mapping(packages.get("official"), "official")
    transfer = _require_mapping(packages.get("transfer"), "transfer")
    if official.get("format") != "auto-research-evidence-package":
        raise ReleaseContractError("官方资料包格式无效")
    for key in ("format_version", "distribution_schema", "identity_version"):
        if official.get(key) != 1:
            raise ReleaseContractError(f"官方资料包 {key} 必须为1")
    _require_version(official.get("current_version"), "官方资料包")
    _require_version(official.get("minimum_app_version"), "最低App")
    _require_version(official.get("maximum_app_version_exclusive"), "最高App")
    if transfer.get("format") != "auto-research-transfer-package":
        raise ReleaseContractError("用户传输包格式无效")
    if transfer.get("transfer_schema") != 1:
        raise ReleaseContractError("用户传输包schema无效")
    if transfer.get("package_kinds") != [
        "literature_collection",
        "personal_experiments",
    ]:
        raise ReleaseContractError("用户传输包类型必须严格分离")
    if transfer.get("integrity") != "sha256-only" or transfer.get("confidentiality") != "none":
        raise ReleaseContractError("用户传输包风险契约无效")
    if transfer.get("maximum_bytes") != 2 * 1024 * 1024 * 1024:
        raise ReleaseContractError("用户传输包上限必须为2GB")

    assets = _require_mapping(value.get("web_assets"), "web_assets")
    missing_assets = REQUIRED_WEB_ASSETS.difference(map(str, assets))
    if missing_assets:
        raise ReleaseContractError("必须登记全部共享前端资产")
    for path, digest in assets.items():
        if not isinstance(path, str) or _SHA256_RE.fullmatch(str(digest or "")) is None:
            raise ReleaseContractError("共享前端资产哈希无效")
    return ReleaseContract(value=dict(value))


def load_release_contract(path: Path | str) -> ReleaseContract:
    contract_path = Path(path)
    try:
        value = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError("无法读取发布契约") from exc
    return validate_release_contract(_require_mapping(value, "release contract"))


def verify_web_asset_hashes(contract: ReleaseContract, project_root: Path | str) -> None:
    root = Path(project_root).resolve()
    for relative, expected in contract.value["web_assets"].items():
        path = (root / str(relative)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ReleaseContractError("共享前端资产路径越界") from exc
        if path.is_symlink() or not path.is_file():
            raise ReleaseContractError(f"共享前端资产缺失：{relative}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise ReleaseContractError(f"共享前端资产已变化：{relative}")

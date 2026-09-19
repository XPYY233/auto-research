from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


def git(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def core_release(project_root: Path) -> str:
    webapp = (project_root / "src" / "auto_research" / "evidence" / "webapp.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'RELEASE_INFO\s*=\s*\{.*?"version"\s*:\s*"([^"]+)"', webapp, re.S)
    return match.group(1) if match else "unknown"


def build_manifest(project_root: Path) -> dict[str, object]:
    desktop_root = project_root / "desktop" / "macos"
    desktop_version = json.loads((desktop_root / "version.json").read_text(encoding="utf-8"))
    release_status = desktop_version.get("release_status")
    if release_status not in {"candidate", "stable"}:
        raise ValueError("desktop release_status must be candidate or stable")
    release_channel = (
        "research-group-stable" if release_status == "stable" else "research-group-candidate"
    )
    release_label = "stable release" if release_status == "stable" else "release candidate"
    dirty = git(project_root, "status", "--porcelain", "--untracked-files=all")
    return {
        "manifest_version": 2,
        "candidate_id": os.environ.get("AUTO_RESEARCH_CANDIDATE_ID") or uuid.uuid4().hex,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "desktop_version": desktop_version["desktop_version"],
        "build_number": desktop_version["build_number"],
        "target": desktop_version["target"],
        "product_target": desktop_version["product_target"],
        "data_mode": desktop_version["data_mode"],
        "minimum_evidence_schema": desktop_version["minimum_evidence_schema"],
        "core_commit": git(project_root, "rev-parse", "HEAD"),
        "core_tags_at_head": git(project_root, "tag", "--points-at", "HEAD").splitlines(),
        "core_release": core_release(project_root),
        "python_runtime": platform.python_version(),
        "dependency_locks": {
            name: hashlib.sha256((desktop_root / name).read_bytes()).hexdigest()
            for name in ("requirements-macos-arm64.lock", "requirements-build-tools.lock")
            if (desktop_root / name).is_file()
        },
        "release_contract_sha256": (
            hashlib.sha256((project_root / "config/release-contract.json").read_bytes()).hexdigest()
            if (project_root / "config/release-contract.json").is_file() else None
        ),
        "validated_build_macos": platform.mac_ver()[0],
        "build_platform": platform.platform(),
        "worktree_clean": not bool(dirty),
        "scientific_data_bundled": False,
        "public_distribution_ready": False,
        "release_status": release_status,
        "release_channel": release_channel,
        "supported_architecture": "arm64",
        "code_signing": "ad-hoc",
        "apple_notarized": False,
        "windows_released": False,
        "publication_note": (
            f"macOS v{desktop_version['desktop_version']} build "
            f"{desktop_version['build_number']} research-group {release_label} for "
            "Apple Silicon; ad-hoc signed and not Apple-notarized; Windows release "
            "is paused and unpublished; production evidence remains external."
        ),
    }


def collect_dependency_notices(resources: Path) -> None:
    """Preserve upstream notices and record the exact build environment."""
    inventory = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
        notices = []
        for entry in distribution.files or []:
            text = str(entry).lower()
            if ".dist-info/" not in text or not any(part in entry.name.lower() for part in ("license", "notice", "copying")):
                continue
            data = distribution.locate_file(entry).read_bytes()
            relative = Path("third-party-notices") / safe_name / entry.name
            destination = resources / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.read_bytes() != data:
                raise ValueError(f"Conflicting upstream notice: {name}")
            destination.write_bytes(data)
            notices.append({"file": relative.as_posix(), "sha256": hashlib.sha256(data).hexdigest()})
        inventory.append({
            "name": name, "version": distribution.version,
            "declared_license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
            "project_urls": distribution.metadata.get_all("Project-URL", []),
            "notices": notices,
        })
    (resources / "dependency-inventory.json").write_text(json.dumps({
        "scope": "build environment, not a complete native binary SBOM",
        "redistribution_clearance": "pending",
        "dependencies": inventory,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--app", required=True)
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    app = Path(args.app).expanduser().resolve()
    resources = app / "Contents" / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    destination = resources / "desktop-build-manifest.json"
    destination.write_text(
        json.dumps(build_manifest(project_root), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    collect_dependency_notices(resources)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

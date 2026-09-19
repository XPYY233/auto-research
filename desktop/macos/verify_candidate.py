from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(executable: Path, project_root: Path, cache_root: Path) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="isolated-smoke-", dir=cache_root) as directory:
        workspace = Path(directory) / "workspace"
        environment = {
            key: value for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "AUTO_RESEARCH"))
        }
        environment["HOME"] = str(Path(directory) / "home")
        Path(environment["HOME"]).mkdir()
        # The candidate creates its own schema. No checkout data/config links,
        # no developer database, and no host Python dependency at runtime.
        subprocess.run(
            [str(executable), "--initialize-workspace", str(workspace)], check=True, env=environment,
        )
        temporary_database = workspace / "db" / "experimental_evidence.sqlite"
        before = sha256(temporary_database)
        subprocess.run(
            [str(executable), "--smoke-test", "--project-root", str(workspace)],
            check=True,
            env=environment,
        )
        after = sha256(temporary_database)
        if before != after:
            raise RuntimeError("隔离冒烟检查改变了临时证据数据库")
        print(f"隔离数据库 SHA-256 保持不变：{before}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--cache-root", required=True)
    args = parser.parse_args()

    app = Path(args.app).expanduser().resolve()
    executable = app / "Contents" / "MacOS" / "Auto Research"
    if not executable.is_file():
        raise FileNotFoundError(f"缺少应用入口：{executable}")
    verify(
        executable,
        Path(args.project_root).expanduser().resolve(),
        Path(args.cache_root).expanduser().resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

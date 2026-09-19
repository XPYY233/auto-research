"""Single engineering gate, shared and Mac suites in separate processes."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], env: dict[str, str]) -> None:
    print("CHECK:", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    required = ("pytest", "requests", "yaml", "fitz", "cryptography", "pydantic", "pyarrow")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    if not shutil.which("node"):
        missing.append("node")
    if missing:
        print("Missing required dependencies: " + ", ".join(missing), file=sys.stderr)
        return 2
    artifacts = ROOT / ".artifacts/checks"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="auto-research-check-home-") as home:
        env = {key: value for key, value in os.environ.items()
               if not any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "AUTO_RESEARCH"))}
        env.update(HOME=home, PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                   PYTHONPATH=os.pathsep.join(str(ROOT / path) for path in ("src", "desktop/macos", "scripts")))
        run([sys.executable, "scripts/check_repository.py"], env)
        contract = json.loads((ROOT / "config/release-contract.json").read_text())
        for relative in contract["web_assets"]:
            if relative.endswith(".js"):
                run(["node", "--check", str(ROOT / relative)], env)
        for name, path in (("shared", "src/tests"), ("macos", "desktop/macos/tests")):
            run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "-p", "engineering_checks", "--strict-markers",
                 f"--junitxml={artifacts / (name + '.xml')}", path], env)
        run(["git", "diff", "--check"], env)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)

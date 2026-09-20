"""Fail closed on research data or credential patterns in tracked source files.

Pattern checks are a guardrail, not a comprehensive security audit. Exceptions
identify exact existing synthetic test values by path and hash, never by folder.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",
    rb"\bsk-[A-Za-z0-9_-]{24,}",
)


def violations(path: str, data: bytes, allowed: set[tuple[str, str]]) -> list[str]:
    errors = []
    parts = Path(path).parts
    suffix = Path(path).suffix.lower()
    private_dirs = {"data", "db", "official-packages", "private-library", "backups", "exports", "State"}
    if private_dirs.intersection(parts) or suffix in {".sqlite", ".sqlite3", ".db", ".pdf", ".aresearch", ".dmg", ".bundle", ".zip", ".tar", ".gz", ".tgz", ".7z", ".parquet", ".jsonl", ".csv", ".xlsx"}:
        errors.append("research data, archive or release artifact")
    if data.startswith((b"SQLite format 3", b"%PDF-", b"PK\x03\x04", b"\x1f\x8b")):
        errors.append("research or archive binary content")
    basename = Path(path).name
    if (basename == ".env" or basename.startswith(".env.") and basename != ".env.example"
        or suffix in {".key", ".pem", ".p12", ".pfx"}
        or basename in {"credentials.json", "credentials.enc", "auth.json", "settings-v1.json", "ai-runtime-state-v1.json"}):
        errors.append("credential or private state file")
    if b"\x00" in data:
        assets = {(x["path"], x["sha256"]) for x in json.loads((ROOT / "config/public-assets.json").read_text())}
        if (path, hashlib.sha256(data).hexdigest()) not in assets:
            errors.append("unreviewed binary payload")
    for pattern in PATTERNS:
        for match in re.findall(pattern, data):
            if (path, hashlib.sha256(match).hexdigest()) not in allowed:
                errors.append("credential pattern (value withheld)")
    return errors


def main() -> int:
    allowed = {(row["path"], row["sha256"]) for row in json.loads((ROOT / "config/secret-fixtures.json").read_text())}
    files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    failures = []
    for name in filter(None, files):
        path = ROOT / name
        if path.is_symlink():
            failures.append((name, ["tracked symlink"]))
        elif path.is_file():
            problems = violations(name, path.read_bytes(), allowed)
            if problems:
                failures.append((name, problems))
    for name, problems in failures:
        print(name + ": " + "; ".join(problems))
    print(f"Repository hygiene: {len(failures)} blocked files")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    if path.startswith(("data/", "db/")) or Path(path).suffix.lower() in {".sqlite", ".sqlite3", ".pdf", ".aresearch", ".dmg", ".bundle"}:
        errors.append("research data or release artifact")
    if data.startswith((b"SQLite format 3", b"%PDF-")):
        errors.append("research binary content")
    if Path(path).name == ".env" or Path(path).suffix.lower() in {".key", ".p12", ".pfx"}:
        errors.append("credential file")
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

"""Bind packaging to the already accepted App, including symlink targets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def inventory(app: Path) -> dict[str, object]:
    if not app.is_dir():
        raise ValueError("Candidate App is missing")
    entries = {}
    for path in sorted(app.rglob('*')):
        name = path.relative_to(app).as_posix()
        if path.is_symlink():
            entries[name] = {'symlink': str(path.readlink())}
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
            entries[name] = {'sha256': digest.hexdigest(), 'size': path.stat().st_size,
                             'mode': path.stat().st_mode & 0o777}
    if not entries:
        raise ValueError("Candidate App is empty")
    return {'schema': 'candidate-app-inventory-v1', 'entries': entries}


def verify(app: Path, manifest: Path) -> None:
    if inventory(app) != json.loads(manifest.read_text(encoding='utf-8')):
        raise ValueError('候选 App 与验收身份不一致，必须重新验收，不能覆盖旧制品。')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['write', 'verify'])
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    if args.operation == 'write':
        data = inventory(args.app)
        with args.manifest.open('x', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write('\n')
    else:
        verify(args.app, args.manifest)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

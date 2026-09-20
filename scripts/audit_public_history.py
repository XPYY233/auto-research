"""Audit every reachable blob and commit identity; never print matched secrets."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
from check_repository import ROOT, violations


def main() -> int:
    allowed = {(x['path'], x['sha256']) for x in json.loads((ROOT / 'config/secret-fixtures.json').read_text())}
    history = {(x['path'], x['sha256']) for x in json.loads((ROOT / 'config/historical-secret-fixtures.json').read_text())}
    objects = subprocess.check_output(['git', 'rev-list', '--objects', '--all'], cwd=ROOT).decode().splitlines()
    problems, blobs, total = [], 0, 0
    child = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert child.stdin is not None and child.stdout is not None
    for entry in objects:
        oid, _, path = entry.partition(' ')
        child.stdin.write((oid + '\n').encode()); child.stdin.flush()
        header = child.stdout.readline().split()
        data = child.stdout.read(int(header[2])); child.stdout.read(1)
        if header[1] == b'blob':
            blobs += 1; total += len(data)
            found = violations(path, data, allowed | history)
            if found:
                problems.append((oid, path, found))
        elif header[1] == b'commit':
            for identity in re.findall(rb'^(?:author|committer) .*?<([^>]+)>', data, re.M):
                if not (identity.endswith(b'@users.noreply.github.com') or identity == b'noreply@github.com'):
                    problems.append((oid, '(commit identity)', ['non-private author email required']))
    child.stdin.close(); child.wait()
    for oid, path, found in problems:
        print(oid[:12], path, '; '.join(found))
    print(json.dumps({'reachable_blobs': blobs, 'bytes_scanned': total, 'blocked_objects': len(problems)}))
    return int(bool(problems))

if __name__ == '__main__':
    raise SystemExit(main())

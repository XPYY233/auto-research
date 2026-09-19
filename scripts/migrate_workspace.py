"""Maintainer entrypoint: prepare a verified workspace without activating it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from auto_research.workspace_migration import migrate_workspace


def main() -> int:
    parser = argparse.ArgumentParser(description='复制并校验旧工作区；不会修改日常目录设置。')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--repair-legacy-sampled-hashes', action='store_true',
                        help='仅校正可复现旧签名且已有同文件完整哈希佐证的记录；保留校正登记。')
    args = parser.parse_args()
    report = migrate_workspace(args.source, args.destination, repair_legacy_sampled_hashes=args.repair_legacy_sampled_hashes)
    print(json.dumps({
        'activated': report['activated'],
        'source_hash_repairs': len(report['source_hash_repairs']),
        'tables_verified': len(report['record_fingerprints']),
        'files_verified': report['source_data_files'],
        'references_relocated': len(report['references']),
        'missing_historical_outputs': len(report['missing_historical_outputs']),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

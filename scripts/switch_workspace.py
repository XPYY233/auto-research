"""Mac maintainer entrypoint; quit the App before using this command."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'desktop/macos')]
from workspace_switch import activate_workspace, rollback_workspace

def main() -> None:
    parser = argparse.ArgumentParser(description='持有 App 独占锁，迁移并原子切换工作区；支持有数据保护的回退。')
    parser.add_argument('--private-root', type=Path, default=Path.home()/'Library/Application Support/Auto Research')
    sub = parser.add_subparsers(dest='operation', required=True)
    activate = sub.add_parser('activate')
    activate.add_argument('--source', type=Path, required=True)
    activate.add_argument('--destination', type=Path, required=True)
    activate.add_argument('--repair-legacy-sampled-hashes', action='store_true')
    rollback = sub.add_parser('rollback')
    rollback.add_argument('--journal', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'activate':
        journal = activate_workspace(args.source, args.destination, args.private_root,
            repair_legacy_sampled_hashes=args.repair_legacy_sampled_hashes)
        print('切换已完成；回退登记：', journal)
    else:
        rollback_workspace(args.journal, args.private_root)
        print('已恢复旧目录设置；两个工作区均保留。')


if __name__ == "__main__":
    main()

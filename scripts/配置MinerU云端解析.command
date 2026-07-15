#!/bin/zsh
set -euo pipefail

PROJECT_DIR="/Users/USER/Zotero/auto-research"
cd "$PROJECT_DIR"

echo "Auto Research · MinerU 云端图表解析"
echo "Token 只会写入 macOS 钥匙串 auto-research-mineru，不会写入项目、数据库或日志。"
echo

PYTHONPATH=src python3 -m auto_research.cli evidence-mineru-store-token
echo
PYTHONPATH=src python3 -m auto_research.cli evidence-mineru-status
echo
echo "配置完成。重新打开本地工作台后，可在表格/图片校对页生成云端候选。"
read -k 1 "?按任意键关闭…"

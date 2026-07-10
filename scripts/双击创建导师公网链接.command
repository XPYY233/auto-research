#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
"${SCRIPT_DIR}/start_readonly_ngrok.command"

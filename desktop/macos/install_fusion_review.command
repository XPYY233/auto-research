#!/bin/zsh
set -euo pipefail
SCRIPT_DIR="${0:A:h}"
PYTHON_BIN="${AUTO_RESEARCH_DESKTOP_PYTHON:-$(command -v python3)}"
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/guarded_install.py"

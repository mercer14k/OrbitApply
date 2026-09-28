#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"
source "$project_dir/macos_common.sh"
check_m_series_mac
require_mac_environment
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt || .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -c 'import fitz, pandas, playwright, streamlit; print("App dependencies verified.")'
echo "Dependencies updated. Double-click run_macos.command to restart the app."

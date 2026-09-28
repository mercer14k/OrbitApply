#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"
source "$project_dir/macos_common.sh"
check_m_series_mac
require_mac_environment
start_mac_ollama
echo "Keep this window open. If no browser opens, visit http://127.0.0.1:8501"
echo "Press Control+C here to stop the app."
.venv/bin/python -m streamlit run launch.py --server.address 127.0.0.1 --server.port 8501 --server.headless false

#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"
source "$project_dir/macos_common.sh"
check_m_series_mac
find_mac_python
start_mac_ollama

echo "Detected $("$python_bin" --version) at $python_bin"
if [ -e .venv ] && ! .venv/bin/python -c 'import platform, sys; ok = (3, 11) <= sys.version_info[:2] <= (3, 14) and platform.machine() == "arm64"; raise SystemExit(0 if ok else 1)' 2>/dev/null; then
    backup_dir=".venv.previous-$(date +%Y%m%d-%H%M%S)"
    mv .venv "$backup_dir"
    echo "Moved the incompatible old environment to $backup_dir"
fi

if [ ! -x .venv/bin/python ]; then "$python_bin" -m venv .venv; fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt || {
    echo "A prebuilt package was unavailable for $(.venv/bin/python --version)."
    echo "Trying the normal installer, which may build a dependency locally."
    .venv/bin/python -m pip install -r requirements.txt
}
.venv/bin/python -m playwright install chromium
"$ollama_bin" pull qwen3:8b
.venv/bin/python -c 'import fitz, pandas, playwright, streamlit; print("App dependencies verified.")'
echo "Setup complete for this Apple-silicon Mac."
echo "Double-click run_macos.command to open OrbitApply."

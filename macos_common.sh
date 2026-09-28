#!/usr/bin/env bash
# Shared by the Apple-silicon Mac .command launchers. No administrator access is needed.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:$PATH"

minimum_python="3.11"
maximum_python="3.14"

finish_mac_task() {
    task_status=$?
    if [ "$task_status" -ne 0 ]; then
        echo ""
        echo "The task stopped. Read the error above before trying again."
    fi
    if [ -t 0 ]; then
        read -r -p "Press Return to close this window... " || true
    fi
}
trap finish_mac_task EXIT

if [ "$(uname -s)" != "Darwin" ]; then
    echo "These launchers are for macOS. Use the Windows or Linux scripts on those systems."
    exit 1
fi

find_mac_python() {
    python_bin=""
    for candidate in \
        python3.14 python3.13 python3.12 python3.11 \
        /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
        /opt/homebrew/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
        python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import platform, sys; ok = (3, 11) <= sys.version_info[:2] <= (3, 14) and platform.machine() == "arm64"; raise SystemExit(0 if ok else 1)' 2>/dev/null; then
            python_bin="$(command -v "$candidate")"
            break
        fi
    done
    if [ -z "$python_bin" ]; then
        echo "A native Apple-silicon Python $minimum_python-$maximum_python was not found."
        echo "Install the current universal2 macOS release from https://www.python.org/downloads/macos/"
        echo "Then close Terminal, reopen it, and run setup_macos.command again."
        exit 1
    fi
}

check_m_series_mac() {
    if [ "$(uname -m)" != "arm64" ]; then
        echo "This package is built for an Apple-silicon Mac (M1 or newer)."
        echo "Open Terminal normally, not through Rosetta, and try again."
        exit 1
    fi

    memory_bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    if [ "$memory_bytes" -gt 0 ] && [ "$memory_bytes" -lt 12884901888 ]; then
        echo "Warning: less than 12 GB of memory was detected. The default 8B model may be slow."
    fi
}

start_mac_ollama() {
    ollama_bin="$(command -v ollama || true)"
    if [ -z "$ollama_bin" ] && [ -x /Applications/Ollama.app/Contents/Resources/ollama ]; then
        ollama_bin="/Applications/Ollama.app/Contents/Resources/ollama"
    fi
    if [ -z "$ollama_bin" ]; then
        echo "Install and open Ollama first: https://ollama.com/download/mac"
        exit 1
    fi
    if ! "$ollama_bin" list >/dev/null 2>&1; then
        open -a Ollama || true
        for attempt in {1..20}; do
            if "$ollama_bin" list >/dev/null 2>&1; then return; fi
            sleep 1
        done
        echo "Ollama is not responding. Open Ollama, then try this launcher again."
        exit 1
    fi
}

require_mac_environment() {
    if [ ! -x .venv/bin/python ]; then
        echo "Run setup_macos.command first. Do not copy a Windows .venv to your Mac."
        exit 1
    fi
    if ! .venv/bin/python -c 'import platform, sys; ok = (3, 11) <= sys.version_info[:2] <= (3, 14) and platform.machine() == "arm64"; raise SystemExit(0 if ok else 1)' 2>/dev/null; then
        echo "The saved environment is incompatible with this Mac or Python version."
        echo "Run setup_macos.command again to rebuild it."
        exit 1
    fi
}

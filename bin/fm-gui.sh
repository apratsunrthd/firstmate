#!/usr/bin/env bash
# fm-gui.sh - launch the local web chat GUI for First Mate.
#
# Starts gui/server.py, which serves a single-page chat UI on localhost and
# proxies each message to a headless `claude -p` invocation run from this
# repo root (so the session loads AGENTS.md and behaves as First Mate).
# See gui/README.md for what this does and does not do, and its safety notes.
#
# Usage: bin/fm-gui.sh [--open]
#   --open   also open the GUI in your default browser once the server is up.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${FM_GUI_PORT:-8756}"
OPEN=0

for arg in "$@"; do
  case "$arg" in
    --open) OPEN=1 ;;
    *) echo "usage: bin/fm-gui.sh [--open]" >&2; exit 1 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found on PATH" >&2
  exit 1
fi

if [ "$OPEN" -eq 1 ]; then
  ( sleep 1
    if command -v open >/dev/null 2>&1; then
      open "http://127.0.0.1:${PORT}"
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "http://127.0.0.1:${PORT}"
    fi
  ) &
fi

exec python3 "$ROOT/gui/server.py"

#!/usr/bin/env bash
# Launcher for the notetaker MCP server. Called by the plugin manifest.
#
# Order of preference:
#   1. uv (on PATH or in the usual install locations)   -> uv run notetaker-mcp
#   2. python3 >= 3.10                                    -> one-time venv in CLAUDE_PLUGIN_DATA
#   3. neither                                            -> clear instructions on stderr, exit 1
#
# stderr from this script shows up in Claude Code under /mcp -> notetaker -> logs.
set -euo pipefail

# No external commands here: a broken PATH must still reach the help text.
HERE="${BASH_SOURCE[0]%/*}"; [ "$HERE" = "${BASH_SOURCE[0]}" ] && HERE=.
ROOT="$(cd "$HERE/.." && pwd -P)"
DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.local/share/recall-notetaker}"
# shellcheck source=scripts/runtime.sh
. "$ROOT/scripts/runtime.sh"

if UV="$(find_uv)"; then
  exec "$UV" run --quiet --directory "$ROOT" notetaker-mcp
fi

if PY="$(find_python)"; then
  VENV="$DATA/venv"
  if ! venv_is_current "$VENV" "$ROOT"; then
    log "uv not found; installing into $VENV with $PY (one-time, needs network)"
    mkdir -p "$DATA"
    "$PY" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet "$ROOT"
    touch "$VENV/.installed"
  fi
  exec "$VENV/bin/notetaker-mcp"
fi

log "ERROR: cannot start - neither 'uv' nor Python >= 3.10 was found."
print_install_help
exit 1

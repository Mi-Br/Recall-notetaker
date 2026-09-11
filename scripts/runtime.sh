#!/usr/bin/env bash
# Shared helpers for launch-mcp.sh and doctor.sh. Must stay bash-3.2 compatible (macOS).

log() { printf '[notetaker] %s\n' "$*" >&2; }

# GUI-launched apps often get a minimal PATH, so look in the usual install spots too.
find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
  local c
  for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [ -x "$c" ]; then echo "$c"; return 0; fi
  done
  return 1
}

find_python() {
  local p
  for p in python3.13 python3.12 python3.11 python3.10 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 \
       && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      command -v "$p"; return 0
    fi
  done
  return 1
}

# True when the fallback venv exists and is newer than the project definition.
venv_is_current() {
  local venv="$1" root="$2"
  [ -x "$venv/bin/notetaker-mcp" ] && [ -f "$venv/.installed" ] && ! [ "$root/pyproject.toml" -nt "$venv/.installed" ]
}

print_install_help() {
  log "Install uv (recommended, ~10 s):"
  log "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  log "  or:  brew install uv"
  log "Alternatively install Python 3.10+ (brew install python) - the plugin will build its own venv."
  log "Then fully restart Claude Code (a new terminal picks up the PATH) and run /notetaker:doctor."
}

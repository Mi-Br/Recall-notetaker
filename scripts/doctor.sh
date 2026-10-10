#!/usr/bin/env bash
# Environment check for the notetaker plugin.
#   scripts/doctor.sh          full report, exit 1 if the MCP server cannot start
#   scripts/doctor.sh --hook   SessionStart mode: silent when healthy, short advice when not
#   RECALL_API_KEY=... scripts/doctor.sh --probe   also find the Recall region for the key
set -uo pipefail

# No external commands here: a broken PATH must still reach the help text.
HERE="${BASH_SOURCE[0]%/*}"; [ "$HERE" = "${BASH_SOURCE[0]}" ] && HERE=.
ROOT="$(cd "$HERE/.." && pwd -P)"
# shellcheck source=scripts/runtime.sh
. "$ROOT/scripts/runtime.sh"

MODE="${1:-}"
problems=0
say() { printf '%s\n' "$*"; }

report_runtime() {
  if UV="$(find_uv)"; then
    say "ok    uv found: $UV ($("$UV" --version 2>/dev/null))"
    case ":$PATH:" in
      *":${UV%/*}:"*) ;;
      *) say "note  uv is not on PATH for this process; the launcher finds it anyway." ;;
    esac
    return 0
  fi
  if PY="$(find_python)"; then
    say "ok    uv not installed; Python fallback available: $PY ($("$PY" --version 2>&1))"
    say "note  first start builds a venv in \${CLAUDE_PLUGIN_DATA}/venv (needs network, ~30 s)."
    return 0
  fi
  say "FAIL  neither uv nor Python >= 3.10 found - the MCP server cannot start."
  return 1
}

if [ "$MODE" = "--hook" ]; then
  if find_uv >/dev/null 2>&1 || find_python >/dev/null 2>&1; then exit 0; fi
  say "The notetaker plugin cannot start: neither 'uv' nor Python 3.10+ is installed."
  say "Tell the user to run:  curl -LsSf https://astral.sh/uv/install.sh | sh   then restart Claude Code,"
  say "or:  brew install uv   - and to run /notetaker:doctor afterwards to confirm."
  exit 0   # never block the session
fi

say "notetaker doctor"
say "plugin root : $ROOT"
say "data dir    : ${CLAUDE_PLUGIN_DATA:-<unset - CLI mode, defaults to ./notes and ./state>}"
runtime_ok=1
report_runtime || { problems=1; runtime_ok=0; }

if command -v git >/dev/null 2>&1; then
  say "ok    git: $(git --version 2>&1)"
else
  say "warn  git not found (only needed for plugin updates)."
fi

if [ -n "${RECALL_API_KEY:-}" ]; then
  say "ok    RECALL_API_KEY is set (${#RECALL_API_KEY} chars); region=${RECALL_REGION:-unset}"
else
  if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
    say "FAIL  RECALL_API_KEY is not set in this cloud session. Add it in the environment settings"
    say "      (network secret or environment variable), then start a NEW session."
    problems=1
  else
    say "note  RECALL_API_KEY not visible to this shell. With the plugin it is injected from the install prompt;"
    say "      to verify the key ask Claude to call the notetaker list_bots tool."
  fi
fi

if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && command -v curl >/dev/null 2>&1; then
  reachable=""
  for r in us-east-1 us-west-2 eu-central-1 ap-northeast-1; do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 8 "https://$r.recall.ai/api/v1/bot/?limit=1" 2>/dev/null)"
    case "$code" in 000|"") ;; *) reachable="$reachable $r";; esac
  done
  if [ -n "$reachable" ]; then
    say "ok    Recall reachable from this cloud session:$reachable"
  else
    say "FAIL  no Recall host is reachable: the environment's network policy blocks it. Add your region"
    say "      host (e.g. us-west-2.recall.ai) under Allowed domains, then start a NEW session."
    problems=1
  fi
fi

if [ "$MODE" = "--probe" ] && [ -n "${RECALL_API_KEY:-}" ] && command -v curl >/dev/null 2>&1; then
  found=""
  for r in us-east-1 us-west-2 eu-central-1 ap-northeast-1; do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H "Authorization: Token $RECALL_API_KEY" "https://$r.recall.ai/api/v1/bot/?limit=1" || echo 000)"
    say "      $r -> HTTP $code"
    [ "$code" = "200" ] && found="$r"
  done
  if [ -n "$found" ]; then say "ok    key belongs to region: $found"; else say "FAIL  no region accepted the key (wrong key, or network blocked)."; problems=1; fi
fi

if [ "$problems" -ne 0 ]; then
  if [ "$runtime_ok" -eq 0 ]; then
    say ""
    print_install_help 2>&1
  fi
  exit 1
fi
say "all good - run the meet command (/notetaker:meet with the plugin, /meet in this repo) to test with a real call."

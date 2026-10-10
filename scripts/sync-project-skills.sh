#!/usr/bin/env bash
# Copy the plugin skills (skills/) into project skills (.claude/skills/) so a Claude Code
# session opened on this repo - including Claude Code on the web - gets them without
# installing the plugin. `doctor` is renamed because /doctor is a built-in command.
# tests/test_project_skills.py fails if the copies drift; rerun this after editing skills/.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for src in "$ROOT"/skills/*/; do
  name="$(basename "$src")"
  dest_name="$name"
  [ "$name" = "doctor" ] && dest_name="notetaker-doctor"
  mkdir -p "$ROOT/.claude/skills/$dest_name"
  sed "s/^name: $name\$/name: $dest_name/" "$src/SKILL.md" > "$ROOT/.claude/skills/$dest_name/SKILL.md"
done
echo "synced: $(ls "$ROOT/.claude/skills" | tr '\n' ' ')"

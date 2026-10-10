"""Project skills (.claude/skills) must mirror the plugin skills (skills/).

Run scripts/sync-project-skills.sh after editing anything under skills/.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENAMES = {"doctor": "notetaker-doctor"}  # /doctor is a built-in Claude Code command


def test_project_skills_match_plugin_skills():
    plugin = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").exists())
    assert plugin, "no plugin skills found"
    for name in plugin:
        dest = RENAMES.get(name, name)
        src_text = (ROOT / "skills" / name / "SKILL.md").read_text()
        expected = src_text.replace(f"\nname: {name}\n", f"\nname: {dest}\n", 1)
        copy = ROOT / ".claude" / "skills" / dest / "SKILL.md"
        assert copy.exists(), f"missing {copy.relative_to(ROOT)}; run scripts/sync-project-skills.sh"
        assert copy.read_text() == expected, f"{copy.relative_to(ROOT)} is stale; run scripts/sync-project-skills.sh"


def test_no_project_skill_shadows_a_builtin():
    builtins = {"doctor", "help", "init", "config", "mcp", "model", "plugin", "review", "status", "clear"}
    names = {p.name for p in (ROOT / ".claude" / "skills").iterdir()}
    assert not names & builtins


def test_project_mcp_config_has_no_secrets_and_is_enabled():
    mcp = json.loads((ROOT / ".mcp.json").read_text())
    server = mcp["mcpServers"]["notetaker"]
    assert server["args"][0].endswith("scripts/launch-mcp.sh")
    assert not any("KEY" in k for k in server.get("env", {})), "keys must come from the environment, not the repo"
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    assert "notetaker" in settings["enabledMcpjsonServers"]

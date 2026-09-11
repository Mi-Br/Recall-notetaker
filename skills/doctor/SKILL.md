---
name: doctor
description: Diagnose why the notetaker plugin or its MCP server is not working (uv/Python missing, wrong Recall region, bad API key). Use when /mcp shows the notetaker server as failed, a notetaker tool errors, or the user asks to check the setup.
disable-model-invocation: false
---

Run the environment check and explain the result to the user in plain language.

1. Run: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.sh"` and show the output.
2. If it reports FAIL for the runtime, give the user the exact install command it printed and
   tell them to **fully restart Claude Code** afterwards (the new PATH is only picked up by a
   new process), then run `/notetaker:doctor` again.
3. If the runtime is fine, verify the Recall connection by calling the `list_bots` tool:
   - success (even an empty list) → setup is complete;
   - HTTP 401 → the API key is wrong; the user re-enters it via `/plugin` → notetaker → configure;
   - HTTP 403 / connection error → most likely the wrong region; suggest trying the other
     regions (us-east-1, us-west-2, eu-central-1, ap-northeast-1), or have the user run
     `RECALL_API_KEY=... bash "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.sh" --probe` in a terminal,
     which reports which region accepts the key.
4. If the MCP server itself is not listed under `/mcp`, tell the user to run `/reload-plugins`
   or restart Claude Code, then check `/mcp` → notetaker → logs, and paste any `[notetaker]`
   lines back here.

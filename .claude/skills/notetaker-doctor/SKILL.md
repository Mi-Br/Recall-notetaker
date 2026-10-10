---
name: notetaker-doctor
description: Diagnose why the notetaker plugin or its MCP server is not working (uv/Python missing, wrong Recall region, bad API key). Use when /mcp shows the notetaker server as failed, a notetaker tool errors, or the user asks to check the setup.
disable-model-invocation: false
---

Run the environment check and explain the result to the user in plain language.

1. Run `bash scripts/doctor.sh` from the notetaker root and show the output. The root is
   `${CLAUDE_PLUGIN_ROOT}` when installed as a plugin, otherwise the repository root.
2. If it reports FAIL for the runtime, give the user the exact install command it printed and
   tell them to **fully restart Claude Code** afterwards (the new PATH is only picked up by a
   new process), then run this check again.
3. If the runtime is fine, verify the Recall connection by calling the `list_bots` tool:
   - success (even an empty list) → setup is complete;
   - "RECALL_API_KEY is not set" or HTTP 401 → the key is missing or wrong. Plugin: re-enter it via
     `/plugin` → notetaker → configure. Cloud session: add a network secret on the environment
     (allowed websites `*.recall.ai`, header `Authorization`, prefix `Token`, not `Bearer`), or an
     environment variable `RECALL_API_KEY` on plans without network secrets, then start a new
     session. Never ask the user to paste the key into the chat;
   - doctor says RECALL_REGION does not match the key's region → set the environment variable
     `RECALL_REGION` to the region it names and start a new session;
   - "network error" / ProxyError 403 in a cloud session → the environment's network policy blocks
     Recall. The user adds the region host (e.g. `us-west-2.recall.ai`) under Allowed domains in the
     environment settings, keeps package managers allowed, and starts a new session;
   - HTTP 403 / connection error elsewhere → most likely the wrong region; suggest trying the other
     regions (us-east-1, us-west-2, eu-central-1, ap-northeast-1), or have the user run
     `RECALL_API_KEY=... bash scripts/doctor.sh --probe` in a terminal from the notetaker root,
     which reports which region accepts the key.
4. If the MCP server itself is not listed under `/mcp`, tell the user to run `/reload-plugins`
   or restart Claude Code, then check `/mcp` → notetaker → logs, and paste any `[notetaker]`
   lines back here.

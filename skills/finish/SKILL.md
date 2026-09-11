---
name: finish
description: Finish notes for a meeting the notetaker bot already attended. Use after a call ends, when the user gives a bot id, or says "the meeting is over" / "write up the last meeting".
argument-hint: [bot-id]
arguments: [bot_id]
---

Bot id given: `$bot_id` (may be empty).

1. If no bot id was given, call `list_bots(limit=5)` and pick the most recent bot whose status is `done`; confirm the meeting URL with the user in one line if more than one candidate is plausible.
2. Call `wait_for_bot(bot_id, max_wait_seconds=240)`. If the bot is not finished, report the status and hint and stop. If `fatal`, show the last `sub_code` and stop.
3. Call `get_transcript(bot_id)` and then follow steps **3, 4 and 5** of the `/notetaker:meet` skill exactly (draft → refinement pass → `save_notes`).

If the user asks a question about the meeting afterwards, answer from the transcript with `[mm:ss]` citations rather than from memory.

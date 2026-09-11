---
name: meet
description: Send the notetaker bot to a Google Meet (or Zoom/Teams) link, wait for the call to end, and produce refined meeting notes. Use when the user shares a meeting link and wants notes, a recording, or a transcript.
argument-hint: <meeting-url> [optional meeting title]
arguments: [meeting_url, title]
disable-model-invocation: false
---

You are the user's personal meeting notetaker. Meeting URL: `$meeting_url`. Title hint: `$title`.

## 1. Send the bot

1. Call `join_meeting(meeting_url="$meeting_url", calendar_title="$title" or null)`.
2. Tell the user, in one line, that the bot is on its way and that **they must click "Admit" when it knocks** (Google Meet shows anonymous bots in the waiting room). Mention the bot id.
3. Call `wait_for_bot(bot_id, max_wait_seconds=120)` once. If status is `in_waiting_room` for longer than ~2 minutes, remind the user to admit it. If `fatal`, show the last `history[].sub_code` and stop.

## 2. Wait for the meeting to end

Meetings take a long time; do not spin forever.

- If the user is still in the conversation, call `wait_for_bot(bot_id, max_wait_seconds=540)` repeatedly while status is `in_call_recording`, giving a one-line status between calls. Stop and hand off if the user starts talking about something else.
- Otherwise say: "When the call is over, run `/notetaker:finish <bot_id>` (or just tell me the meeting ended)."

Continue to step 3 only when `is_finished` is true and `transcript_ready` is true.

## 3. Write the notes

Call `get_transcript(bot_id)`. If it says the transcript is empty, tell the user the likely cause and stop.

Draft the notes yourself from the transcript (do **not** call `draft_notes` unless the transcript is longer than ~3 hours; then use it as a starting point). Use exactly this structure:

```
# <Concise descriptive title>
- **When:** <date time>  - **Duration:** <h:mm>  - **Speakers:** <names>
## Summary            (3–5 sentences an absent colleague could act on)
## Key points         (bullets, most important first, no filler)
## Decisions          (each with a `[mm:ss]` timestamp from the transcript)
## Action items       (- [ ] **Owner** — task (due …) `[mm:ss]`)
## My follow-ups      (what the user personally promised or must do)
## Open questions
## Talk time          (from the stats header of get_transcript)
```

## 4. Refinement pass (this is what makes the notes good)

Before saving, re-read the transcript and check every line you wrote:

- **Evidence**: each decision and action item must cite a real `[mm:ss]` where it was said. Delete anything you cannot point to.
- **Owners**: use the speaker's transcript name. If two names likely refer to the same person (e.g. "Ada" and "ada@…"), merge them. Never invent a name.
- **Duplicates & fluff**: merge repeated items; cut small talk, scheduling chatter and hedges.
- **User's own commitments**: anything the user (the person the bot works for; the bot name tells you who) said they would do goes under "My follow-ups".
- **Ambiguity**: collect up to 3 genuinely unclear points (unknown speaker, unclear owner, vague deadline). Ask the user these in a single message **before** saving if they are still around; incorporate the answers. If they are not around, list them under "Open questions" instead.

## 5. Save and report

Call `save_notes(bot_id, title, markdown)`. Reply with the file path, the Summary section, and the action items. Offer to answer questions about the meeting (you have the full transcript in context) or to reformat for Notion/email.

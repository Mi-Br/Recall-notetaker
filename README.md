# Recall Notetaker

A personal assistant that joins my Google Meet calls as a named bot, records them through
[Recall.ai](https://www.recall.ai) (pay‑as‑you‑go), and turns the transcript into
Markdown notes (summary, decisions, action items, my follow‑ups) with Claude.

* **Design and option analysis:** [`docs/DESIGN.md`](docs/DESIGN.md)
* **API cheat‑sheet:** [`docs/RECALL_API_NOTES.md`](docs/RECALL_API_NOTES.md)

## Status

Phase 1 (ad hoc CLI) is implemented and unit‑tested, but **has not yet been run against
the live Recall API** — the environment it was written in blocks `*.recall.ai`.
First live run checklist is at the bottom of `docs/DESIGN.md`.

## Quick start

```bash
git clone git@github.com:Mi-Br/Recall-notetaker.git && cd Recall-notetaker
uv venv && uv pip install -e ".[dev]"          # or: python -m venv .venv && pip install -e ".[dev]"
cp .env.example .env                            # fill RECALL_API_KEY, ANTHROPIC_API_KEY, BOT_NAME
notetaker detect-region                         # prints the region for your key → put in .env

notetaker join https://meet.google.com/abc-defg-hij --wait
#  → bot appears in the waiting room, admit it
#  → CLI polls until the call ends and the transcript is ready
#  → notes/2026-09-11-1401-<title>.md  (+ raw .transcript.json next to it)
```

Other commands:

```bash
notetaker schedule <meet-url> 2026-09-12T14:00     # scheduled bot (join_at), ≥10 min ahead
notetaker list                                     # recent bots and statuses
notetaker status <bot-id>                          # status history (add --json for everything)
notetaker wait <bot-id>                            # resume waiting / write notes later
notetaker notes <bot-id>                           # regenerate notes (new prompt, no Recall cost)
notetaker cancel <bot-id>                          # delete a scheduled bot
```

Transcript provider is `--provider recallai_async` by default; `meeting_captions` is the
free option (needs captions on in Meet), `recallai_streaming` is for live use.

## How it works

```
notetaker join ──POST /api/v1/bot──▶ Recall.ai bot joins Meet ──▶ records
      │                                     │
      └──poll GET /api/v1/bot/{id}◀─────────┘  status: done + transcript ready
                 │
                 ├─ download transcript JSON → speaker paragraphs (transcript.py)
                 ├─ Claude (structured output → MeetingNotes)   (notes.py)
                 └─ notes/<date>-<title>.md                     (cli.py)
```

No public webhook endpoint is needed. `state/bots.json` remembers bots we created.

## Development

```bash
.venv/bin/python -m pytest -q
```

Recall's own MCP server (read‑only: bots, logs, docs) is handy while debugging with
Claude Code — copy `.mcp.json.example` to `.mcp.json` and export `RECALL_MCP_API_KEY`.

## Roadmap

1. ✅ Ad hoc CLI (this)
2. Auto‑join from my Google Calendar (own scheduler + `join_at`, GitHub Actions cron) — see DESIGN §3
3. Signed‑in bot for auto‑admission; Notion / email sinks; live mode

## Security

* Secrets live in `.env` (git‑ignored). Never commit API keys. Rotate any key that has been
  pasted into a chat or log.
* `notes/` and `state/` are git‑ignored: transcripts contain other people's words.

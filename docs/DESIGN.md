# Recall Notetaker — Design

Personal meeting assistant that joins my Google Meet calls, records them via
[Recall.ai](https://www.recall.ai) (Pay As You Go), and turns the transcript into
structured notes with Claude.

Status: **design + phase‑1 CLI implemented (unit‑tested, not yet run live)**. Nothing here has been run against the live
Recall API yet (the sandbox this was written in blocks `*.recall.ai`), so every
request shape below is taken from Recall's own sample repos and docs and must be
smoke‑tested once (see `docs/RECALL_API_NOTES.md`).

---

## 1. Goal and constraints

| Item | Decision |
|---|---|
| Who uses it | Just me (single user, single Google account). |
| Platform | Google Meet only for now (Recall also supports Zoom/Teams, free to add later). |
| Billing | Recall Pay As You Go: no platform fee, first 5 h free, then $0.50 / recorded hour, +$0.15 / hour for Recall transcription. Media kept 7 days free. |
| Notes engine | Claude API (Anthropic). |
| Hosting | Start with zero infrastructure (local CLI, polling). Public webhook endpoint is optional, not required. |
| Secrets | Never in git. `.env` locally, GitHub Actions secrets later. |

---

## 2. Options explored

### 2.1 How the bot gets *into* the meeting

| # | Option | Effort | Pros | Cons |
|---|---|---|---|---|
| A | **Ad hoc**: I paste the Meet URL → `POST /api/v1/bot` → bot joins now | tiny | Works today; nothing to host | Manual step per meeting |
| B | **Own scheduler**: read *my* Google Calendar (Google Calendar API, single‑user OAuth), find events with a `meet.google.com` link, create bots with `join_at` | small | Full control, no webhooks needed, runs fine as a cron job (GitHub Actions / laptop) | I own dedup + reschedule/cancel logic (~100 lines) |
| C | **Recall Calendar V2 integration**: register my Google OAuth client with Recall, connect calendar, receive `calendar.sync_events` webhooks, call *Schedule Bot For Calendar Event* | medium | Free, Recall handles reschedules/cancellations/dedup, attaches event metadata (title, attendees) to recording | Needs a 24/7 public webhook receiver + Google OAuth consent screen setup; designed for multi‑user SaaS |
| D | **Recall Desktop Recording SDK**: record from my Mac, *no bot in the call* | large | Invisible, no admission problem, same $/h | Must build/sign a desktop app (Electron/Swift), Mac must be on |
| E | **Google Meet REST API** native recordings/transcripts (no Recall) | medium | No per‑hour Recall cost | Requires Workspace Business Standard+ and Gemini transcription; artifacts only after the call; not usable on personal Gmail |

**Decision:** A now → B next. C only if this ever becomes multi‑user. D/E kept as
future alternatives.

### 2.2 The Google Meet *admission* problem (important)

By default a Recall bot joins Google Meet as an **anonymous guest** and sits in the
waiting room until a host/co‑host admits it. Consequences:

* Meetings **I host**: I click *Admit* once. Fine.
* Meetings **someone else hosts** (with Host Controls on): only they can admit. The bot
  waits `automatic_leave.waiting_room_timeout` (default 1200 s) then leaves.

Ways to remove the manual step, in order of cost:

1. Keep it anonymous, set a short waiting‑room timeout, accept the click. **(Phase 1)**
2. **Signed‑in bot**: give Recall Google credentials for a *dedicated* Google account
   (`google_meet.login_required: true`). If that account is on the calendar invite it
   skips the waiting room. Recall recommends a fresh account with recovery email +
   phone; docs say a dedicated paid Workspace account is required. ≈ $7/mo extra.
   Combine with option B/C so the bot's address is auto‑added to invites.
3. Desktop SDK (option D) — no admission at all.

### 2.3 Transcription provider

| Provider key | When | Cost (PAYG) | Notes |
|---|---|---|---|
| `recallai_async` | after the call | $0.15/h | Best accuracy for the money; diarized. **Default.** |
| `recallai_streaming` | live during call | $0.15/h | Only needed for real‑time features (live notes, alerts). Not needed for post‑meeting notes. |
| `meeting_captions` | live, scrapes Google Meet captions | free | Requires captions enabled in Meet; quality/diarization is what Google gives you. Good fallback / cost floor. |
| Deepgram / AssemblyAI / etc. via Recall | either | their price, own key | Only if Recall's own engine disappoints. |

### 2.4 Where Claude fits (and what "Agent Kickstart" actually is)

Recall's *Build with AI Agents* page offers (a) ready‑made prompts for coding agents and
(b) a **Recall MCP server**. The MCP server is **read‑only**: list/retrieve bots, bot logs,
search docs. It **cannot create a bot**, so Claude + MCP alone can't "start the recording".
That is the job of *this* project's code.

So Claude plays three separate roles:

1. **Build/debug time**: Claude Code with the Recall MCP attached (docs search, inspect a
   bot that failed). Template in `.mcp.json.example`, key from env.
2. **Interactive use**: *this repo is itself a Claude Code plugin* (`.claude-plugin/`,
   `skills/`, `recall_notetaker/mcp_server.py`). Our own MCP server exposes
   `join_meeting`, `wait_for_bot`, `get_transcript`, `save_notes`, … and the
   `/notetaker:meet` skill drives the flow. The Claude session writes and **refines** the
   notes itself (evidence check against timestamps, owner merge, clarifying questions),
   so no Anthropic API key is required for this path.
3. **Headless run time** (phase 2 cron): the Claude *API* summarises transcripts into
   notes via `notes.py`. Plain `anthropic` SDK call, structured output.

### 2.5 Receiving results: webhooks vs polling

* Recall pushes `bot.status_change` (`bot.done`), `recording.done`, `transcript.done`
  via Svix‑signed webhooks — requires a public HTTPS URL.
* For a single user, **polling `GET /api/v1/bot/{id}`** every 30–60 s until
  `status_changes[-1].code == "done"` is simpler and needs no public endpoint.

**Decision:** polling by default; webhook receiver kept as an optional FastAPI route for
when the service is hosted.

---

## 3. Recommended architecture

```
                 ┌──────────────────────────┐
  Meet URL /     │  recall_notetaker (py)   │
  calendar ────▶ │  cli.py  ─ join/schedule │──POST /api/v1/bot──▶ Recall.ai ──▶ joins Google Meet
                 │  scheduler.py (phase 2)  │                        │
                 │                          │◀─poll GET /bot/{id}────┘  (or webhook)
                 │  recall.py  ─ API client │
                 │  transcript.py ─ parse   │──GET transcript download_url
                 │  notes.py   ─ Claude     │──Claude API (Messages)
                 └────────────┬─────────────┘
                              ▼
                     notes/YYYY-MM-DD-<title>.md   (+ raw transcript JSON)
```

### Phase 1 — ad hoc CLI + Claude Code plugin (in this repo now)

```
notetaker join https://meet.google.com/abc-defg-hij --name "Michail's Notetaker"
notetaker wait <bot_id>          # poll until done, then fetch transcript + write notes
notetaker notes <bot_id>         # (re)generate notes for a finished bot
notetaker list                   # recent bots + status
```

Bot request (see `recall_notetaker/recall.py`):

```json
{
  "meeting_url": "https://meet.google.com/abc-defg-hij",
  "bot_name": "Michail's Notetaker",
  "recording_config": {
    "transcript": { "provider": { "recallai_async": { "language_code": "en" } } },
    "participant_events": {},
    "meeting_metadata": {},
    "start_recording_on": "participant_join"
  },
  "automatic_leave": {
    "waiting_room_timeout": 600,
    "noone_joined_timeout": 600,
    "everyone_left_timeout": 30
  },
  "metadata": { "source": "recall-notetaker", "event_id": "<calendar event id if any>" }
}
```

Notes pipeline: transcript JSON → speaker‑grouped paragraphs with `mm:ss` stamps →
Claude prompt (summary, decisions, action items with owner, open questions) →
Markdown file. Raw transcript JSON is saved alongside so notes can be regenerated
with a better prompt without paying Recall again (media expires after 7 days).

### Phase 2 — auto‑join from my calendar (option B)

* Google Calendar API, installed‑app OAuth, scope `calendar.readonly`, token cached
  locally / as a GitHub secret.
* Every 15 min: list events in the next 24 h with `hangoutLink` or a
  `meet.google.com` URL in location/description; skip declined; skip events already
  mapped in `state/bots.json`; for the rest `POST /api/v1/bot` with
  `join_at = start - 60s` (Recall wants ≥10 min ahead for guaranteed timing).
* On time/URL change → `PATCH /api/v1/bot/{id}` (Update Scheduled Bot); on cancel →
  `DELETE /api/v1/bot/{id}`.
* Same job also runs `wait`/`notes` for bots that finished since last run.
* Deployment target: **GitHub Actions `schedule` workflow in this private repo**,
  committing notes back to `notes/`. Zero servers. Fallback: `launchd` on the Mac.
* Opt‑out controls: a keyword in the event title (e.g. `[no-bot]`), or only events
  with ≥2 attendees.

### Phase 3 — nice‑to‑haves

* Signed‑in bot for auto‑admit (§2.2).
* Sinks: Notion page, Google Doc, email digest.
* Live mode with `recallai_streaming` + realtime webhooks.
* Recall Calendar V2 if anyone else wants to use it.

---

## 4. Cost estimate

Assume 20 meetings/month × 45 min = 15 recorded hours.

| Item | Rate | Monthly |
|---|---|---|
| Recall recording | $0.50 / h | $7.50 |
| Recall async transcription | $0.15 / h | $2.25 |
| Storage | fetch within 7 days → free | $0 |
| Claude notes (Opus 5: ~12k input × $5/M + ~1.5k output × $25/M ≈ $0.10 per meeting) | | ≈ $2 |
| Optional signed‑in bot Google account | Workspace seat | ≈ $7 |
| **Total (phase 1/2, no signed-in bot)** | | **≈ $12 / month** |

First 5 hours are free, so month one is roughly half that.

---

## 5. Security notes

* The Recall API key that was pasted into the chat that produced this design should be
  **rotated in the Recall dashboard** — anything pasted into a chat/log should be treated
  as leaked. It is not stored anywhere in this repo.
* `.env`, `state/`, `notes/` are git‑ignored by default (notes contain other people's
  words). Phase 2 may deliberately commit `notes/` — decide then.
* Recall webhooks (if enabled) must be verified with the Svix signature headers
  (`webhook-id`, `webhook-timestamp`, `webhook-signature`).
* Recording consent: the bot is visible in the call as a named participant; keep the name
  honest (e.g. "Michail's Notetaker").

---

## 6. Open questions / to verify on first live run

1. Exact Recall MCP endpoint URL and auth header for `.mcp.json` (docs page
   `docs.recall.ai/docs/docs-mcp` was unreachable from the build sandbox).
2. Confirm `recallai_async` is billed at the same $0.15/h as streaming on PAYG.
3. Which region the current API key belongs to (`us-east-1` / `us-west-2` /
   `eu-central-1` / `ap-northeast-1`). Run `notetaker list` against each until one
   returns 200.
4. Whether transcript entries expose participant *email* (needed to map action‑item
   owners); otherwise map names via calendar attendees in phase 2.

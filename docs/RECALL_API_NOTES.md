# Recall.ai API notes (as used by this project)

Everything here is distilled from Recall's public sample repos
(`recallai/meeting-bot`, `recallai/meeting-action-items-bot`) and docs search results.
**Items marked ⚠ were not verifiable from the build sandbox** (`*.recall.ai` blocked) and
must be confirmed on the first live run.

## Regions / auth

* Base URL: `https://{region}.recall.ai` — `us-east-1`, `us-west-2`, `eu-central-1`, `ap-northeast-1`.
  An API key belongs to exactly one region; `notetaker detect-region` finds it.
* Header: `Authorization: Token <API_KEY>`.

## Create Bot — `POST /api/v1/bot/`

```json
{
  "meeting_url": "https://meet.google.com/abc-defg-hij",
  "bot_name": "Michail's Notetaker",
  "join_at": "2026-09-12T12:00:00+00:00",
  "recording_config": {
    "transcript": {"provider": {"recallai_async": {"language_code": "en"}}},
    "participant_events": {},
    "meeting_metadata": {},
    "start_recording_on": "participant_join",
    "realtime_endpoints": [
      {"type": "webhook", "url": "https://…/wh", "events": ["transcript.data", "participant_events.join"]}
    ]
  },
  "automatic_leave": {
    "waiting_room_timeout": 600,
    "noone_joined_timeout": 600,
    "everyone_left_timeout": 30
  },
  "metadata": {"source": "recall-notetaker"},
  "google_meet": {"login_required": false}
}
```

* `join_at` omitted / < 10 min ahead → ad hoc bot joins immediately. ≥ 10 min ahead →
  scheduled bot (Recall guarantees on‑time join). Bots need boot + navigation time; set
  `join_at` 10–15 s early if exact timing matters.
* Transcript providers seen in samples: `recallai_streaming` (`mode`:
  `prioritize_accuracy` | `prioritize_low_latency`, `language_code`, `filter_profanity`),
  `meeting_captions` (`{}`), `recallai_async` ⚠ (key name from docs search; confirm exact
  options).
* `automatic_leave` defaults: `waiting_room_timeout` 1200, `noone_joined_timeout` 1200,
  `everyone_left_timeout` 2, `in_call_not_recording_timeout` 3600,
  `recording_permission_denied_timeout` 30 (seconds).
* `google_meet.login_required: true` forces a signed‑in bot (needs bot Google credentials
  configured in the Recall dashboard) — see DESIGN §2.2.

## Retrieve Bot — `GET /api/v1/bot/{id}/`

Relevant parts of the response:

```json
{
  "id": "…",
  "meeting_url": {"meeting_id": "abc-defg-hij", "platform": "google_meet"},
  "status_changes": [
    {"code": "joining_call", "created_at": "…", "sub_code": null, "message": null},
    {"code": "in_waiting_room", …},
    {"code": "in_call_not_recording", …},
    {"code": "in_call_recording", …},
    {"code": "call_ended", …},
    {"code": "done", …}
  ],
  "recordings": [{
    "id": "…", "started_at": "…", "completed_at": "…",
    "media_shortcuts": {
      "transcript":         {"status": {"code": "done"}, "data": {"download_url": "https://…"}},
      "participant_events": {"status": {"code": "done"}, "data": {"participants_download_url": "https://…"}},
      "video_mixed":        {"status": {"code": "done"}, "data": {"download_url": "https://…"}}
    }
  }]
}
```

Status codes (from the `bot.status_change` webhook list): `joining_call`, `in_waiting_room`,
`in_call_not_recording`, `recording_permission_allowed`, `recording_permission_denied`,
`in_call_recording`, `call_ended`, `done`, `fatal`. Terminal: `done`, `fatal`.

`download_url`s are pre‑signed and expire; fetch promptly. Media is kept 7 days on PAYG.

## Transcript download (JSON)

```json
[
  {"participant": {"id": 100, "name": "Ada", "email": null, "is_host": true, "platform": "…", "extra_data": {}},
   "words": [{"text": "Hello", "start_timestamp": {"relative": 1.2, "absolute": "…"},
              "end_timestamp": {"relative": 1.5, "absolute": "…"}}]}
]
```

`relative` is seconds from recording start. Parsed by `recall_notetaker/transcript.py`.

## Other endpoints

| Purpose | Call |
|---|---|
| Update scheduled bot (time/URL) | `PATCH /api/v1/bot/{id}/` |
| Delete scheduled bot | `DELETE /api/v1/bot/{id}/` |
| List bots | `GET /api/v1/bot/?limit=N` (paginated: `results`, `next`) ⚠ |
| Legacy transcript | `GET /api/v1/bot/{id}/transcript` (used in the action‑items sample; prefer `media_shortcuts`) |
| Re‑transcribe a recording asynchronously | `POST /api/v1/recording/{recording_id}/create_transcript/` with `{"provider": {"recallai_async": {…}}}` ⚠ |

## Webhooks (optional, not used in phase 1)

* Configure endpoints in dashboard → Webhooks. Events: `bot.status_change`,
  `recording.done`, `transcript.done`, `transcript.failed`, calendar events (V2).
* Delivered via Svix; verify with headers `webhook-id`, `webhook-timestamp`,
  `webhook-signature` and the endpoint's signing secret (`svix` library). Reject if the
  timestamp is > 5 min old.
* Bot‑level realtime endpoints (`recording_config.realtime_endpoints`) push
  `transcript.data`, `participant_events.*` to your URL during the call.

## Calendar V2 (phase 3 option, not used)

* Google OAuth client with scopes `calendar.events.readonly` + `userinfo.email`;
  give Recall the client id/secret; connect a calendar with the user's refresh token.
* Webhooks `calendar.update`, `calendar.sync_events` → list events →
  *Schedule Bot For Calendar Event* / *Delete Bot From Calendar Event*.
* Free on all plans.

## Recall MCP (dev/debug only)

Read‑only tools: list bots (by time window/status/metadata), retrieve bot, bot logs,
list workspaces, search/get docs. Auth: OAuth (interactive) or a scoped MCP API key sent as
a Bearer token to the regional endpoint. Cannot create bots. Template in `.mcp.json.example` ⚠.

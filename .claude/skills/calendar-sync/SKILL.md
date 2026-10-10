---
name: calendar-sync
description: Book the notetaker bot (Angela) into every upcoming video meeting on the user's calendars, moving or cancelling earlier bookings that changed. Use for the daily calendar job, or when the user asks to "schedule Angela for my meetings" or "sync my calendar".
disable-model-invocation: false
---

Goal: every video meeting in the next 30 hours on the user's personal and Werkzet calendars
has exactly one Recall bot booked; changed or cancelled meetings have their bot moved or
removed. This often runs unattended as a scheduled routine: never ask questions, finish
with a short report.

## 1. Read the calendars (Google Calendar connector)

1. List the calendars visible to the connected Google account.
2. Choose which to read:
   - if the environment variable `SYNC_CALENDARS` is set, read exactly those (comma-separated
     calendar names or ids, matched case-insensitively);
   - otherwise read `michailbredichin@gmail.com` (key `personal`) and `michail@werkzet.com`
     (key `werkzet`), and skip Todoist, Family, holidays, birthdays and other feeds.
3. For each chosen calendar, list events from now until 30 hours from now with recurring
   events expanded into single instances. Keep each event's id, summary, start (dateTime
   with offset, or date for all-day), end, hangoutLink, conferenceData, location,
   description, status and attendees (with the `self` flag and responseStatus).
4. If the connector reports `accessRole: freeBusyReader` for a calendar, its events have no
   titles or meeting links, so nothing on it can be booked. Still include it, but say in the
   report that the calendar is shared as free/busy only and needs "See all event details".
5. If a calendar fails to load, leave it out of the file and mention it in the report.
   Never send an empty list for a calendar you could not read: an empty list cancels its
   bookings.

## 2. Book the bots

Write the events to a JSON file, keyed by a short stable name per calendar (for example
`personal`, `werkzet`; keep the same names every run, they are part of how bookings are
recognised):

```json
{"calendars": {"personal": [ ...events... ], "werkzet": [ ...events... ]}}
```

Then from the repository root run:

```bash
uv run notetaker sync-calendar /tmp/calendar-events.json
```

If `uv` is missing: `python3 -m venv /tmp/v && /tmp/v/bin/pip install -q . &&
/tmp/v/bin/notetaker sync-calendar /tmp/calendar-events.json`.

The command is idempotent. It books a bot for each new meeting, joining one minute early,
moves bots whose meeting moved, cancels bots whose meeting vanished, was declined or opted
out with `[no-angela]` in the title or description, books one bot when the same call is on
both calendars, and skips meetings without a video link. It prints a JSON report.

If it fails with a network or 401 error, run `bash scripts/doctor.sh` and include its
FAIL lines in the report.

## 3. Report

Finish with a short plain-text message (it may be pushed to the user's phone):

- first line: `Angela: N booked, N moved, N cancelled` (add `, N errors` if any);
- then one line per booked or moved meeting: local time (Europe/Amsterdam), title;
- then any errors or calendars that could not be read.

If nothing changed and there were no errors, a single line is enough, e.g.
`Angela: no changes, 3 meetings already booked today.`

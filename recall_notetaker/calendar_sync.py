"""Book the notetaker bot into upcoming calendar meetings, idempotently.

Input is the calendars as read by Claude through the Google Calendar connector:

    {"calendars": {"personal": [<event>, ...], "werkzet": [<event>, ...]}}

where <event> is either a raw Google Calendar event (start.dateTime, hangoutLink,
conferenceData, attendees[].self/responseStatus, ...) or a flat dict:
    {"id", "title", "start", "end", "meeting_url", "response_status", "all_day"}.

Every bot this module books carries metadata {"source": "calendar-sync", "calendar",
"event_id"}, so each run can find its own earlier bookings in Recall (no local state):
new meetings get a bot, moved meetings get their bot moved, and bots whose meeting
disappeared or was declined are cancelled. Calendars missing from the input are left
alone, so a calendar that failed to load never loses its bookings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .recall import RecallClient, RecallError

SOURCE = "calendar-sync"
OPT_OUT = re.compile(r"\[(no[- ]?angela|no[- ]?bot|no[- ]?notes)\]|#no(angela|bot|notes)\b", re.I)
MEETING_URL = re.compile(
    r"https://(?:meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}"
    r"|[\w.-]*zoom\.us/j/[^\s\"'<>]+"
    r"|teams\.microsoft\.com/l/meetup-join/[^\s\"'<>]+"
    r"|teams\.live\.com/meet/[^\s\"'<>]+)",
    re.I,
)
JOIN_EARLY = timedelta(minutes=1)


@dataclass
class Meeting:
    calendar: str
    event_id: str
    title: str
    start: datetime
    meeting_url: str

    @property
    def join_at(self) -> datetime:
        return self.start - JOIN_EARLY


@dataclass
class SyncReport:
    created: list[dict[str, Any]] = field(default_factory=list)
    moved: list[dict[str, Any]] = field(default_factory=list)
    cancelled: list[dict[str, Any]] = field(default_factory=list)
    unchanged: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("created", "moved", "cancelled", "unchanged", "skipped", "errors")}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date_time")
        if not value:
            return None  # {"date": ...} = all-day
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else None  # naive times are ambiguous: refuse them


def _find_url(event: dict[str, Any]) -> str | None:
    for key in ("meeting_url", "hangoutLink", "hangout_link", "conferenceUrl", "conference_url"):
        if event.get(key):
            m = MEETING_URL.search(str(event[key]))
            if m:
                return m.group(0)
    conf = event.get("conferenceData") or {}
    video = (conf.get("videoEntryPoint") or {}).get("uri")  # Claude's Google Calendar connector
    if video:
        m = MEETING_URL.search(video)
        if m:
            return m.group(0)
    for ep in (conf.get("entryPoints") or []):
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            m = MEETING_URL.search(ep["uri"])
            if m:
                return m.group(0)
    for key in ("location", "description"):
        m = MEETING_URL.search(str(event.get(key) or ""))
        if m:
            return m.group(0)
    return None


def _declined(event: dict[str, Any]) -> bool:
    if str(event.get("response_status", "")).lower() == "declined":
        return True
    if str(event.get("status", "")).lower() == "cancelled":
        return True
    for a in event.get("attendees") or []:
        if a.get("self") and str(a.get("responseStatus", "")).lower() == "declined":
            return True
    return False


def plan(calendars: dict[str, Iterable[dict[str, Any]]], *, now: datetime, hours: float,
         report: SyncReport) -> list[Meeting]:
    """Turn raw events into the meetings that should have a bot."""
    horizon = now + timedelta(hours=hours)
    wanted: list[Meeting] = []
    seen: dict[tuple[str, str], Meeting] = {}
    for cal, events in calendars.items():
        for ev in events or []:
            title = str(ev.get("title") or ev.get("summary") or "(no title)")
            eid = str(ev.get("id") or ev.get("event_id") or "")
            info = {"calendar": cal, "event_id": eid, "title": title}
            start = _parse_dt(ev.get("start"))
            if ev.get("all_day") or start is None:
                report.skipped.append({**info, "reason": "all-day or no start time with zone"})
                continue
            if not eid:
                report.skipped.append({**info, "reason": "no event id"})
                continue
            if start <= now:
                report.skipped.append({**info, "reason": "already started"})
                continue
            if start > horizon:
                continue  # outside this run's window; a later run books it
            url = _find_url(ev)
            if not url:
                report.skipped.append({**info, "reason": "no video meeting link"})
                continue
            if _declined(ev):
                report.skipped.append({**info, "reason": "declined or cancelled"})
                continue
            if OPT_OUT.search(title) or OPT_OUT.search(str(ev.get("description") or "")):
                report.skipped.append({**info, "reason": "opted out ([no-angela])"})
                continue
            # the same call on both calendars: book one bot
            key = (url.lower(), start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"))
            if key in seen:
                report.skipped.append({**info, "reason": f"same meeting as {seen[key].calendar}:{seen[key].title}"})
                continue
            m = Meeting(cal, eid, title, start, url)
            seen[key] = m
            wanted.append(m)
    return wanted


def _existing(client: RecallClient, now: datetime) -> list[dict[str, Any]]:
    """Future bots booked by this job (Recall filters by join time and metadata)."""
    params = {"join_at_after": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
              "metadata__source": SOURCE, "limit": 100}
    out: list[dict[str, Any]] = []
    page = client._request("GET", "/api/v1/bot/", params=params)
    for _ in range(20):
        out.extend(page.get("results") or [])
        nxt = page.get("next")
        if not nxt:
            break
        page = client._request("GET", nxt)
    return out


def sync(client: RecallClient, calendars: dict[str, Iterable[dict[str, Any]]], *,
         now: datetime | None = None, hours: float = 30, dry_run: bool = False) -> SyncReport:
    now = now or datetime.now(timezone.utc)
    report = SyncReport()
    wanted = plan(calendars, now=now, hours=hours, report=report)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for bot in _existing(client, now):
        md = bot.get("metadata") or {}
        by_key[(md.get("calendar", ""), md.get("event_id", ""))] = bot

    for m in wanted:
        info = {"calendar": m.calendar, "event_id": m.event_id, "title": m.title,
                "start": m.start.isoformat(), "meeting_url": m.meeting_url}
        bot = by_key.pop((m.calendar, m.event_id), None)
        try:
            if bot is None:
                join_at = m.join_at if m.join_at > now else None  # starts in under a minute: go now
                if not dry_run:
                    bot = client.create_bot(m.meeting_url, join_at=join_at, metadata={
                        "source": SOURCE, "calendar": m.calendar, "event_id": m.event_id, "title": m.title[:200]})
                report.created.append({**info, "bot_id": (bot or {}).get("id")})
                continue
            old_at = _parse_dt(bot.get("join_at"))
            old_url = _bot_url(bot)
            if old_at and abs((old_at - m.join_at).total_seconds()) <= 60 and old_url == m.meeting_url.lower():
                report.unchanged.append({**info, "bot_id": bot["id"]})
                continue
            if not dry_run:
                client.update_scheduled_bot(bot["id"], join_at=m.join_at, meeting_url=m.meeting_url)
            report.moved.append({**info, "bot_id": bot["id"], "was": bot.get("join_at")})
        except RecallError as e:
            report.errors.append({**info, "error": str(e)[:500]})

    # bots left over belong to meetings that vanished, were declined or opted out
    for (cal, eid), bot in by_key.items():
        if cal not in calendars:
            continue  # that calendar was not read this run: leave its bookings alone
        info = {"calendar": cal, "event_id": eid, "title": (bot.get("metadata") or {}).get("title", ""),
                "bot_id": bot["id"], "join_at": bot.get("join_at")}
        try:
            if not dry_run:
                client.delete_bot(bot["id"])
            report.cancelled.append(info)
        except RecallError as e:
            report.errors.append({**info, "error": str(e)[:500]})
    return report


def _bot_url(bot: dict[str, Any]) -> str:
    url = bot.get("meeting_url")
    if isinstance(url, dict) and url.get("platform") == "google_meet" and url.get("meeting_id"):
        return f"https://meet.google.com/{url['meeting_id']}".lower()
    return str(url or "").lower()

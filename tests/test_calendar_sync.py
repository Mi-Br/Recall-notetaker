import json
from datetime import datetime, timedelta, timezone

import httpx

from recall_notetaker.calendar_sync import sync
from recall_notetaker.config import Settings
from recall_notetaker.recall import RecallClient

NOW = datetime(2026, 10, 12, 4, 7, tzinfo=timezone.utc)


def iso(hours: float) -> str:
    return (NOW + timedelta(hours=hours)).astimezone(timezone(timedelta(hours=2))).isoformat()


class FakeRecall:
    """In-memory Recall: list (with join_at_after + metadata__source), create, patch, delete."""

    def __init__(self):
        self.bots: dict[str, dict] = {}
        self.n = 0
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        if method == "GET" and path == "/api/v1/bot/":
            after = datetime.fromisoformat(request.url.params["join_at_after"].replace("Z", "+00:00"))
            src = request.url.params.get("metadata__source")
            res = [b for b in self.bots.values()
                   if b["join_at"] and datetime.fromisoformat(b["join_at"]) > after
                   and (not src or b["metadata"].get("source") == src)]
            return httpx.Response(200, json={"count": len(res), "next": None, "previous": None, "results": res})
        if method == "POST" and path == "/api/v1/bot/":
            body = json.loads(request.content)
            self.n += 1
            bid = f"bot-{self.n}"
            self.bots[bid] = {"id": bid, "join_at": body.get("join_at"), "meeting_url": body["meeting_url"],
                              "metadata": body["metadata"]}
            return httpx.Response(201, json=self.bots[bid])
        bid = path.rstrip("/").split("/")[-1]
        if method == "PATCH":
            self.bots[bid].update(json.loads(request.content))
            return httpx.Response(200, json=self.bots[bid])
        if method == "DELETE":
            self.bots.pop(bid)
            return httpx.Response(204)
        return httpx.Response(404)

    def client(self) -> RecallClient:
        return RecallClient(Settings(recall_api_key="k", recall_region="eu-central-1"),
                            transport=httpx.MockTransport(self.handler))


def google_event(eid, hours, link="https://meet.google.com/abc-defg-hij", title="Client call", **extra):
    ev = {"id": eid, "summary": title, "start": {"dateTime": iso(hours)}, "end": {"dateTime": iso(hours + 1)}}
    if link:
        ev["hangoutLink"] = link
    ev.update(extra)
    return ev


def test_books_moves_and_cancels_idempotently():
    fake = FakeRecall()
    cal = {"werkzet": [google_event("e1", 3), google_event("e2", 5, link="https://meet.google.com/xyz-abcd-efg")],
           "personal": [google_event("p1", 8, link=None, location="https://meet.google.com/ppp-qqqq-rrr")]}
    r = sync(fake.client(), cal, now=NOW)
    assert len(r.created) == 3 and not r.errors
    assert {b["metadata"]["event_id"] for b in fake.bots.values()} == {"e1", "e2", "p1"}
    e1 = next(b for b in fake.bots.values() if b["metadata"]["event_id"] == "e1")
    assert datetime.fromisoformat(e1["join_at"]) == NOW + timedelta(hours=3) - timedelta(minutes=1)
    assert e1["metadata"] == {"source": "calendar-sync", "calendar": "werkzet", "event_id": "e1", "title": "Client call"}

    # second run with the same calendars changes nothing
    r2 = sync(fake.client(), cal, now=NOW)
    assert len(r2.unchanged) == 3 and not (r2.created or r2.moved or r2.cancelled)

    # e1 moved by an hour, e2 removed: move one bot, cancel the other
    cal["werkzet"] = [google_event("e1", 4)]
    r3 = sync(fake.client(), cal, now=NOW)
    assert [m["event_id"] for m in r3.moved] == ["e1"]
    assert [c["event_id"] for c in r3.cancelled] == ["e2"]
    assert len(fake.bots) == 2


def test_calendar_missing_from_input_keeps_its_bots():
    fake = FakeRecall()
    sync(fake.client(), {"werkzet": [google_event("e1", 3)], "personal": [google_event("p1", 4, link="https://meet.google.com/ppp-qqqq-rrr")]}, now=NOW)
    r = sync(fake.client(), {"werkzet": [google_event("e1", 3)]}, now=NOW)  # personal failed to load
    assert not r.cancelled and len(fake.bots) == 2


def test_skips_and_dedupes():
    fake = FakeRecall()
    me_declined = {"attendees": [{"email": "m@x", "self": True, "responseStatus": "declined"}]}
    cal = {
        "werkzet": [
            google_event("ok", 2),
            google_event("nolink", 2.5, link=None),
            google_event("declined", 3, link="https://meet.google.com/ddd-dddd-ddd", **me_declined),
            google_event("optout", 3.5, link="https://meet.google.com/ooo-oooo-ooo", title="1:1 [no-angela]"),
            google_event("past", -1, link="https://meet.google.com/ppp-pppp-ppp"),
            google_event("far", 48, link="https://meet.google.com/fff-ffff-fff"),
            {"id": "allday", "summary": "Holiday", "start": {"date": "2026-10-12"}},
            google_event("cancelled", 4, link="https://meet.google.com/ccc-cccc-ccc", status="cancelled"),
        ],
        # same call also on the personal calendar: one bot only
        "personal": [google_event("dup", 2)],
    }
    r = sync(fake.client(), cal, now=NOW)
    assert [c["event_id"] for c in r.created] == ["ok"]
    reasons = {s["event_id"]: s["reason"] for s in r.skipped}
    assert "no video meeting link" in reasons["nolink"]
    assert "declined" in reasons["declined"] and "declined" in reasons["cancelled"]
    assert "opted out" in reasons["optout"]
    assert reasons["past"] == "already started"
    assert "all-day" in reasons["allday"]
    assert reasons["dup"].startswith("same meeting as werkzet")
    assert "far" not in reasons  # outside the window, picked up by a later run


def test_flat_events_and_dry_run():
    fake = FakeRecall()
    cal = {"werkzet": [{"id": "f1", "title": "Flat", "start": iso(2), "meeting_url": "https://meet.google.com/abc-defg-hij"},
                       {"id": "naive", "title": "No zone", "start": "2026-10-12T10:00:00", "meeting_url": "https://meet.google.com/abc-defg-hij"}]}
    r = sync(fake.client(), cal, now=NOW, dry_run=True)
    assert [c["event_id"] for c in r.created] == ["f1"] and not fake.bots
    assert any(s["event_id"] == "naive" for s in r.skipped)
    assert not any(c.startswith("POST") for c in fake.calls)


def test_connector_event_format():
    """Shape returned by Claude's Google Calendar connector (seen live 2026-10-10)."""
    fake = FakeRecall()
    ev = {"id": "c1", "summary": "Client", "start": {"dateTime": iso(3), "timeZone": "Europe/Amsterdam"},
          "conferenceData": {"solutionName": "Google Meet", "videoEntryPoint": {"uri": "https://meet.google.com/hge-ghmm-uod"}},
          "conferenceUrl": "https://meet.google.com/hge-ghmm-uod", "status": "confirmed"}
    only_video = {k: v for k, v in ev.items() if k != "conferenceUrl"} | {"id": "c2", "start": {"dateTime": iso(4)},
                  "conferenceData": {"videoEntryPoint": {"uri": "https://meet.google.com/aaa-bbbb-ccc"}}}
    # private event shared as free/busy only: no title, no link
    busy = {"id": "b1", "start": {"dateTime": iso(5)}, "status": "confirmed", "visibility": "private"}
    r = sync(fake.client(), {"personal": [ev, only_video], "werkzet": [busy]}, now=NOW)
    assert sorted(c["event_id"] for c in r.created) == ["c1", "c2"]
    assert any(s["event_id"] == "b1" and "no video meeting link" in s["reason"] for s in r.skipped)

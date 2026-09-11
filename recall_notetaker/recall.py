"""Thin client for the Recall.ai Bot API (v1).

Endpoints used (all relative to https://{region}.recall.ai):
  POST   /api/v1/bot/                         create (ad hoc or scheduled via join_at)
  GET    /api/v1/bot/{id}/                    retrieve (status_changes, recordings, media_shortcuts)
  PATCH  /api/v1/bot/{id}/                    update a scheduled bot (join_at, meeting_url, ...)
  DELETE /api/v1/bot/{id}/                    delete a scheduled bot
  GET    /api/v1/bot/?limit=N                 list bots
  POST   /api/v1/recording/{id}/create_transcript/   re-transcribe asynchronously

Request shapes follow Recall's own sample repos; see docs/RECALL_API_NOTES.md.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

import httpx

from .config import Settings

TERMINAL_STATUSES = {"done", "fatal"}


class RecallError(RuntimeError):
    pass


@dataclass
class BotSummary:
    id: str
    meeting_url: str | None
    status: str | None
    join_at: str | None
    created_at: str | None

    @classmethod
    def from_api(cls, bot: dict[str, Any]) -> "BotSummary":
        return cls(
            id=bot["id"],
            meeting_url=_meeting_url(bot),
            status=latest_status(bot),
            join_at=bot.get("join_at"),
            created_at=bot.get("created_at"),
        )


def _meeting_url(bot: dict[str, Any]) -> str | None:
    url = bot.get("meeting_url")
    if isinstance(url, dict):
        # Google Meet: {"meeting_id": "abc-defg-hij", "platform": "google_meet"}
        mid = url.get("meeting_id")
        if url.get("platform") == "google_meet" and mid:
            return f"https://meet.google.com/{mid}"
        return str(url)
    return url


def latest_status(bot: dict[str, Any]) -> str | None:
    changes = bot.get("status_changes") or []
    return changes[-1].get("code") if changes else None


def transcript_shortcut(bot: dict[str, Any]) -> dict[str, Any] | None:
    """Return recordings[0].media_shortcuts.transcript or None."""
    for rec in bot.get("recordings") or []:
        shortcut = (rec.get("media_shortcuts") or {}).get("transcript")
        if shortcut:
            return shortcut
    return None


def transcript_download_url(bot: dict[str, Any]) -> str | None:
    shortcut = transcript_shortcut(bot)
    if not shortcut:
        return None
    if (shortcut.get("status") or {}).get("code") not in (None, "done"):
        return None
    return (shortcut.get("data") or {}).get("download_url")


def participants_download_url(bot: dict[str, Any]) -> str | None:
    for rec in bot.get("recordings") or []:
        pe = (rec.get("media_shortcuts") or {}).get("participant_events") or {}
        url = (pe.get("data") or {}).get("participants_download_url")
        if url:
            return url
    return None


class RecallClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._http = httpx.Client(
            base_url=settings.recall_base_url,
            headers={
                "Authorization": f"Token {settings.recall_api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30,
            transport=transport,
        )
        # Downloads go to pre-signed S3 URLs: no auth header, separate client.
        self._download = httpx.Client(timeout=60, transport=transport)

    # -- helpers -----------------------------------------------------------

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        r = self._http.request(method, path, **kw)
        if r.status_code >= 400:
            raise RecallError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # -- bot config --------------------------------------------------------

    def build_bot_payload(
        self,
        meeting_url: str,
        *,
        join_at: datetime | None = None,
        bot_name: str | None = None,
        provider: str = "recallai_async",
        metadata: dict[str, str] | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        """Assemble the Create Bot body.

        provider: "recallai_async" (default, post-call), "recallai_streaming"
                  (live) or "meeting_captions" (free, scrapes Meet captions).
        """
        s = self.settings
        if provider == "meeting_captions":
            provider_cfg: dict[str, Any] = {"meeting_captions": {}}
        elif provider == "recallai_streaming":
            provider_cfg = {"recallai_streaming": {"language_code": s.transcript_language,
                                                   "mode": "prioritize_accuracy"}}
        elif provider == "recallai_async":
            provider_cfg = {"recallai_async": {"language_code": s.transcript_language}}
        else:
            raise ValueError(f"unknown transcript provider {provider!r}")

        recording_config: dict[str, Any] = {
            "transcript": {"provider": provider_cfg},
            "participant_events": {},
            "meeting_metadata": {},
            "start_recording_on": "participant_join",
        }
        if webhook_url:
            recording_config["realtime_endpoints"] = [
                {"type": "webhook", "url": webhook_url, "events": ["transcript.data"]}
            ]

        body: dict[str, Any] = {
            "meeting_url": meeting_url,
            "bot_name": bot_name or s.bot_name,
            "recording_config": recording_config,
            "automatic_leave": {
                "waiting_room_timeout": s.waiting_room_timeout,
                "noone_joined_timeout": s.noone_joined_timeout,
                "everyone_left_timeout": s.everyone_left_timeout,
            },
            "metadata": {"source": "recall-notetaker", **(metadata or {})},
        }
        if join_at is not None:
            body["join_at"] = join_at.isoformat()
        return body

    # -- bot lifecycle -----------------------------------------------------

    def create_bot(self, meeting_url: str, **kw: Any) -> dict[str, Any]:
        return self._request("POST", "/api/v1/bot/", json=self.build_bot_payload(meeting_url, **kw))

    def get_bot(self, bot_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/bot/{bot_id}/")

    def update_scheduled_bot(self, bot_id: str, **fields: Any) -> dict[str, Any]:
        if "join_at" in fields and isinstance(fields["join_at"], datetime):
            fields["join_at"] = fields["join_at"].isoformat()
        return self._request("PATCH", f"/api/v1/bot/{bot_id}/", json=fields)

    def delete_bot(self, bot_id: str) -> None:
        self._request("DELETE", f"/api/v1/bot/{bot_id}/")

    def list_bots(self, limit: int = 20) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/v1/bot/", params={"limit": limit})
        if isinstance(data, dict):
            return data.get("results") or []
        return data or []

    def create_async_transcript(self, recording_id: str, language_code: str | None = None) -> dict[str, Any]:
        """Re-transcribe a finished recording (e.g. captions came back empty)."""
        body = {"provider": {"recallai_async": {
            "language_code": language_code or self.settings.transcript_language}}}
        return self._request("POST", f"/api/v1/recording/{recording_id}/create_transcript/", json=body)

    # -- waiting / downloads ----------------------------------------------

    def wait_until_done(
        self,
        bot_id: str,
        *,
        poll_seconds: int = 30,
        timeout_seconds: int = 4 * 3600,
        on_status: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, Any]:
        """Poll Retrieve Bot until status is done/fatal AND the transcript is ready."""
        deadline = time.monotonic() + timeout_seconds
        last = None
        while True:
            bot = self.get_bot(bot_id)
            status = latest_status(bot)
            if status != last and on_status:
                on_status(status or "unknown")
                last = status
            if status == "fatal":
                return bot
            if status == "done" and (transcript_download_url(bot) or not transcript_shortcut(bot)):
                return bot
            if time.monotonic() > deadline:
                raise RecallError(f"timed out waiting for bot {bot_id} (last status {status})")
            sleep(poll_seconds)

    def download_json(self, url: str) -> Any:
        r = self._download.get(url)
        r.raise_for_status()
        return r.json()

    def fetch_transcript(self, bot: dict[str, Any]) -> list[dict[str, Any]]:
        url = transcript_download_url(bot)
        if not url:
            raise RecallError("transcript is not available for this bot (yet)")
        return self.download_json(url)

    def fetch_participants(self, bot: dict[str, Any]) -> list[dict[str, Any]]:
        url = participants_download_url(bot)
        return self.download_json(url) if url else []


def detect_region(api_key: str, regions: Iterable[str], transport: httpx.BaseTransport | None = None) -> str | None:
    """Try List Bots in each region; the first 200 wins."""
    with httpx.Client(timeout=15, transport=transport,
                      headers={"Authorization": f"Token {api_key}"}) as http:
        for region in regions:
            try:
                r = http.get(f"https://{region}.recall.ai/api/v1/bot/", params={"limit": 1})
            except httpx.HTTPError:
                continue
            if r.status_code == 200:
                return region
    return None

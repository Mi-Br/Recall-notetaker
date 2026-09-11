import json
from datetime import datetime, timezone

import httpx
import pytest

from recall_notetaker.config import Settings
from recall_notetaker.recall import (
    BotSummary,
    RecallClient,
    RecallError,
    detect_region,
    transcript_download_url,
)


def make_client(handler):
    settings = Settings(recall_api_key="k", recall_region="us-west-2", bot_name="Test Bot",
                        waiting_room_timeout=123)
    return RecallClient(settings, transport=httpx.MockTransport(handler))


def test_create_bot_payload_and_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "bot-1", "status_changes": []})

    client = make_client(handler)
    bot = client.create_bot("https://meet.google.com/abc-defg-hij",
                            join_at=datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc),
                            metadata={"event_id": "ev1"})
    assert bot["id"] == "bot-1"
    assert seen["url"] == "https://us-west-2.recall.ai/api/v1/bot/"
    assert seen["auth"] == "Token k"
    body = seen["body"]
    assert body["bot_name"] == "Test Bot"
    assert body["join_at"] == "2026-09-12T12:00:00+00:00"
    assert body["recording_config"]["transcript"]["provider"] == {"recallai_async": {"language_code": "en"}}
    assert body["automatic_leave"]["waiting_room_timeout"] == 123
    assert body["metadata"] == {"source": "recall-notetaker", "event_id": "ev1"}


def test_provider_variants():
    client = make_client(lambda r: httpx.Response(200, json={}))
    p = client.build_bot_payload("u", provider="meeting_captions")
    assert p["recording_config"]["transcript"]["provider"] == {"meeting_captions": {}}
    p = client.build_bot_payload("u", provider="recallai_streaming")
    assert "recallai_streaming" in p["recording_config"]["transcript"]["provider"]
    with pytest.raises(ValueError):
        client.build_bot_payload("u", provider="nope")


def test_error_raises():
    client = make_client(lambda r: httpx.Response(401, json={"detail": "bad key"}))
    with pytest.raises(RecallError) as ei:
        client.get_bot("x")
    assert "401" in str(ei.value)


def test_wait_until_done_waits_for_transcript(bot_done_json, transcript_json):
    calls = {"n": 0}
    processing = json.loads(json.dumps(bot_done_json))
    processing["recordings"][0]["media_shortcuts"]["transcript"]["status"]["code"] = "processing"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "files.example.test":
            return httpx.Response(200, json=transcript_json)
        calls["n"] += 1
        return httpx.Response(200, json=processing if calls["n"] == 1 else bot_done_json)

    client = make_client(handler)
    statuses = []
    bot = client.wait_until_done("bot", poll_seconds=0, on_status=statuses.append, sleep=lambda s: None)
    assert calls["n"] == 2
    assert statuses == ["done"]
    assert transcript_download_url(bot) == "https://files.example.test/transcript.json"
    assert len(client.fetch_transcript(bot)) == 5


def test_bot_summary_google_meet_url(bot_done_json):
    s = BotSummary.from_api(bot_done_json)
    assert s.meeting_url == "https://meet.google.com/abc-defg-hij"
    assert s.status == "done"


def test_detect_region():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.host.startswith("eu-central-1") else 403, json=[])

    assert detect_region("k", ["us-east-1", "eu-central-1"], transport=httpx.MockTransport(handler)) == "eu-central-1"
    assert detect_region("k", ["us-east-1"], transport=httpx.MockTransport(handler)) is None

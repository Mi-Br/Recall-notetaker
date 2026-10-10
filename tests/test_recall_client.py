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
    assert body["recording_config"]["transcript"]["provider"] == {
        "recallai_streaming": {"language_code": "en", "mode": "prioritize_accuracy"}}
    assert body["automatic_leave"]["waiting_room_timeout"] == 123
    assert body["metadata"] == {"source": "recall-notetaker", "event_id": "ev1"}


def test_provider_variants():
    client = make_client(lambda r: httpx.Response(200, json={}))
    p = client.build_bot_payload("u", provider="meeting_captions")
    assert p["recording_config"]["transcript"]["provider"] == {"meeting_captions": {}}
    with pytest.raises(ValueError):
        client.build_bot_payload("u", provider="recallai_async")  # Recall rejects it at Create Bot
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


def test_network_secret_mode_sends_no_auth_header_and_hints_on_401(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.delenv("RECALL_API_KEY", raising=False)
    monkeypatch.setattr("recall_notetaker.config.load_dotenv", lambda: None)
    settings = Settings.from_env()  # must not raise without a key in cloud sessions
    assert settings.recall_api_key == ""
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(401, json={"detail": "Authentication credentials were not provided."})

    client = RecallClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(RecallError, match="prefix 'Token'"):
        client.list_bots()
    assert seen["auth"] is None  # the proxy adds it; we must not send an empty "Token "


def test_key_required_outside_cloud(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.delenv("RECALL_API_KEY", raising=False)
    monkeypatch.setattr("recall_notetaker.config.load_dotenv", lambda: None)
    with pytest.raises(SystemExit, match="RECALL_API_KEY is not set"):
        Settings.from_env()


def test_bot_image_is_sent_as_camera_tile(tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    settings = Settings(recall_api_key="k", bot_image=img)
    body = RecallClient(settings).build_bot_payload("u")
    frame = body["automatic_video_output"]["in_call_recording"]
    assert frame["kind"] == "jpeg"
    import base64
    assert base64.b64decode(frame["b64_data"]) == img.read_bytes()
    assert body["automatic_video_output"]["in_call_not_recording"] == frame
    assert "automatic_video_output" not in RecallClient(Settings(recall_api_key="k")).build_bot_payload("u")


def test_bot_image_env(monkeypatch, tmp_path):
    from recall_notetaker.config import DEFAULT_BOT_IMAGE, _bot_image
    monkeypatch.delenv("BOT_IMAGE", raising=False)
    assert _bot_image() == (DEFAULT_BOT_IMAGE if DEFAULT_BOT_IMAGE.is_file() else None)
    monkeypatch.setenv("BOT_IMAGE", "none")
    assert _bot_image() is None
    monkeypatch.setenv("BOT_IMAGE", str(tmp_path / "missing.jpg"))
    assert _bot_image() is None


def test_output_image_posts_jpeg(tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0x")
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = RecallClient(Settings(recall_api_key="k", bot_image=img), transport=httpx.MockTransport(handler))
    client.output_image("bot-9")
    assert seen["path"] == "/api/v1/bot/bot-9/output_video/"
    assert seen["body"]["kind"] == "jpeg" and seen["body"]["b64_data"]


def test_bot_payload_records_mp3():
    body = RecallClient(Settings(recall_api_key="k")).build_bot_payload("u")
    assert body["recording_config"]["audio_mixed_mp3"] == {}


def test_media_urls_and_retranscribe(bot_done_json, transcript_json, tmp_path):
    bot = json.loads(json.dumps(bot_done_json))
    bot["recordings"][0]["media_shortcuts"]["audio_mixed"] = {"data": {"download_url": "https://files.example.test/a.mp3"}}
    bot["recordings"][0]["media_shortcuts"]["video_mixed"] = {"data": {"download_url": "https://files.example.test/v.mp4"}}
    polls = {"n": 0}

    def handler(request):
        if request.url.host == "files.example.test":
            if request.url.path.endswith(".json"):
                return httpx.Response(200, json=transcript_json)
            return httpx.Response(200, content=b"media-bytes")
        if request.method == "POST" and request.url.path == "/api/v1/recording/rec-1/create_transcript/":
            assert json.loads(request.content)["provider"]["recallai_async"]["language_code"] == "lt"
            return httpx.Response(201, json={"id": "tr-9", "status": {"code": "processing"}})
        if request.url.path == "/api/v1/transcript/tr-9/":
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(200, json={"id": "tr-9", "status": {"code": "processing"}})
            return httpx.Response(200, json={"id": "tr-9", "status": {"code": "done"},
                                             "data": {"download_url": "https://files.example.test/t.json"}})
        return httpx.Response(404)

    client = make_client(handler)
    urls = client.media_urls(bot)
    assert urls == {"mp3": "https://files.example.test/a.mp3", "mp4": "https://files.example.test/v.mp4"}
    path = client.download(urls["mp3"], tmp_path / "x.mp3")
    assert path.read_bytes() == b"media-bytes"
    res = client.retranscribe(bot, "lt", poll_seconds=0, sleep=lambda s: None)
    assert res["status"] == "done" and res["transcript_id"] == "tr-9" and len(res["words"]) == 5
    # resume path: a timeout returns the id with words=None
    polls["n"] = -5
    pending = client.retranscribe(bot, "lt", transcript_id="tr-9", max_wait_seconds=0, sleep=lambda s: None)
    assert pending == {"transcript_id": "tr-9", "status": "processing", "words": None}

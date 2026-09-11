import json

import httpx
import pytest

from recall_notetaker import mcp_server as srv
from recall_notetaker.config import Settings
from recall_notetaker.recall import RecallClient


@pytest.fixture
def wired(tmp_path, bot_done_json, transcript_json, monkeypatch):
    """Point the MCP module at a mocked Recall API and a temp notes/state dir."""
    created = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "files.example.test":
            return httpx.Response(200, json=transcript_json)
        if request.method == "POST" and request.url.path == "/api/v1/bot/":
            created["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": "new-bot", "status_changes": []})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path == "/api/v1/bot/":
            return httpx.Response(200, json={"results": [bot_done_json]})
        return httpx.Response(200, json=bot_done_json)

    settings = Settings(recall_api_key="k", recall_region="us-west-2", bot_name="Michail's Notetaker",
                        notes_dir=tmp_path / "notes", state_file=tmp_path / "state" / "bots.json")
    monkeypatch.setattr(srv, "_settings", settings)
    monkeypatch.setattr(srv, "_client", RecallClient(settings, transport=httpx.MockTransport(handler)))
    return created


def test_tools_registered():
    names = set(srv.server._tool_manager._tools)
    assert {"join_meeting", "wait_for_bot", "get_transcript", "save_notes", "list_bots"} <= names


def test_join_records_state(wired):
    out = srv.join_meeting("https://meet.google.com/abc-defg-hij", calendar_title="Budget sync")
    assert out["bot_id"] == "new-bot"
    assert wired["body"]["bot_name"] == "Michail's Notetaker"
    assert srv._state().bots["new-bot"]["calendar_title"] == "Budget sync"


def test_wait_and_status(wired):
    view = srv.wait_for_bot("bot-1", max_wait_seconds=0)
    assert view["is_finished"] and view["transcript_ready"]
    assert view["history"][-1]["code"] == "done"
    assert view["meeting_url"] == "https://meet.google.com/abc-defg-hij"


def test_get_transcript_saves_raw_and_formats(wired):
    text = srv.get_transcript("0f3d1a2b-1111-2222-3333-444455556666")
    assert "Speakers: Ada" in text
    assert "[01:05] Bob: I'll send the budget Friday." in text
    raw_files = list(srv._s().notes_dir.glob("*.transcript.json"))
    assert len(raw_files) == 1 and "0f3d1a2b" in raw_files[0].name


def test_save_notes_default_and_custom_dir(wired, tmp_path):
    out = srv.save_notes("bot-1", "Budget sync", "# Budget sync\n\nhello")
    assert out["notes_path"].endswith("2026-09-11-0901-budget-sync.md")
    assert srv._state().bots["bot-1"]["notes_path"] == out["notes_path"]
    out2 = srv.save_notes("bot-1", "Budget sync", "# again", output_dir=str(tmp_path / "proj" / "notes"))
    assert (tmp_path / "proj" / "notes" / "2026-09-11-0901-budget-sync.md").read_text() == "# again\n"
    assert out2["previous_notes"] == out["notes_path"]


def test_draft_notes_without_anthropic_key(wired):
    assert "ANTHROPIC_API_KEY" in srv.draft_notes("bot-1")


def test_list_and_cancel(wired):
    assert srv.list_bots()[0]["status"] == "done"
    assert srv.cancel_bot("bot-1").startswith("deleted")


def test_errors_reach_the_model(monkeypatch, tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    def boom(request):
        raise httpx.ConnectError("proxy refused", request=request)

    settings = Settings(recall_api_key="k", notes_dir=tmp_path, state_file=tmp_path / "s.json")
    monkeypatch.setattr(srv, "_settings", settings)
    monkeypatch.setattr(srv, "_client", RecallClient(settings, transport=httpx.MockTransport(boom)))
    with pytest.raises(ToolError, match="network error.*proxy refused"):
        srv.bot_status("x")

    monkeypatch.setattr(srv, "_client", RecallClient(settings, transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"detail": "Invalid token."}))))
    with pytest.raises(ToolError, match="401.*Invalid token"):
        srv.list_bots()

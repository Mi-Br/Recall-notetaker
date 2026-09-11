"""MCP server exposing the notetaker to Claude Code (stdio).

Started by the plugin's MCP config as `notetaker-mcp`. Every tool is a thin wrapper
over recall.py / notes.py so the same behaviour is available from the CLI.

Design notes
* Tool calls must finish well inside the MCP client's timeout, so `wait_for_bot`
  polls for a bounded number of seconds and reports back; the skill calls it again.
* `draft_notes` uses the Claude API only if ANTHROPIC_API_KEY is set. Inside Claude
  Code the agent can (and usually should) write the notes itself from
  `get_transcript`, then persist them with `save_notes`.
"""

from __future__ import annotations

import functools
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .config import Settings
from .notes import MeetingContext, generate_notes, render_markdown, slugify
from .recall import BotSummary, RecallClient, RecallError, latest_status, transcript_download_url, transcript_shortcut
from .state import BotState
from .transcript import Paragraph, fmt_ts, render_text, speakers, stats, to_paragraphs, to_segments

def _surface_errors(fn):
    """Recall/config failures reach the model verbatim instead of a generic 'Error executing tool'."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPError as exc:
            raise ToolError(f"network error talking to Recall ({exc.__class__.__name__}): {exc}") from exc
        except (RecallError, ValueError, SystemExit) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


server = MCPServer(
    name="notetaker",
    instructions=(
        "Tools for sending a Recall.ai bot to a Google Meet, checking on it, fetching the "
        "transcript and saving notes. Typical flow: join_meeting -> (user admits bot) -> "
        "wait_for_bot until done -> get_transcript -> write notes -> save_notes."
    ),
)

_settings: Settings | None = None
_client: RecallClient | None = None


def _s() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def _c() -> RecallClient:
    global _client
    if _client is None:
        _client = RecallClient(_s())
    return _client


def _state() -> BotState:
    return BotState(_s().state_file)


def _started_at(bot: dict[str, Any]) -> datetime | None:
    for rec in bot.get("recordings") or []:
        if rec.get("started_at"):
            return datetime.fromisoformat(rec["started_at"].replace("Z", "+00:00"))
    for ch in bot.get("status_changes") or []:
        if ch.get("code") == "in_call_recording" and ch.get("created_at"):
            return datetime.fromisoformat(ch["created_at"].replace("Z", "+00:00"))
    return None


def _status_view(bot: dict[str, Any]) -> dict[str, Any]:
    status = latest_status(bot)
    shortcut = transcript_shortcut(bot)
    return {
        "bot_id": bot["id"],
        "meeting_url": BotSummary.from_api(bot).meeting_url,
        "status": status,
        "is_finished": status in ("done", "fatal"),
        "transcript_ready": bool(transcript_download_url(bot)),
        "transcript_status": ((shortcut or {}).get("status") or {}).get("code"),
        "history": [
            {"at": ch.get("created_at"), "code": ch.get("code"), "sub_code": ch.get("sub_code"), "message": ch.get("message")}
            for ch in bot.get("status_changes") or []
        ],
        "hint": _hint(status),
    }


def _hint(status: str | None) -> str:
    return {
        None: "Bot created; not yet reported joining.",
        "joining_call": "Bot is loading the meeting page.",
        "in_waiting_room": "Bot is knocking. The host must click Admit in Google Meet.",
        "in_call_not_recording": "Bot is in the call, recording has not started yet.",
        "in_call_recording": "Recording. Nothing to do until the call ends.",
        "call_ended": "Call over; Recall is finalising media/transcript.",
        "done": "Finished. Transcript may still be processing for a minute or two.",
        "fatal": "Bot failed. Check history[].sub_code (e.g. timeouts, not admitted).",
    }.get(status, "")


def _paragraphs_for(bot: dict[str, Any]) -> tuple[list[Paragraph], list[dict[str, Any]]]:
    raw = _c().fetch_transcript(bot)
    return to_paragraphs(to_segments(raw)), raw


def _context_for(bot: dict[str, Any], entry: dict[str, Any]) -> MeetingContext:
    s = _s()
    return MeetingContext(
        meeting_url=BotSummary.from_api(bot).meeting_url,
        started_at=_started_at(bot) or datetime.now(timezone.utc),
        owner_name=s.bot_name.replace("'s Notetaker", "").strip() or None,
        calendar_title=entry.get("calendar_title"),
        attendees=entry.get("attendees"),
    )


# ---------------------------------------------------------------- tools


@server.tool()
@_surface_errors
def join_meeting(meeting_url: str, bot_name: str | None = None, provider: str = "recallai_async",
                 calendar_title: str | None = None) -> dict[str, Any]:
    """Send the notetaker bot to a Google Meet (or Zoom/Teams) URL right now.

    provider: recallai_async (default, best post-call transcript), recallai_streaming
    (live), or meeting_captions (free; requires captions enabled in Meet).
    Returns the bot id. The host must admit the bot from the waiting room.
    """
    bot = _c().create_bot(meeting_url, bot_name=bot_name, provider=provider)
    _state().record(bot["id"], meeting_url=meeting_url, status=latest_status(bot) or "created",
                    calendar_title=calendar_title)
    return {"bot_id": bot["id"], "meeting_url": meeting_url,
            "next": "Ask the user to admit the bot when it knocks, then call wait_for_bot."}


@server.tool()
@_surface_errors
def schedule_meeting(meeting_url: str, join_at: str, bot_name: str | None = None,
                     calendar_title: str | None = None) -> dict[str, Any]:
    """Schedule the bot to join at an ISO-8601 time (>= 10 minutes ahead for guaranteed timing).

    Naive timestamps are treated as the machine's local time.
    """
    when = datetime.fromisoformat(join_at)
    if when.tzinfo is None:
        when = when.astimezone()
    bot = _c().create_bot(meeting_url, join_at=when, bot_name=bot_name)
    _state().record(bot["id"], meeting_url=meeting_url, join_at=when.isoformat(), status="scheduled",
                    calendar_title=calendar_title)
    return {"bot_id": bot["id"], "join_at": when.isoformat()}


@server.tool()
@_surface_errors
def bot_status(bot_id: str) -> dict[str, Any]:
    """Current status and status history of a bot, plus whether the transcript is ready."""
    return _status_view(_c().get_bot(bot_id))


@server.tool()
@_surface_errors
def wait_for_bot(bot_id: str, max_wait_seconds: int = 240, poll_seconds: int = 20) -> dict[str, Any]:
    """Poll the bot for up to max_wait_seconds (bounded so the tool call cannot time out).

    Returns as soon as the bot is finished AND the transcript is ready, or when the
    budget is spent. If `is_finished` is false, call again (or ask the user to come
    back after the meeting).
    """
    client = _c()
    deadline = time.monotonic() + max(0, min(max_wait_seconds, 540))
    while True:
        bot = client.get_bot(bot_id)
        view = _status_view(bot)
        if view["status"] == "fatal" or (view["status"] == "done" and (view["transcript_ready"] or not transcript_shortcut(bot))):
            _state().record(bot_id, status=view["status"])
            return view
        if time.monotonic() >= deadline:
            view["hint"] = f"Still {view['status']}. {view['hint']} Call wait_for_bot again later."
            return view
        time.sleep(poll_seconds)


@server.tool()
@_surface_errors
def get_transcript(bot_id: str, include_stats: bool = True) -> str:
    """Return the diarized transcript as '[mm:ss] Speaker: text' lines.

    Also saves the raw transcript JSON under NOTES_DIR so notes can be regenerated later
    without paying Recall again.
    """
    bot = _c().get_bot(bot_id)
    if not transcript_download_url(bot):
        return json.dumps({"error": "transcript not ready", **_status_view(bot)}, indent=2)
    paragraphs, raw = _paragraphs_for(bot)
    s = _s()
    s.notes_dir.mkdir(parents=True, exist_ok=True)
    started = _started_at(bot) or datetime.now(timezone.utc)
    raw_path = s.notes_dir / f"{started.strftime('%Y-%m-%d-%H%M')}-{bot_id[:8]}.transcript.json"
    raw_path.write_text(json.dumps(raw, indent=1))
    _state().record(bot_id, transcript_path=str(raw_path), status=latest_status(bot))

    if not paragraphs:
        return "TRANSCRIPT IS EMPTY. Likely causes: bot never admitted, nobody spoke, or captions were off (meeting_captions provider)."
    head = []
    if include_stats:
        st = stats(paragraphs)
        head.append(f"Meeting: {BotSummary.from_api(bot).meeting_url}  Started: {started.isoformat()}  Duration: {fmt_ts(st.duration_ms)}")
        head.append("Speakers: " + ", ".join(f"{sp} ({fmt_ts(st.talk_time_ms[sp])}, {st.turns[sp]} turns)" for sp in speakers(paragraphs)))
        head.append(f"Raw transcript saved to: {raw_path}")
        head.append("")
    return "\n".join(head) + render_text(paragraphs)


@server.tool()
@_surface_errors
def draft_notes(bot_id: str) -> str:
    """Generate a first-draft notes Markdown with the Claude API (needs ANTHROPIC_API_KEY).

    Prefer writing the notes yourself from get_transcript when you are already Claude;
    use this for a quick baseline or when running headless.
    """
    s = _s()
    if not s.anthropic_api_key:
        return "ANTHROPIC_API_KEY is not configured. Call get_transcript and write the notes yourself, then save_notes."
    bot = _c().get_bot(bot_id)
    if not transcript_download_url(bot):
        return json.dumps({"error": "transcript not ready", **_status_view(bot)}, indent=2)
    paragraphs, _ = _paragraphs_for(bot)
    if not paragraphs:
        return "Transcript is empty; nothing to draft."
    ctx = _context_for(bot, _state().bots.get(bot_id, {}))
    notes = generate_notes(paragraphs, ctx, model=s.notes_model)
    md = render_markdown(notes, ctx, paragraphs)
    path = _write_notes_file(bot_id, notes.title, md, started=ctx.started_at, draft=True)
    return f"Draft saved to {path}\n\n{md}"


@server.tool()
@_surface_errors
def save_notes(bot_id: str, title: str, markdown: str, output_dir: str | None = None) -> dict[str, Any]:
    """Persist final (refined) notes as <date>-<slug>.md and record the path in state.

    Files go to NOTES_DIR unless output_dir is given (e.g. a folder in the user's project).
    """
    bot_entry = _state().bots.get(bot_id, {})
    started = None
    try:
        started = _started_at(_c().get_bot(bot_id))
    except RecallError:
        pass
    path = _write_notes_file(bot_id, title, markdown, started=started or datetime.now(timezone.utc),
                             directory=Path(output_dir) if output_dir else None)
    return {"notes_path": str(path), "bot_id": bot_id, "previous_notes": bot_entry.get("notes_path")}


def _write_notes_file(bot_id: str, title: str, markdown: str, *, started: datetime | None,
                      draft: bool = False, directory: Path | None = None) -> Path:
    directory = directory or _s().notes_dir
    directory.mkdir(parents=True, exist_ok=True)
    started = started or datetime.now(timezone.utc)
    suffix = ".draft.md" if draft else ".md"
    path = directory / f"{started.strftime('%Y-%m-%d-%H%M')}-{slugify(title)}{suffix}"
    path.write_text(markdown if markdown.endswith("\n") else markdown + "\n")
    _state().record(bot_id, **({"draft_path": str(path)} if draft else {"notes_path": str(path), "title": title}))
    return path


@server.tool()
@_surface_errors
def list_bots(limit: int = 10) -> list[dict[str, Any]]:
    """Recent bots in the Recall workspace with status, time and meeting URL."""
    out = []
    for bot in _c().list_bots(limit=limit):
        s = BotSummary.from_api(bot)
        out.append({"bot_id": s.id, "status": s.status, "join_at": s.join_at, "created_at": s.created_at,
                    "meeting_url": s.meeting_url})
    return out


@server.tool()
@_surface_errors
def cancel_bot(bot_id: str) -> str:
    """Delete a scheduled bot so it never joins."""
    _c().delete_bot(bot_id)
    _state().record(bot_id, status="cancelled")
    return f"deleted {bot_id}"


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()

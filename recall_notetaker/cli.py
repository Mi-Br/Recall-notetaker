"""`notetaker` command line.

  notetaker detect-region                 find which region the API key belongs to
  notetaker join <meet-url> [--wait]      send the bot now (optionally wait + write notes)
  notetaker schedule <meet-url> <iso-time> schedule a bot (join_at); >=10 min ahead
  notetaker status <bot-id>               print status history
  notetaker wait <bot-id>                 poll until done, then write notes
  notetaker notes <bot-id>                (re)generate notes for a finished bot
  notetaker list                          recent bots
  notetaker cancel <bot-id>               delete a scheduled bot
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import REGIONS, Settings
from .notes import MeetingContext, generate_notes, render_markdown, slugify
from .recall import BotSummary, RecallClient, RecallError, detect_region, latest_status
from .state import BotState
from .transcript import to_paragraphs, to_segments


def _print(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


def _started_at(bot: dict) -> datetime | None:
    for rec in bot.get("recordings") or []:
        if rec.get("started_at"):
            return datetime.fromisoformat(rec["started_at"].replace("Z", "+00:00"))
    for ch in bot.get("status_changes") or []:
        if ch.get("code") == "in_call_recording" and ch.get("created_at"):
            return datetime.fromisoformat(ch["created_at"].replace("Z", "+00:00"))
    return None


def write_notes(settings: Settings, client: RecallClient, state: BotState, bot: dict) -> Path:
    bot_id = bot["id"]
    transcript = client.fetch_transcript(bot)
    paragraphs = to_paragraphs(to_segments(transcript))
    started = _started_at(bot) or datetime.now(timezone.utc)
    entry = state.bots.get(bot_id, {})
    ctx = MeetingContext(
        meeting_url=BotSummary.from_api(bot).meeting_url,
        started_at=started,
        owner_name=settings.bot_name.replace("'s Notetaker", "").strip() or None,
        calendar_title=entry.get("calendar_title"),
        attendees=entry.get("attendees"),
    )

    settings.notes_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y-%m-%d-%H%M")
    raw_path = settings.notes_dir / f"{stamp}-{bot_id[:8]}.transcript.json"
    raw_path.write_text(json.dumps(transcript, indent=1))

    if not paragraphs:
        _print("transcript is empty — nothing to summarise (was the bot admitted? were captions on?)")
        state.record(bot_id, status=latest_status(bot), transcript_path=str(raw_path), notes_path=None)
        return raw_path

    _print(f"summarising {len(paragraphs)} paragraphs with {settings.notes_model} …")
    notes = generate_notes(paragraphs, ctx, model=settings.notes_model)
    md_path = settings.notes_dir / f"{stamp}-{slugify(notes.title)}.md"
    md_path.write_text(render_markdown(notes, ctx, paragraphs))
    state.record(bot_id, status=latest_status(bot), transcript_path=str(raw_path), notes_path=str(md_path))
    _print(f"notes written to {md_path}")
    return md_path


def cmd_detect_region(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    region = detect_region(settings.recall_api_key, REGIONS)
    if region:
        print(region)
        return 0
    _print("no region accepted this key (check the key, or network access to *.recall.ai)")
    return 1


def cmd_join(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    client = RecallClient(settings)
    state = BotState(settings.state_file)
    bot = client.create_bot(args.meeting_url, bot_name=args.name, provider=args.provider)
    state.record(bot["id"], meeting_url=args.meeting_url, status=latest_status(bot) or "created")
    print(bot["id"])
    _print(f"bot {bot['id']} is on its way to {args.meeting_url} — admit it from the waiting room.")
    if args.wait:
        return _wait_and_notes(settings, client, state, bot["id"])
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    client = RecallClient(settings)
    state = BotState(settings.state_file)
    join_at = datetime.fromisoformat(args.join_at)
    if join_at.tzinfo is None:
        join_at = join_at.astimezone()  # interpret naive as local time
    bot = client.create_bot(args.meeting_url, join_at=join_at, bot_name=args.name, provider=args.provider)
    state.record(bot["id"], meeting_url=args.meeting_url, join_at=join_at.isoformat(), status="scheduled")
    print(bot["id"])
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    bot = RecallClient(settings).get_bot(args.bot_id)
    for ch in bot.get("status_changes") or []:
        sub = f" ({ch['sub_code']})" if ch.get("sub_code") else ""
        print(f"{ch.get('created_at')}  {ch.get('code')}{sub}  {ch.get('message') or ''}")
    if args.json:
        print(json.dumps(bot, indent=2))
    return 0


def _wait_and_notes(settings: Settings, client: RecallClient, state: BotState, bot_id: str) -> int:
    bot = client.wait_until_done(bot_id, on_status=lambda s: _print(f"[{bot_id[:8]}] {s}"))
    if latest_status(bot) == "fatal":
        state.record(bot_id, status="fatal")
        _print("bot ended with a fatal status; see `notetaker status` for details")
        return 2
    write_notes(settings, client, state, bot)
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    return _wait_and_notes(settings, RecallClient(settings), BotState(settings.state_file), args.bot_id)


def cmd_notes(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    client = RecallClient(settings)
    bot = client.get_bot(args.bot_id)
    write_notes(settings, client, BotState(settings.state_file), bot)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    for bot in RecallClient(settings).list_bots(limit=args.limit):
        s = BotSummary.from_api(bot)
        print(f"{s.id}  {s.status or '-':<22} {s.join_at or s.created_at or ''}  {s.meeting_url or ''}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    RecallClient(settings).delete_bot(args.bot_id)
    _print(f"deleted bot {args.bot_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="notetaker", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("detect-region").set_defaults(fn=cmd_detect_region)

    def bot_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--name", help="bot display name (default BOT_NAME)")
        sp.add_argument("--provider", default="recallai_async",
                        choices=["recallai_async", "recallai_streaming", "meeting_captions"])

    j = sub.add_parser("join", help="send the bot to a meeting now")
    j.add_argument("meeting_url")
    j.add_argument("--wait", action="store_true", help="poll until done and write notes")
    bot_opts(j)
    j.set_defaults(fn=cmd_join)

    s = sub.add_parser("schedule", help="schedule a bot with join_at")
    s.add_argument("meeting_url")
    s.add_argument("join_at", help="ISO-8601, e.g. 2026-09-12T14:00 (local) or ...T12:00:00+00:00")
    bot_opts(s)
    s.set_defaults(fn=cmd_schedule)

    st = sub.add_parser("status")
    st.add_argument("bot_id")
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_status)

    w = sub.add_parser("wait")
    w.add_argument("bot_id")
    w.set_defaults(fn=cmd_wait)

    n = sub.add_parser("notes")
    n.add_argument("bot_id")
    n.set_defaults(fn=cmd_notes)

    ls = sub.add_parser("list")
    ls.add_argument("--limit", type=int, default=20)
    ls.set_defaults(fn=cmd_list)

    c = sub.add_parser("cancel")
    c.add_argument("bot_id")
    c.set_defaults(fn=cmd_cancel)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except RecallError as e:
        _print(f"Recall API error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

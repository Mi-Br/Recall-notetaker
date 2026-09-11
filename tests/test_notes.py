from datetime import datetime, timezone

from recall_notetaker.notes import (
    ActionItem,
    Decision,
    MeetingContext,
    MeetingNotes,
    build_user_prompt,
    render_markdown,
    slugify,
)
from recall_notetaker.transcript import to_paragraphs, to_segments


def _ctx():
    return MeetingContext(meeting_url="https://meet.google.com/abc-defg-hij",
                          started_at=datetime(2026, 9, 11, 9, 1, tzinfo=timezone.utc), owner_name="Michail")


def test_prompt_contains_transcript_and_context(transcript_json):
    paras = to_paragraphs(to_segments(transcript_json))
    prompt = build_user_prompt(paras, _ctx())
    assert "Owner of these notes: Michail" in prompt
    assert "Speakers detected: Ada, Bob" in prompt
    assert "[01:05] Bob: I'll send the budget Friday." in prompt


def test_render_markdown(transcript_json):
    paras = to_paragraphs(to_segments(transcript_json))
    notes = MeetingNotes(
        title="Budget sync",
        summary="Short sync about the budget.",
        key_points=["Budget due Friday"],
        decisions=[Decision(decision="Ship budget Friday", rationale="deadline", evidence_ts="01:05")],
        action_items=[ActionItem(task="Send the budget", owner="Bob", due="Friday", evidence_ts="01:05")],
        open_questions=["Which currency?"],
        follow_up_for_me=["Review Bob's budget"],
    )
    md = render_markdown(notes, _ctx(), paras)
    assert md.startswith("# Budget sync")
    assert "- [ ] **Bob** — Send the budget (due Friday) `[01:05]`" in md
    assert "## My follow-ups" in md and "Review Bob's budget" in md
    assert "## Talk time" in md
    assert "[01:05] Bob: I'll send the budget Friday." in md


def test_slugify():
    assert slugify("Budget sync: Q3 / planning!") == "budget-sync-q3-planning"
    assert slugify("!!!") == "meeting"

"""Generate structured meeting notes from a transcript with the Claude API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from .transcript import Paragraph, fmt_ts, render_text, speakers, stats


class ActionItem(BaseModel):
    task: str
    owner: Optional[str] = Field(None, description="Person responsible, as named in the transcript, or null")
    due: Optional[str] = Field(None, description="Due date/time if stated, else null")
    evidence_ts: Optional[str] = Field(None, description="Transcript timestamp like 12:34 where this was agreed")


class Decision(BaseModel):
    decision: str
    rationale: Optional[str] = None
    evidence_ts: Optional[str] = None


class MeetingNotes(BaseModel):
    title: str = Field(description="Short descriptive title for the meeting")
    summary: str = Field(description="2-5 sentence executive summary")
    key_points: list[str] = Field(description="Main discussion points, most important first")
    decisions: list[Decision]
    action_items: list[ActionItem]
    open_questions: list[str]
    follow_up_for_me: list[str] = Field(
        description="Things the note-taker's owner personally promised or needs to do; empty if none"
    )


@dataclass
class MeetingContext:
    meeting_url: str | None
    started_at: datetime | None
    owner_name: str | None = None  # the person the notetaker works for
    calendar_title: str | None = None
    attendees: list[str] | None = None


SYSTEM_PROMPT = """You are a meticulous meeting note-taker working for one person (the owner).
You receive a diarized transcript of a Google Meet call with [mm:ss] timestamps.
Write notes that the owner can act on the next morning without re-listening.

Rules:
- Use only what is in the transcript. Never invent names, dates, or commitments.
- Attribute action items to the speaker who took them, using their transcript name.
- Prefer concrete wording ("send the Q3 budget to Ada by Friday") over vague ("follow up").
- If the transcript is a fragment, empty, or clearly not a meeting, say so in the summary and leave lists empty.
- Timestamps you cite must appear in the transcript."""


def build_user_prompt(paragraphs: list[Paragraph], ctx: MeetingContext) -> str:
    st = stats(paragraphs)
    header = [
        f"Owner of these notes: {ctx.owner_name or 'unknown'}",
        f"Meeting URL: {ctx.meeting_url or 'unknown'}",
        f"Started: {ctx.started_at.isoformat() if ctx.started_at else 'unknown'}",
        f"Calendar title: {ctx.calendar_title or 'unknown'}",
        f"Attendees (from calendar): {', '.join(ctx.attendees) if ctx.attendees else 'unknown'}",
        f"Speakers detected: {', '.join(speakers(paragraphs)) or 'none'}",
        f"Duration: {fmt_ts(st.duration_ms)}",
    ]
    return "\n".join(header) + "\n\nTRANSCRIPT:\n" + render_text(paragraphs)


def generate_notes(
    paragraphs: list[Paragraph],
    ctx: MeetingContext,
    *,
    model: str = "claude-opus-5",
    client: anthropic.Anthropic | None = None,
) -> MeetingNotes:
    client = client or anthropic.Anthropic()
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(paragraphs, ctx)}],
        output_format=MeetingNotes,
    )
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise RuntimeError(f"Claude declined to summarise this transcript: {details}")
    if response.parsed_output is None:
        raise RuntimeError(f"No structured output returned (stop_reason={response.stop_reason})")
    return response.parsed_output


def render_markdown(notes: MeetingNotes, ctx: MeetingContext, paragraphs: list[Paragraph]) -> str:
    st = stats(paragraphs)
    lines: list[str] = []
    when = ctx.started_at.strftime("%Y-%m-%d %H:%M") if ctx.started_at else "unknown date"
    lines.append(f"# {notes.title}")
    lines.append("")
    lines.append(f"- **When:** {when}")
    if ctx.meeting_url:
        lines.append(f"- **Meeting:** {ctx.meeting_url}")
    lines.append(f"- **Duration:** {fmt_ts(st.duration_ms)}")
    if speakers(paragraphs):
        lines.append(f"- **Speakers:** {', '.join(speakers(paragraphs))}")
    lines.append("")
    lines.append("## Summary")
    lines.append(notes.summary)
    lines.append("")
    if notes.key_points:
        lines.append("## Key points")
        lines.extend(f"- {p}" for p in notes.key_points)
        lines.append("")
    if notes.decisions:
        lines.append("## Decisions")
        for d in notes.decisions:
            extra = f" — {d.rationale}" if d.rationale else ""
            ts = f" `[{d.evidence_ts}]`" if d.evidence_ts else ""
            lines.append(f"- {d.decision}{extra}{ts}")
        lines.append("")
    if notes.action_items:
        lines.append("## Action items")
        for a in notes.action_items:
            owner = f"**{a.owner}** — " if a.owner else ""
            due = f" (due {a.due})" if a.due else ""
            ts = f" `[{a.evidence_ts}]`" if a.evidence_ts else ""
            lines.append(f"- [ ] {owner}{a.task}{due}{ts}")
        lines.append("")
    if notes.follow_up_for_me:
        lines.append("## My follow-ups")
        lines.extend(f"- [ ] {f}" for f in notes.follow_up_for_me)
        lines.append("")
    if notes.open_questions:
        lines.append("## Open questions")
        lines.extend(f"- {q}" for q in notes.open_questions)
        lines.append("")
    if st.talk_time_ms:
        lines.append("## Talk time")
        for spk, ms in sorted(st.talk_time_ms.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {spk}: {fmt_ts(ms)} ({st.turns.get(spk, 0)} turns)")
        lines.append("")
    lines.append("<details><summary>Transcript</summary>")
    lines.append("")
    lines.append("```")
    lines.append(render_text(paragraphs))
    lines.append("```")
    lines.append("</details>")
    lines.append("")
    return "\n".join(lines)


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "meeting"

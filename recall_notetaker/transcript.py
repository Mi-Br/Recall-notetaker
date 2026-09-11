"""Turn Recall's transcript JSON into speaker paragraphs and plain text.

Recall transcript download shape (one entry per utterance):
[
  {"participant": {"id": 100, "name": "Ada", "email": null, "is_host": true, ...},
   "words": [{"text": "Hello", "start_timestamp": {"relative": 1.2, "absolute": "..."},
              "end_timestamp": {"relative": 1.5, "absolute": "..."}}, ...]},
  ...
]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class Segment:
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    email: str | None = None


@dataclass
class Paragraph:
    speaker: str
    start_ms: int
    end_ms: int
    text: str
    email: str | None = None


@dataclass
class TranscriptStats:
    duration_ms: int = 0
    talk_time_ms: dict[str, int] = field(default_factory=dict)
    turns: dict[str, int] = field(default_factory=dict)


def to_segments(transcript_json: Iterable[dict[str, Any]]) -> list[Segment]:
    segs: list[Segment] = []
    for entry in transcript_json:
        participant = entry.get("participant") or {}
        words = entry.get("words") or []
        if not words:
            continue
        start = int(float(words[0]["start_timestamp"]["relative"]) * 1000)
        end = int(float(words[-1]["end_timestamp"]["relative"]) * 1000)
        text = " ".join((w.get("text") or "").strip() for w in words).strip()
        if not text:
            continue
        segs.append(Segment(
            speaker=participant.get("name") or "Unknown speaker",
            start_ms=start,
            end_ms=max(end, start),
            text=text,
            email=participant.get("email"),
        ))
    segs.sort(key=lambda s: s.start_ms)
    return segs


def to_paragraphs(segments: list[Segment], gap_ms: int = 1500) -> list[Paragraph]:
    """Merge consecutive segments from the same speaker when the pause is short."""
    out: list[Paragraph] = []
    cur: Paragraph | None = None
    for s in segments:
        if cur is None or s.speaker != cur.speaker or s.start_ms - cur.end_ms > gap_ms:
            if cur:
                out.append(cur)
            cur = Paragraph(s.speaker, s.start_ms, s.end_ms, s.text, s.email)
        else:
            cur.end_ms = max(cur.end_ms, s.end_ms)
            cur.text = f"{cur.text} {s.text}".strip()
    if cur:
        out.append(cur)
    return out


def fmt_ts(ms: int) -> str:
    s = max(0, ms // 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def render_text(paragraphs: list[Paragraph]) -> str:
    return "\n".join(f"[{fmt_ts(p.start_ms)}] {p.speaker}: {p.text}" for p in paragraphs)


def stats(paragraphs: list[Paragraph]) -> TranscriptStats:
    st = TranscriptStats()
    for p in paragraphs:
        st.duration_ms = max(st.duration_ms, p.end_ms)
        st.talk_time_ms[p.speaker] = st.talk_time_ms.get(p.speaker, 0) + (p.end_ms - p.start_ms)
        st.turns[p.speaker] = st.turns.get(p.speaker, 0) + 1
    return st


def speakers(paragraphs: list[Paragraph]) -> list[str]:
    seen: dict[str, None] = {}
    for p in paragraphs:
        seen.setdefault(p.speaker, None)
    return list(seen)

"""Environment-driven settings. Loads a local `.env` if present."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REGIONS = ("us-east-1", "us-west-2", "eu-central-1", "ap-northeast-1")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass
class Settings:
    recall_api_key: str
    recall_region: str = "us-west-2"
    anthropic_api_key: str | None = None
    notes_model: str = "claude-opus-5"
    bot_name: str = "Notetaker"
    transcript_language: str = "en"
    waiting_room_timeout: int = 600
    noone_joined_timeout: int = 600
    everyone_left_timeout: int = 30
    notes_dir: Path = field(default_factory=lambda: Path("notes"))
    state_file: Path = field(default_factory=lambda: Path("state/bots.json"))

    @property
    def recall_base_url(self) -> str:
        if self.recall_region not in REGIONS:
            raise ValueError(f"RECALL_REGION must be one of {REGIONS}, got {self.recall_region!r}")
        return f"https://{self.recall_region}.recall.ai"

    @classmethod
    def from_env(cls, require_recall_key: bool = True) -> "Settings":
        load_dotenv()
        key = os.getenv("RECALL_API_KEY", "")
        if require_recall_key and not key:
            raise SystemExit("RECALL_API_KEY is not set (copy .env.example to .env).")
        return cls(
            recall_api_key=key,
            recall_region=os.getenv("RECALL_REGION", "us-west-2"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            notes_model=os.getenv("NOTES_MODEL", "claude-opus-5"),
            bot_name=os.getenv("BOT_NAME", "Notetaker"),
            transcript_language=os.getenv("TRANSCRIPT_LANGUAGE", "en"),
            waiting_room_timeout=_int("WAITING_ROOM_TIMEOUT", 600),
            noone_joined_timeout=_int("NOONE_JOINED_TIMEOUT", 600),
            everyone_left_timeout=_int("EVERYONE_LEFT_TIMEOUT", 30),
            notes_dir=Path(os.getenv("NOTES_DIR", "notes")),
            state_file=Path(os.getenv("STATE_FILE", "state/bots.json")),
        )

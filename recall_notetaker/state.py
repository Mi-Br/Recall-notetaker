"""Tiny JSON store mapping bot ids to what we know about them.

Used by the CLI to remember bots we created, and (phase 2) by the calendar
scheduler to avoid scheduling two bots for one event.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BotState:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] = {"bots": {}}
        if path.exists():
            self._data = json.loads(path.read_text() or '{"bots": {}}')
            self._data.setdefault("bots", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")

    @property
    def bots(self) -> dict[str, dict[str, Any]]:
        return self._data["bots"]

    def record(self, bot_id: str, **fields: Any) -> None:
        entry = self.bots.setdefault(bot_id, {"created_at": datetime.now(timezone.utc).isoformat()})
        entry.update(fields)
        self.save()

    def by_event(self, event_id: str) -> str | None:
        for bot_id, entry in self.bots.items():
            if entry.get("event_id") == event_id:
                return bot_id
        return None

    def pending(self) -> list[str]:
        """Bots we created that don't yet have notes written."""
        return [bid for bid, e in self.bots.items() if not e.get("notes_path") and e.get("status") not in ("fatal",)]

"""Persistent daily selection history."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


class History:
    def __init__(self, path: Path) -> None:
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text())
        else:
            self.data = {"version": 1, "entries": []}
        self.data.setdefault("entries", [])

    @staticmethod
    def _coverage_topic(entry: dict) -> str | None:
        return entry.get("coverage_topic") or entry.get("topic")

    def get(self, date_str: str) -> dict | None:
        for entry in self.data["entries"]:
            if entry.get("date") == date_str:
                return entry
        return None

    def recent_slugs(self, as_of: date, days: int) -> set[str]:
        if days <= 0:
            return set()
        result = set()
        for entry in self.data["entries"]:
            try:
                entry_date = date.fromisoformat(entry["date"])
            except (KeyError, ValueError):
                continue
            if 0 <= (as_of - entry_date).days <= days:
                if entry.get("slug"):
                    result.add(entry["slug"])
        return result

    def topic_counts(self, as_of: date, days: int = 30) -> dict[str, int]:
        """Count each topic's appearances in the trailing window.

        Older entries outside the window are intentionally forgotten: the
        scheduler should repair a missed topic even if it was popular months
        ago.
        """
        counts: dict[str, int] = {}
        for entry in self.data["entries"]:
            topic = self._coverage_topic(entry)
            if not topic:
                continue
            try:
                entry_date = date.fromisoformat(entry["date"])
            except (KeyError, ValueError):
                continue
            age = (as_of - entry_date).days
            if 0 <= age < max(1, days):
                counts[topic] = counts.get(topic, 0) + 1
        return counts

    def difficulty_counts(self, as_of: date, days: int = 7) -> dict[str, int]:
        """Difficulty mix over the previous `days` days (today's entry excluded)."""
        counts: dict[str, int] = {}
        for entry in self.data["entries"]:
            difficulty = entry.get("difficulty")
            if not difficulty:
                continue
            try:
                entry_date = date.fromisoformat(entry["date"])
            except (KeyError, ValueError):
                continue
            age = (as_of - entry_date).days
            if 0 < age <= days:
                counts[difficulty] = counts.get(difficulty, 0) + 1
        return counts

    def last_used_by_topic(self, as_of: date) -> dict[str, int]:
        """Return topic -> days since its most recent use (future entries ignored)."""
        result: dict[str, int] = {}
        for entry in self.data["entries"]:
            topic = self._coverage_topic(entry)
            if not topic:
                continue
            try:
                entry_date = date.fromisoformat(entry["date"])
            except (KeyError, ValueError):
                continue
            delta = (as_of - entry_date).days
            if delta < 0:
                continue
            if topic not in result or delta < result[topic]:
                result[topic] = delta
        return result

    def upsert(self, entry: dict[str, Any]) -> None:
        entries = self.data["entries"]
        for i, existing in enumerate(entries):
            if existing.get("date") == entry.get("date"):
                entries[i] = {**existing, **entry}
                break
        else:
            entries.append(entry)
        entries.sort(key=lambda e: e.get("date", ""))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(self.path)

    def all(self) -> list[dict]:
        return list(self.data["entries"])

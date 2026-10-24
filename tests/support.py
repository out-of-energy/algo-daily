"""Shared fixtures for dataset tests.

Run from the project root:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import functools
import json
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NORM = ROOT / "data" / "normalized"
DB = ROOT / "data" / "algorithms.db"
TOPICS_CFG = ROOT / "config" / "topics.json"

# The dataset is not committed; build it with `make data`.
DATA_READY = DB.exists() and (NORM / "manifest.json").exists() and (NORM / "index.json").exists()
DATA_MISSING_REASON = "题库数据未构建：先运行 `make data`（或 python3 scripts/bootstrap_data.py）"
requires_data = unittest.skipUnless(DATA_READY, DATA_MISSING_REASON)

# Snapshot expected for the 2026-10-24 dataset. When the local data *is* this
# snapshot, counts must match exactly so accidental loss cannot pass silently.
# A freshly fetched dataset (LeetCode keeps adding problems) is checked for
# internal consistency against its own manifest instead; see expected().
EXPECTED = {
    "total": 4475,
    "free_with_statement": 3688,
    "paid_metadata_only": 787,
    "in_scope": 3992,
    "in_scope_free_with_statement": 3465,
    "pending_classification": 89,
    "out_of_scope": 394,
    "statement_en": 3409,
    "statement_zh": 279,
    "topics": 15,
}

ALLOWED_SCOPES = {
    "algorithm", "database", "shell", "concurrency", "javascript", "pandas", "pending",
}
ALLOWED_DIFFICULTIES = {"Easy", "Medium", "Hard"}
ALLOWED_LANGS = {"en", "zh"}


@functools.lru_cache(maxsize=1)
def load_index() -> list[dict]:
    return json.loads((NORM / "index.json").read_text())


@functools.lru_cache(maxsize=1)
def load_index_by_slug() -> dict[str, dict]:
    return {row["slug"]: row for row in load_index()}


@functools.lru_cache(maxsize=1)
def load_manifest() -> dict:
    return json.loads((NORM / "manifest.json").read_text())


def _manifest_counts() -> dict:
    counts = load_manifest().get("counts", {})
    langs = counts.get("statement_languages", {})
    return {
        **{k: counts.get(k) for k in EXPECTED if k not in {"statement_en", "statement_zh"}},
        "statement_en": langs.get("en"),
        "statement_zh": langs.get("zh"),
        "topics": EXPECTED["topics"],
    }


@functools.lru_cache(maxsize=1)
def is_reference_snapshot() -> bool:
    counts = load_manifest().get("counts", {})
    return all(counts.get(k) == EXPECTED[k] for k in ("total", "free_with_statement", "paid_metadata_only"))


@functools.lru_cache(maxsize=1)
def expected() -> dict:
    """Exact snapshot numbers for the reference data, the manifest's own counts otherwise."""
    return dict(EXPECTED) if is_reference_snapshot() else _manifest_counts()


@functools.lru_cache(maxsize=1)
def load_topics() -> dict:
    return json.loads((NORM / "topics.json").read_text())


@functools.lru_cache(maxsize=1)
def load_topic_cfg() -> dict:
    return json.loads(TOPICS_CFG.read_text())


@functools.lru_cache(maxsize=1)
def load_pending() -> dict:
    return json.loads((NORM / "pending-classification.json").read_text())


@functools.lru_cache(maxsize=1)
def load_out_of_scope() -> dict:
    return json.loads((NORM / "out-of-scope.json").read_text())


def load_full(slug: str) -> dict:
    return json.loads((NORM / "problems" / f"{slug}.json").read_text())


def full_paths() -> list[Path]:
    return sorted((NORM / "problems").glob("*.json"))


def iter_full():
    for path in full_paths():
        yield path, json.loads(path.read_text())


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def topic_ids() -> list[str]:
    return [c["id"] for c in load_topic_cfg()["categories"]]

#!/usr/bin/env python3
"""Normalize the raw question bank into a stable, queryable dataset.

Outputs
-------
data/normalized/
  index.json              all problems, compact metadata
  index.jsonl             one compact record per line
  problems/<slug>.json    full canonical records for free problems
  by-topic/<topic>.json   per-topic extracts
  topics.json             taxonomy + counts
  out-of-scope.json       database/shell/concurrency/unclassified problems
  manifest.json           dataset manifest, counts, licenses
data/algorithms.db        SQLite mirror with joins + full-text search

No network access is performed. Run scripts/fetch_leetcode.py first.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "leetcode"
RAW_INDEX = RAW / "index.json"
RAW_PROBLEMS = RAW / "problems"
OUT = ROOT / "data" / "normalized"
OUT_PROBLEMS = OUT / "problems"
OUT_TOPICS = OUT / "by-topic"
DB_PATH = ROOT / "data" / "algorithms.db"
SCHEMA_VERSION = "1.0.0"
SOURCE_ID = "leetcode-cn"
LICENSE_NOTE = (
    "Problem statements are LeetCode's copyrighted content, stored locally for "
    "personal study only. Do not redistribute. Code snippets are LeetCode starter "
    "templates. Official pages remain the canonical source."
)
DIFFICULTY_LEVEL = {"Easy": 1, "Medium": 2, "Hard": 3}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class HTMLTextExtractor(HTMLParser):
    """Turn LeetCode HTML into readable plain text without losing code layout."""

    BLOCK = {"p", "div", "section", "article", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
    NEWLINE = {"br", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.pre_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "pre":
            self.pre_depth += 1
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.NEWLINE:
            self.parts.append("\n")
        elif tag in self.BLOCK and not self.pre_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
            self.parts.append("\n")
        elif tag in self.BLOCK and not self.pre_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self.pre_depth:
            self.parts.append(data.replace("\r\n", "\n"))
        else:
            self.parts.append(re.sub(r"[ \t\r\n\f\v]+", " ", data))


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def classify(problem: dict, topics_cfg: dict, raw: dict | None = None) -> dict:
    cats = topics_cfg["categories"]
    rules = topics_cfg["classification"]
    tag_slugs = [t["slug"] for t in problem.get("tags", [])]
    tag_set = set(tag_slugs)
    matches: list[dict] = []

    for cat in cats:
        # A category can exclude noisy overlaps. Example: tree BFS/DFS problems
        # carry breadth/depth-first tags but belong to tree-bst, not graph.
        excluded = tag_set.intersection(cat.get("exclude_tags", []))
        if excluded and not tag_set.intersection(cat.get("exclude_unless_tags", [])):
            continue
        matched = [tag for tag in tag_slugs if tag in cat["tag_weights"]]
        tag_score = sum(cat["tag_weights"][tag] for tag in matched)
        curated = problem["id"] in cat.get("must", [])
        score = tag_score + (rules["must_bonus"] if curated else 0.0)
        if score <= 0:
            continue
        reason_parts = []
        if curated:
            reason_parts.append("curated")
        if matched:
            reason_parts.append("tags:" + ",".join(sorted(set(matched))))
        matches.append({
            "id": cat["id"],
            "label_zh": cat["label"],
            "short_zh": cat["short"],
            "score": round(score, 2),
            "is_primary": False,
            "curated": curated,
            "matched_tags": sorted(set(matched)),
            "reason": "+".join(reason_parts),
            "sort_order": cat["order"],
        })

    matches.sort(key=lambda m: (-m["score"], m["sort_order"]))
    if matches:
        matches[0]["is_primary"] = True
        primary = matches[0]["id"]
        top = matches[0]["score"]
        second = matches[1]["score"] if len(matches) > 1 else 0.0
        if top >= 4 and top - second >= 1:
            confidence = "high"
        elif top >= 2:
            confidence = "medium"
        else:
            confidence = "low"
        scope = "algorithm"
        in_scope = True
    else:
        primary = None
        confidence = "none"
        in_scope = False
        infra = sorted(tag_set.intersection(rules["out_of_scope_tags"]))
        scope = infra[0] if len(infra) == 1 else ("infrastructure" if infra else "")
        if scope:
            pass
        else:
            scope = detect_scope_without_tags(problem, raw)

    for match in matches:
        match.pop("sort_order", None)

    return {
        "topics": matches,
        "topic_ids": [m["id"] for m in matches],
        "primary_topic": primary,
        "in_scope": in_scope,
        "scope": scope,
        "confidence": confidence,
        "method": rules["method"],
        "curated_topic_ids": [m["id"] for m in matches if m["curated"]],
        "needs_review": scope == "pending",
    }


def detect_scope_without_tags(problem: dict, raw: dict | None) -> str:
    """Best-effort scope for questions that LeetCode has not tagged.

    This is deliberately conservative: only obvious JavaScript, Pandas and SQL
    items are moved out of the algorithm corpus. Everything else is marked
    `pending` for later review instead of guessing a topic.
    """
    slug = (problem.get("slug") or "").lower()
    langs = {
        s.get("lang_slug")
        for s in ((raw or {}).get("code_snippets") or [])
        if s.get("lang_slug")
    }
    content = ((raw or {}).get("content_html") or "").lower()
    sample = content[:2000]
    if langs and langs <= {"javascript", "typescript"}:
        return "javascript"
    if "dataframe" in content or slug.startswith("create-a-dataframe"):
        return "pandas"
    if "<p>table:" in sample or ("table:" in sample and not langs):
        return "database"
    if any(token in slug for token in (
        "promise", "memoize", "curry", "debounce", "throttle", "json-deep",
        "function", "counter", "event-emitter", "dataframe",
    )):
        return "javascript"
    return "pending"


def normalize_tags(tags: list[dict]) -> list[dict]:
    out = []
    for tag in tags:
        out.append({
            "slug": tag.get("slug", ""),
            "name": tag.get("name", ""),
            "name_zh": tag.get("name_zh") or tag.get("name", ""),
        })
    return [t for t in out if t["slug"]]


def compact_record(full: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "uid": full["uid"],
        "source": full["source"],
        "source_id": full["source_id"],
        "frontend_id": full["frontend_id"],
        "slug": full["slug"],
        "title": full["title"],
        "title_zh": full["title_zh"],
        "difficulty": full["difficulty"],
        "difficulty_level": full["difficulty_level"],
        "paid_only": full["paid_only"],
        "has_statement": full["has_statement"],
        "statement_lang": (full.get("statement") or {}).get("lang"),
        "in_scope": full["in_scope"],
        "scope": full["scope"],
        "primary_topic": full["primary_topic"],
        "topic_ids": full["topic_ids"],
        "topic_scores": {t["id"]: t["score"] for t in full["topics"]},
        "curated_topic_ids": full["classification"]["curated_topic_ids"],
        "classification_confidence": full["classification"]["confidence"],
        "needs_review": full["classification"].get("needs_review", False),
        "tags": full["tag_slugs"],
        "ac_rate": full["ac_rate"],
        "likes": full["likes"],
        "dislikes": full["dislikes"],
        "official_url": full["official_url"],
    }


def build_full(meta: dict, raw: dict | None, cls: dict, tags: list[dict]) -> dict:
    """Create a canonical full record. Paid problems have no raw statement and
    therefore are kept in the index only."""
    code = {}
    statement = None
    if raw:
        for snippet in raw.get("code_snippets") or []:
            lang_slug = snippet.get("lang_slug")
            if not lang_slug:
                continue
            code[lang_slug] = {
                "lang": snippet.get("lang") or lang_slug,
                "code": snippet.get("code") or "",
            }
        english_html = raw.get("content_html") or ""
        chinese_html = raw.get("translated_content_html") or ""
        english_text = html_to_text(english_html)
        chinese_text = html_to_text(chinese_html)
        english_is_placeholder = (
            "english description is not available" in english_html.lower()
            or len(english_text) < 80
        )
        use_chinese = bool(chinese_text) and english_is_placeholder
        if use_chinese:
            statement_html, statement_text, lang = chinese_html, chinese_text, "zh"
        else:
            statement_html, statement_text, lang = english_html, english_text, "en"
        statement = {
            "lang": lang,
            "format": "html",
            "html": statement_html,
            "text": statement_text,
            "translated": bool(chinese_text),
            "examples": raw.get("example_testcases") or "",
            "sample_testcase": raw.get("sample_testcase") or "",
            "hints": raw.get("hints") or [],
        }
        # Preserve the Chinese text when it exists and is not already primary.
        if chinese_text and not use_chinese:
            statement["zh"] = {"html": chinese_html, "text": chinese_text}

    stats = (raw or {}).get("stats") or {}
    likes = (raw or {}).get("likes")
    dislikes = (raw or {}).get("dislikes")
    ac_rate = stats.get("ac_rate")
    if ac_rate is None:
        ac_rate = meta.get("ac_rate")

    return {
        "schema_version": SCHEMA_VERSION,
        "uid": f"{SOURCE_ID}:{meta['frontend_id']}",
        "source": SOURCE_ID,
        "source_id": meta["frontend_id"],
        "frontend_id": meta["frontend_id"],
        "slug": meta["slug"],
        "title": html.unescape(meta.get("title") or ""),
        "title_zh": html.unescape(meta.get("title_zh") or meta.get("title") or ""),
        "difficulty": meta.get("difficulty", "Unknown"),
        "difficulty_level": DIFFICULTY_LEVEL.get(meta.get("difficulty", ""), 0),
        "paid_only": bool(meta.get("paid_only")),
        "has_statement": bool(statement and statement["text"]),
        "statement_lang": (statement or {}).get("lang"),
        "statement": statement,
        "topics": cls["topics"],
        "topic_ids": cls["topic_ids"],
        "topic_scores": {t["id"]: t["score"] for t in cls["topics"]},
        "primary_topic": cls["primary_topic"],
        "curated_topic_ids": cls["curated_topic_ids"],
        "classification_confidence": cls["confidence"],
        "needs_review": cls["needs_review"],
        "classification": {
            "method": cls["method"],
            "confidence": cls["confidence"],
            "in_scope": cls["in_scope"],
            "scope": cls["scope"],
            "needs_review": cls["needs_review"],
            "curated_topic_ids": cls["curated_topic_ids"],
        },
        "in_scope": cls["in_scope"],
        "scope": cls["scope"],
        "tags": tags,
        "tag_slugs": [t["slug"] for t in tags],
        "tag_names": [t["name"] for t in tags],
        "tag_names_zh": [t["name_zh"] for t in tags],
        "code_snippets": code,
        "meta_data": (raw or {}).get("meta_data") or {},
        "stats": {
            "ac_rate": ac_rate,
            "total_accepted": stats.get("total_accepted"),
            "total_submission": stats.get("total_submission"),
        },
        "ac_rate": ac_rate,
        "likes": likes,
        "dislikes": dislikes,
        "official_url": f"https://leetcode.cn/problems/{meta['slug']}/",
        "statement_source": "leetcode.cn",
        "fetched_at": (raw or {}).get("fetched_at") or "",
        "license": LICENSE_NOTE,
    }


def write_outputs(full_records: list[dict], topics_cfg: dict, generated_at: str, out: Path = OUT) -> None:
    out_problems = out / "problems"
    out_topics = out / "by-topic"
    for directory in (out, out_problems, out_topics):
        directory.mkdir(parents=True, exist_ok=True)

    compact = [compact_record(r) for r in full_records]
    compact.sort(key=lambda r: r["uid"])
    dump_json(out / "index.json", compact)
    with (out / "index.jsonl").open("w") as fh:
        for record in compact:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    for full in full_records:
        if full["has_statement"]:
            dump_json(out_problems / f"{full['slug']}.json", full)

    # Per-topic extracts, preserving multiple memberships.
    by_uid = {r["uid"]: r for r in compact}
    topic_counts = {}
    for cat in topics_cfg["categories"]:
        rows = []
        for full in full_records:
            match = next((t for t in full["topics"] if t["id"] == cat["id"]), None)
            if not match:
                continue
            base = dict(by_uid[full["uid"]])
            base.update({
                "topic_match": {
                    "score": match["score"],
                    "is_primary": match["is_primary"],
                    "matched_tags": match["matched_tags"],
                    "reason": match["reason"],
                },
            })
            rows.append(base)
        rows.sort(key=lambda r: (not r["topic_match"]["is_primary"], r["difficulty_level"], r["uid"]))
        doc = {
            "schema_version": SCHEMA_VERSION,
            "topic": {
                "id": cat["id"],
                "order": cat["order"],
                "label_zh": cat["label"],
                "short_zh": cat["short"],
                "accent": cat["accent"],
                "blurb": cat["blurb"],
                "patterns": cat["patterns"],
                "tag_weights": cat["tag_weights"],
                "must_ids": cat.get("must", []),
                "exclude_tags": cat.get("exclude_tags", []),
            },
            "generated_at": generated_at,
            "count": len(rows),
            "primary_count": sum(1 for r in rows if r["topic_match"]["is_primary"]),
            "free_with_statement_count": sum(1 for r in rows if r["has_statement"]),
            "problems": rows,
        }
        dump_json(out_topics / f"{cat['id']}.json", doc)
        topic_counts[cat["id"]] = {
            "count": len(rows),
            "primary_count": doc["primary_count"],
            "free_with_statement_count": doc["free_with_statement_count"],
        }

    topics_doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "classification": topics_cfg["classification"],
        "categories": [
            {
                "id": c["id"],
                "order": c["order"],
                "label_zh": c["label"],
                "short_zh": c["short"],
                "accent": c["accent"],
                "blurb": c["blurb"],
                "patterns": c["patterns"],
                "tag_weights": c["tag_weights"],
                "must_ids": c.get("must", []),
                "exclude_tags": c.get("exclude_tags", []),
                **topic_counts.get(c["id"], {}),
            }
            for c in topics_cfg["categories"]
        ],
    }
    dump_json(out / "topics.json", topics_doc)

    pending = [r for r in compact if r["scope"] == "pending"]
    dump_json(out / "pending-classification.json", {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "count": len(pending),
        "note": (
            "LeetCode currently exposes no topic tags for these questions. They are "
            "kept out of the 15 topic files rather than guessed. Review or enrich "
            "later, then add rules to config/topics.json."
        ),
        "problems": pending,
    })

    out_of_scope = [r for r in compact if not r["in_scope"] and r["scope"] != "pending"]
    scope_counts = Counter(r["scope"] for r in out_of_scope)
    dump_json(out / "out-of-scope.json", {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "count": len(out_of_scope),
        "counts_by_scope": dict(sorted(scope_counts.items())),
        "note": "JavaScript, Pandas, SQL/Shell/Concurrency tasks outside the 15 algorithm topics. Kept for index completeness.",
        "problems": out_of_scope,
    })

    free = [r for r in compact if r["has_statement"]]
    in_scope = [r for r in compact if r["in_scope"]]
    daily_ready = [r for r in in_scope if r["has_statement"] and not r["paid_only"]]
    statement_languages = Counter(
        (r.get("statement") or {}).get("lang")
        for r in full_records
        if r.get("has_statement")
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": "algo-bank",
        "generated_at": generated_at,
        "source": {
            "id": SOURCE_ID,
            "name": "LeetCode 中文站",
            "homepage": "https://leetcode.cn/problemset/",
            "metadata": "all public questions, including premium titles",
            "statements": "free questions only; premium statements not copied",
        },
        "counts": {
            "total": len(compact),
            "free_with_statement": len(free),
            "paid_metadata_only": sum(1 for r in compact if r["paid_only"]),
            "in_scope": len(in_scope),
            "in_scope_free_with_statement": len(daily_ready),
            "out_of_scope": len(out_of_scope),
            "pending_classification": len(pending),
            "statement_languages": dict(sorted(statement_languages.items())),
            "topics": len(topics_cfg["categories"]),
        },
        "topics": topic_counts,
        "out_of_scope": dict(sorted(scope_counts.items())),
        "pending_classification": len(pending),
        "files": {
            "index_json": "data/normalized/index.json",
            "index_jsonl": "data/normalized/index.jsonl",
            "full_records": "data/normalized/problems/<slug>.json",
            "by_topic": "data/normalized/by-topic/<topic-id>.json",
            "sqlite": "data/algorithms.db",
            "api": "algo_store.py",
        },
        "license": {
            "statements": LICENSE_NOTE,
            "metadata": "Public metadata, retained for identification and linking.",
            "redistribution": "Do not publish the statement corpus or SQLite database publicly.",
        },
    }
    dump_json(out / "manifest.json", manifest)


def build_sqlite(full_records: list[dict], topics_cfg: dict, generated_at: str, db_path: Path = DB_PATH) -> None:
    tmp_path = db_path.with_suffix(".building.db")
    if tmp_path.exists():
        tmp_path.unlink()
    conn = sqlite3.connect(tmp_path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sources (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, homepage TEXT,
            metadata_policy TEXT, statement_policy TEXT, license_note TEXT
        );
        CREATE TABLE topics (
            id TEXT PRIMARY KEY, sort_order INTEGER NOT NULL, label_zh TEXT NOT NULL,
            short_zh TEXT, accent TEXT, blurb TEXT, patterns_json TEXT,
            tag_weights_json TEXT, must_ids_json TEXT, exclude_tags_json TEXT
        );
        CREATE TABLE problems (
            uid TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            frontend_id TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            title_zh TEXT,
            difficulty TEXT,
            difficulty_level INTEGER,
            paid_only INTEGER NOT NULL,
            has_statement INTEGER NOT NULL,
            in_scope INTEGER NOT NULL,
            scope TEXT,
            primary_topic TEXT,
            classification_confidence TEXT,
            needs_review INTEGER NOT NULL DEFAULT 0,
            ac_rate REAL,
            likes INTEGER,
            dislikes INTEGER,
            statement_lang TEXT,
            statement_html TEXT,
            statement_text TEXT,
            examples TEXT,
            sample_testcase TEXT,
            hints_json TEXT,
            code_snippets_json TEXT,
            meta_data_json TEXT,
            topic_ids_json TEXT,
            topic_scores_json TEXT,
            curated_topic_ids_json TEXT,
            tag_slugs_json TEXT,
            official_url TEXT,
            fetched_at TEXT,
            license TEXT
        );
        CREATE TABLE tags (
            slug TEXT PRIMARY KEY, name TEXT, name_zh TEXT
        );
        CREATE TABLE problem_tags (
            problem_uid TEXT NOT NULL,
            tag_slug TEXT NOT NULL,
            PRIMARY KEY (problem_uid, tag_slug)
        );
        CREATE TABLE problem_topics (
            problem_uid TEXT NOT NULL,
            topic_id TEXT NOT NULL,
            score REAL NOT NULL,
            is_primary INTEGER NOT NULL,
            curated INTEGER NOT NULL,
            matched_tags_json TEXT,
            reason TEXT,
            PRIMARY KEY (problem_uid, topic_id)
        );
        CREATE INDEX idx_problems_difficulty ON problems(difficulty_level);
        CREATE INDEX idx_problems_scope ON problems(in_scope);
        CREATE INDEX idx_problems_primary ON problems(primary_topic);
        CREATE INDEX idx_problems_paid ON problems(paid_only);
        CREATE INDEX idx_problem_topics_topic ON problem_topics(topic_id);
        CREATE INDEX idx_problem_tags_tag ON problem_tags(tag_slug);
        CREATE VIEW v_problem_list AS
            SELECT uid, source, frontend_id, slug, title, title_zh, difficulty,
                   difficulty_level, paid_only, has_statement, in_scope, scope,
                   primary_topic, ac_rate, likes, topic_ids_json, tag_slugs_json,
                   official_url
            FROM problems;
        CREATE VIEW v_daily_candidates AS
            SELECT * FROM problems
            WHERE in_scope = 1 AND paid_only = 0 AND has_statement = 1;
        CREATE VIEW v_pending_classification AS
            SELECT * FROM problems WHERE scope = 'pending';
    """)

    conn.execute("INSERT INTO meta VALUES (?, ?)", ("schema_version", SCHEMA_VERSION))
    conn.execute("INSERT INTO meta VALUES (?, ?)", ("generated_at", generated_at))
    conn.execute("INSERT INTO meta VALUES (?, ?)", ("source", SOURCE_ID))
    conn.execute("INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)", (
        SOURCE_ID,
        "LeetCode 中文站",
        "https://leetcode.cn/problemset/",
        "All public question metadata, including premium titles.",
        "Free question statements only. Premium statements are not copied.",
        LICENSE_NOTE,
    ))

    for cat in topics_cfg["categories"]:
        conn.execute("INSERT INTO topics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            cat["id"], cat["order"], cat["label"], cat["short"], cat["accent"],
            cat["blurb"], json.dumps(cat["patterns"], ensure_ascii=False),
            json.dumps(cat["tag_weights"], ensure_ascii=False),
            json.dumps(cat.get("must", []), ensure_ascii=False),
            json.dumps(cat.get("exclude_tags", []), ensure_ascii=False),
        ))

    tag_seen = {}
    for rec in full_records:
        st = rec.get("statement") or {}
        conn.execute("""
            INSERT INTO problems VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
        """, (
            rec["uid"], rec["source"], rec["source_id"], rec["frontend_id"], rec["slug"],
            rec["title"], rec["title_zh"], rec["difficulty"], rec["difficulty_level"],
            int(rec["paid_only"]), int(rec["has_statement"]), int(rec["in_scope"]),
            rec["scope"], rec["primary_topic"], rec["classification"]["confidence"],
            int(rec["classification"].get("needs_review", False)),
            rec["ac_rate"], rec["likes"], rec["dislikes"],
            st.get("lang"), st.get("html"), st.get("text"), st.get("examples"),
            st.get("sample_testcase"), json.dumps(st.get("hints") or [], ensure_ascii=False),
            json.dumps(rec["code_snippets"], ensure_ascii=False),
            json.dumps(rec["meta_data"], ensure_ascii=False),
            json.dumps(rec["topic_ids"], ensure_ascii=False),
            json.dumps({t["id"]: t["score"] for t in rec["topics"]}, ensure_ascii=False),
            json.dumps(rec["classification"]["curated_topic_ids"], ensure_ascii=False),
            json.dumps(rec["tag_slugs"], ensure_ascii=False),
            rec["official_url"], rec["fetched_at"], rec["license"],
        ))
        for tag in rec["tags"]:
            tag_seen[tag["slug"]] = tag
            conn.execute(
                "INSERT OR IGNORE INTO problem_tags VALUES (?, ?)",
                (rec["uid"], tag["slug"]),
            )
        for topic in rec["topics"]:
            conn.execute("INSERT INTO problem_topics VALUES (?,?,?,?,?,?,?)", (
                rec["uid"], topic["id"], topic["score"], int(topic["is_primary"]),
                int(topic["curated"]), json.dumps(topic["matched_tags"], ensure_ascii=False),
                topic["reason"],
            ))

    for tag in tag_seen.values():
        conn.execute(
            "INSERT OR REPLACE INTO tags VALUES (?, ?, ?)",
            (tag["slug"], tag["name"], tag["name_zh"]),
        )

    # FTS is a convenience index. The query API also supports LIKE so Chinese
    # search works even when trigram tokenization is unavailable.
    fts_status = "unavailable"
    for tokenizer in ("trigram", "unicode61"):
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE problems_fts USING fts5("
                "uid UNINDEXED, title, title_zh, statement_text, tags_text, "
                f"tokenize='{tokenizer}')"
            )
            fts_status = tokenizer
            break
        except sqlite3.OperationalError:
            continue
    if fts_status != "unavailable":
        for rec in full_records:
            st = rec.get("statement") or {}
            conn.execute(
                "INSERT INTO problems_fts VALUES (?, ?, ?, ?, ?)",
                (
                    rec["uid"], rec["title"], rec["title_zh"],
                    st.get("text") or "", " ".join(rec["tag_slugs"]),
                ),
            )
    conn.execute("INSERT INTO meta VALUES (?, ?)", ("fts_tokenizer", fts_status))

    conn.commit()
    counts = {
        "problems": conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0],
        "full_statements": conn.execute(
            "SELECT COUNT(*) FROM problems WHERE has_statement=1"
        ).fetchone()[0],
        "problem_topics": conn.execute("SELECT COUNT(*) FROM problem_topics").fetchone()[0],
        "tags": conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
    }
    conn.close()

    if counts["problems"] != len(full_records):
        raise RuntimeError(f"SQLite row mismatch: {counts['problems']} != {len(full_records)}")
    tmp_path.replace(db_path)
    print(f"sqlite: {db_path} ({counts}) fts={fts_status}")


def publish_dataset(full_records: list[dict], topics_cfg: dict, generated_at: str, skip_db: bool = False) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dataset-build-", dir=OUT.parent) as tmp:
        staging = Path(tmp)
        normalized = staging / "normalized"
        database = staging / "algorithms.db"
        previous = staging / "previous"
        write_outputs(full_records, topics_cfg, generated_at, normalized)
        if not skip_db:
            build_sqlite(full_records, topics_cfg, generated_at, database)

        had_previous = OUT.exists()
        if had_previous:
            OUT.replace(previous)
        try:
            normalized.replace(OUT)
            if not skip_db:
                database.replace(DB_PATH)
        except OSError:
            if OUT.exists():
                shutil.rmtree(OUT)
            if had_previous:
                previous.replace(OUT)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the normalized question bank")
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    if not RAW_INDEX.exists():
        print(f"missing {RAW_INDEX}; run scripts/fetch_leetcode.py first", file=sys.stderr)
        return 2

    topics_cfg = load_json(ROOT / "config" / "topics.json")
    raw_index = load_json(RAW_INDEX)
    generated_at = now()
    full_records = []
    missing = []
    for meta in raw_index:
        raw = None
        raw_path = RAW_PROBLEMS / f"{meta['slug']}.json"
        if raw_path.exists():
            raw = load_json(raw_path)
        elif not meta.get("paid_only"):
            missing.append(meta["slug"])
        tags = normalize_tags(meta.get("tags", []))
        cls = classify(meta, topics_cfg, raw)
        full = build_full(meta, raw, cls, tags)
        full_records.append(full)

    if missing:
        print(
            f"error: {len(missing)} free problems have no raw statement; first: {missing[:5]}. "
            "Run make refresh to retry fetching; previous dataset retained.",
            file=sys.stderr,
        )
        return 1

    # Deterministic id order, matching the source index.
    full_records.sort(key=lambda r: (int(r["source_id"]) if r["source_id"].isdigit() else 10**9, r["slug"]))
    publish_dataset(full_records, topics_cfg, generated_at, skip_db=args.skip_db)

    manifest = load_json(OUT / "manifest.json")
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    print("\ntopic counts:")
    for cat in topics_cfg["categories"]:
        t = manifest["topics"][cat["id"]]
        print(f"  {cat['id']:<32} {t['count']:>5} total  {t['primary_count']:>4} primary  {t['free_with_statement_count']:>4} with statement")
    print(f"\nnormalized: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validate the normalized dataset, JSONL exports, and SQLite mirror."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NORM = ROOT / "data" / "normalized"
DB = ROOT / "data" / "algorithms.db"
TOPICS_CFG = ROOT / "config" / "topics.json"
TOPIC_IDS = None


def fail(errors: list[str], message: str) -> None:
    errors.append(message)
    print("FAIL:", message)


def main() -> int:
    errors: list[str] = []
    topics_cfg = json.loads(TOPICS_CFG.read_text())
    topic_ids = [c["id"] for c in topics_cfg["categories"]]
    if len(topic_ids) != 15:
        fail(errors, f"expected 15 topics, got {len(topic_ids)}")
    if len(set(topic_ids)) != len(topic_ids):
        fail(errors, "duplicate topic ids")

    index = json.loads((NORM / "index.json").read_text())
    index_by_uid = {r["uid"]: r for r in index}
    manifest = json.loads((NORM / "manifest.json").read_text())
    if len(index) != manifest["counts"]["total"]:
        fail(errors, "index count != manifest total")
    if len({r["uid"] for r in index}) != len(index):
        fail(errors, "duplicate uid in index")
    if len({r["slug"] for r in index}) != len(index):
        fail(errors, "duplicate slug in index")

    # JSONL must exactly mirror the JSON index.
    jsonl_lines = [
        json.loads(line)
        for line in (NORM / "index.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if len(jsonl_lines) != len(index):
        fail(errors, f"index.jsonl has {len(jsonl_lines)} rows, index has {len(index)}")
    if {r["uid"] for r in jsonl_lines} != set(index_by_uid):
        fail(errors, "index.jsonl uid set differs from index.json")

    in_scope = [r for r in index if r["in_scope"]]
    free = [r for r in index if r["has_statement"]]
    paid = [r for r in index if r["paid_only"]]
    pending = [r for r in index if r["scope"] == "pending"]
    out_scope = [r for r in index if not r["in_scope"] and r["scope"] != "pending"]

    if len(free) != manifest["counts"]["free_with_statement"]:
        fail(errors, "free count mismatch")
    if len(in_scope) != manifest["counts"]["in_scope"]:
        fail(errors, "in_scope count mismatch")
    if len(pending) != manifest["counts"]["pending_classification"]:
        fail(errors, "pending count mismatch")
    if len(out_scope) != manifest["counts"]["out_of_scope"]:
        fail(errors, "out_of_scope count mismatch")

    for record in index:
        uid = record["uid"]
        if not record["in_scope"]:
            if record["primary_topic"] is not None or record["topic_ids"]:
                fail(errors, f"{uid}: out-of-scope record has topics")
            continue
        if record["primary_topic"] not in topic_ids:
            fail(errors, f"{uid}: primary_topic missing/unknown")
        if not record["topic_ids"] or record["topic_ids"][0] != record["primary_topic"]:
            fail(errors, f"{uid}: primary topic is not first in topic_ids")
        if len(set(record["topic_ids"])) != len(record["topic_ids"]):
            fail(errors, f"{uid}: duplicate topic ids")
        if any(t not in topic_ids for t in record["topic_ids"]):
            fail(errors, f"{uid}: unknown topic id")
        if record["topic_scores"].get(record["primary_topic"]) != max(record["topic_scores"].values()):
            fail(errors, f"{uid}: primary is not the highest-scoring topic")
        if record["paid_only"] and record["has_statement"]:
            fail(errors, f"{uid}: paid problem marked as having a local statement")

    # Every topic extract must agree with index membership.
    for topic_id in topic_ids:
        path = NORM / "by-topic" / f"{topic_id}.json"
        if not path.exists():
            fail(errors, f"missing topic file {topic_id}")
            continue
        doc = json.loads(path.read_text())
        expected = [r for r in in_scope if topic_id in r["topic_ids"]]
        if doc["count"] != len(expected):
            fail(errors, f"{topic_id}: file count {doc['count']} != index {len(expected)}")
        if set(r["uid"] for r in doc["problems"]) != {r["uid"] for r in expected}:
            fail(errors, f"{topic_id}: uid set differs from index")
        if doc["primary_count"] != sum(1 for r in expected if r["primary_topic"] == topic_id):
            fail(errors, f"{topic_id}: primary_count mismatch")
        for row in doc["problems"]:
            if not row.get("topic_match"):
                fail(errors, f"{topic_id}: record missing topic_match")
                break
            if topic_id not in row["topic_ids"]:
                fail(errors, f"{topic_id}: record not in its own topic_ids")
                break

    # Full records: only free problems, statement must be usable.
    full_paths = list((NORM / "problems").glob("*.json"))
    if len(full_paths) != len(free):
        fail(errors, f"full record count {len(full_paths)} != free count {len(free)}")
    free_slugs = {r["slug"] for r in free}
    for path in full_paths:
        record = json.loads(path.read_text())
        if record["slug"] != path.stem:
            fail(errors, f"{path.name}: slug/path mismatch")
        if record["paid_only"]:
            fail(errors, f"{path.name}: paid statement stored locally")
        statement = record.get("statement") or {}
        if not statement.get("text") or not statement.get("html"):
            fail(errors, f"{path.name}: empty statement")
        if statement.get("lang") not in {"en", "zh"}:
            fail(errors, f"{path.name}: invalid statement language")
        if "english description is not available" in (statement.get("text") or "").lower():
            fail(errors, f"{path.name}: English placeholder was not backfilled")
        if record["in_scope"] and len(statement.get("text") or "") < 80:
            fail(errors, f"{path.name}: in-scope statement too short")
        if record["slug"] not in free_slugs:
            fail(errors, f"{path.name}: full record not in free index")
        if record["in_scope"]:
            if not record.get("topics"):
                fail(errors, f"{path.name}: in-scope record has no topics")
            if sum(1 for t in record["topics"] if t["is_primary"]) != 1:
                fail(errors, f"{path.name}: expected exactly one primary topic")
        elif record.get("topics"):
            fail(errors, f"{path.name}: out-of-scope record has topics")

    # License cannot be lost from the full record.
    for path in full_paths[:200]:
        record = json.loads(path.read_text())
        if "personal study" not in record.get("license", ""):
            fail(errors, f"{path.name}: license note missing")
            break

    # Pending and out-of-scope files.
    pending_doc = json.loads((NORM / "pending-classification.json").read_text())
    if pending_doc["count"] != len(pending):
        fail(errors, "pending file count mismatch")
    out_doc = json.loads((NORM / "out-of-scope.json").read_text())
    if out_doc["count"] != len(out_scope):
        fail(errors, "out-of-scope file count mismatch")
    # Curated ids must exist and actually match their category.
    by_source_id = {r["source_id"]: r for r in index}
    for category in topics_cfg["categories"]:
        for pid in category["must"]:
            record = by_source_id.get(str(pid))
            if not record:
                fail(errors, f"{category['id']}: curated id {pid} not in index")
            elif record["in_scope"] and category["id"] not in record["topic_ids"]:
                fail(errors, f"{category['id']}: curated id {pid} does not match category")

    # SQLite mirror checks.
    if not DB.exists():
        fail(errors, "SQLite database missing")
    else:
        conn = sqlite3.connect(DB)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            fail(errors, f"sqlite integrity_check: {integrity}")
        db_problems = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
        if db_problems != len(index):
            fail(errors, f"sqlite problems {db_problems} != index {len(index)}")
        db_full = conn.execute(
            "SELECT COUNT(*) FROM problems WHERE has_statement=1"
        ).fetchone()[0]
        if db_full != len(free):
            fail(errors, f"sqlite full statements {db_full} != free {len(free)}")
        bad_paid = conn.execute(
            "SELECT COUNT(*) FROM problems WHERE paid_only=1 "
            "AND (statement_text IS NOT NULL OR statement_html IS NOT NULL)"
        ).fetchone()[0]
        if bad_paid:
            fail(errors, f"sqlite stores {bad_paid} paid statements")
        empty_free = conn.execute(
            "SELECT COUNT(*) FROM problems WHERE has_statement=1 "
            "AND (statement_text IS NULL OR length(trim(statement_text))=0)"
        ).fetchone()[0]
        if empty_free:
            fail(errors, f"sqlite has {empty_free} empty free statements")
        orphan_topics = conn.execute(
            "SELECT COUNT(*) FROM problem_topics pt "
            "LEFT JOIN problems p ON p.uid=pt.problem_uid WHERE p.uid IS NULL"
        ).fetchone()[0]
        if orphan_topics:
            fail(errors, f"sqlite has {orphan_topics} orphan problem_topics")
        no_primary = conn.execute(
            "SELECT COUNT(*) FROM problems p WHERE p.in_scope=1 "
            "AND NOT EXISTS (SELECT 1 FROM problem_topics pt "
            "WHERE pt.problem_uid=p.uid AND pt.is_primary=1)"
        ).fetchone()[0]
        if no_primary:
            fail(errors, f"sqlite has {no_primary} in-scope problems without primary topic")
        duplicate_primary = conn.execute(
            "SELECT COUNT(*) FROM (SELECT problem_uid, COUNT(*) c FROM problem_topics "
            "WHERE is_primary=1 GROUP BY problem_uid HAVING c>1)"
        ).fetchone()[0]
        if duplicate_primary:
            fail(errors, f"sqlite has {duplicate_primary} multiple-primary problems")
        topic_count = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        if topic_count != 15:
            fail(errors, f"sqlite topics {topic_count} != 15")
        conn.close()

    if errors:
        print(f"\n{len(errors)} validation error(s)")
        return 1

    print("OK: dataset is internally consistent")
    print(json.dumps({
        "total": len(index),
        "free_with_statement": len(free),
        "paid_metadata_only": len(paid),
        "in_scope": len(in_scope),
        "in_scope_free_with_statement": sum(
            1 for r in index if r["in_scope"] and not r["paid_only"] and r["has_statement"]
        ),
        "topics": len(topic_ids),
        "pending_classification": len(pending),
        "out_of_scope": len(out_scope),
        "full_records": len(full_paths),
        "sqlite": str(DB.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

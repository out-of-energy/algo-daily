#!/usr/bin/env python3
"""Small query layer over the normalized algorithm question bank.

Examples
--------
    python3 algo_store.py stats
    python3 algo_store.py topics
    python3 algo_store.py list --topic graph --difficulty Medium --free --limit 10
    python3 algo_store.py random --topic dynamic-programming --free
    python3 algo_store.py show two-sum
    python3 algo_store.py export --topic graph --free --out work/graph.jsonl

Library use
-----------
    from algo_store import ProblemStore
    store = ProblemStore()
    rows = store.list(topic="graph", difficulty="Medium", free=True, limit=20)
    problem = store.get("number-of-islands")
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "algorithms.db"
FULL_DIR = ROOT / "data" / "normalized" / "problems"
DIFFICULTIES = {"easy": "Easy", "medium": "Medium", "hard": "Hard"}


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ProblemStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"{self.db_path} not found; run `python3 scripts/build_dataset.py`"
            )
        self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ProblemStore":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @staticmethod
    def _compact(row: sqlite3.Row) -> dict:
        return {
            "uid": row["uid"],
            "source": row["source"],
            "source_id": row["source_id"],
            "frontend_id": row["frontend_id"],
            "slug": row["slug"],
            "title": row["title"],
            "title_zh": row["title_zh"],
            "difficulty": row["difficulty"],
            "difficulty_level": row["difficulty_level"],
            "paid_only": bool(row["paid_only"]),
            "has_statement": bool(row["has_statement"]),
            "statement_lang": row["statement_lang"],
            "in_scope": bool(row["in_scope"]),
            "scope": row["scope"],
            "primary_topic": row["primary_topic"],
            "classification_confidence": row["classification_confidence"],
            "needs_review": bool(row["needs_review"]),
            "topic_ids": _json_load(row["topic_ids_json"], []),
            "topic_scores": _json_load(row["topic_scores_json"], {}),
            "curated_topic_ids": _json_load(row["curated_topic_ids_json"], []),
            "tags": _json_load(row["tag_slugs_json"], []),
            "ac_rate": row["ac_rate"],
            "likes": row["likes"],
            "dislikes": row["dislikes"],
            "official_url": row["official_url"],
        }

    def _build_query(
        self,
        topic: str | None = None,
        difficulty: str | None = None,
        free: bool = False,
        require_statement: bool = False,
        primary_only: bool = False,
        tag: str | None = None,
        search: str | None = None,
        include_out_of_scope: bool = False,
        source: str | None = None,
        scope: str | None = None,
        lang: str | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        joins = ""
        if topic:
            joins += (
                " JOIN problem_topics pt ON pt.problem_uid = p.uid"
                " AND pt.topic_id = ?"
            )
            params.append(topic)
            if primary_only:
                clauses.append("pt.is_primary = 1")
        elif primary_only:
            clauses.append("p.primary_topic IS NOT NULL")
        if tag:
            joins += " JOIN problem_tags ptg ON ptg.problem_uid = p.uid AND ptg.tag_slug = ?"
            params.append(tag)
        if difficulty:
            level = DIFFICULTIES.get(difficulty.lower())
            if not level:
                raise ValueError(f"difficulty must be one of {sorted(DIFFICULTIES.values())}")
            clauses.append("p.difficulty = ?")
            params.append(level)
        if free:
            clauses.append("p.paid_only = 0")
        if require_statement:
            clauses.append("p.has_statement = 1")
        if scope:
            clauses.append("p.scope = ?")
            params.append(scope)
        elif not include_out_of_scope:
            clauses.append("p.in_scope = 1")
        if source:
            clauses.append("p.source = ?")
            params.append(source)
        if lang:
            clauses.append("p.statement_lang = ?")
            params.append(lang)
        if search:
            needle = f"%{_escape_like(search)}%"
            clauses.append(
                "(p.title LIKE ? ESCAPE '\\' OR p.title_zh LIKE ? ESCAPE '\\' "
                "OR p.statement_text LIKE ? ESCAPE '\\' OR p.tag_slugs_json LIKE ? ESCAPE '\\')"
            )
            params.extend([needle] * 4)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return joins + where, params

    def list(
        self,
        topic: str | None = None,
        difficulty: str | None = None,
        free: bool = False,
        require_statement: bool = False,
        primary_only: bool = False,
        tag: str | None = None,
        search: str | None = None,
        include_out_of_scope: bool = False,
        source: str | None = None,
        scope: str | None = None,
        lang: str | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "id",
    ) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be positive")
        order_sql = {
            "id": "p.difficulty_level ASC, p.uid ASC",
            "likes": "p.likes DESC NULLS LAST, p.uid ASC",
            "random": "RANDOM()",
        }.get(order)
        if not order_sql:
            raise ValueError("order must be id, likes or random")
        where, params = self._build_query(
            topic, difficulty, free, require_statement, primary_only,
            tag, search, include_out_of_scope, source, scope, lang,
        )
        sql = (
            "SELECT p.* FROM problems p"
            + where
            + f" ORDER BY {order_sql} LIMIT ? OFFSET ?"
        )
        rows = self.conn.execute(sql, [*params, limit, offset]).fetchall()
        return [self._compact(row) for row in rows]

    def count(
        self,
        topic: str | None = None,
        difficulty: str | None = None,
        free: bool = False,
        require_statement: bool = False,
        primary_only: bool = False,
        tag: str | None = None,
        search: str | None = None,
        include_out_of_scope: bool = False,
        source: str | None = None,
        scope: str | None = None,
        lang: str | None = None,
    ) -> int:
        where, params = self._build_query(
            topic, difficulty, free, require_statement, primary_only,
            tag, search, include_out_of_scope, source, scope, lang,
        )
        sql = "SELECT COUNT(DISTINCT p.uid) FROM problems p" + where
        return int(self.conn.execute(sql, params).fetchone()[0])

    def random(
        self,
        topic: str | None = None,
        difficulty: str | None = None,
        free: bool = True,
        primary_only: bool = False,
        tag: str | None = None,
        require_statement: bool = True,
    ) -> dict | None:
        rows = self.list(
            topic=topic,
            difficulty=difficulty,
            free=free,
            require_statement=require_statement,
            primary_only=primary_only,
            tag=tag,
            limit=1,
            order="random",
        )
        return rows[0] if rows else None

    def get(self, slug_or_uid: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM problems WHERE slug = ? OR uid = ?",
            (slug_or_uid, slug_or_uid),
        ).fetchone()
        if not row:
            return None
        full_path = FULL_DIR / f"{row['slug']}.json"
        if row["has_statement"] and full_path.exists() and full_path.parent == FULL_DIR:
            return json.loads(full_path.read_text())
        # Paid/out-of-scope records have no local statement. Rebuild the public
        # metadata shape from SQLite instead.
        topics = []
        for t in self.conn.execute("""
            SELECT pt.topic_id, pt.score, pt.is_primary, pt.curated,
                   pt.matched_tags_json, pt.reason, t.label_zh, t.short_zh
            FROM problem_topics pt JOIN topics t ON t.id = pt.topic_id
            WHERE pt.problem_uid = ? ORDER BY pt.is_primary DESC, pt.score DESC
        """, (row["uid"],)):
            topics.append({
                "id": t["topic_id"],
                "label_zh": t["label_zh"],
                "short_zh": t["short_zh"],
                "score": t["score"],
                "is_primary": bool(t["is_primary"]),
                "curated": bool(t["curated"]),
                "matched_tags": _json_load(t["matched_tags_json"], []),
                "reason": t["reason"],
            })
        tags = [
            dict(t)
            for t in self.conn.execute("""
                SELECT tg.slug, tg.name, tg.name_zh
                FROM problem_tags ptg JOIN tags tg ON tg.slug = ptg.tag_slug
                WHERE ptg.problem_uid = ? ORDER BY tg.slug
            """, (row["uid"],))
        ]
        record = self._compact(row)
        record.update({
            "statement": None,
            "topics": topics,
            "code_snippets": {},
            "meta_data": _json_load(row["meta_data_json"], {}),
            "license": row["license"],
            "note": (
                "Premium statement not stored locally; use official_url."
                if row["paid_only"] else
                "Local statement unavailable; rebuild with `make build` or use official_url."
            ),
        })
        return record

    def topics(self) -> list[dict]:
        result = []
        for row in self.conn.execute("""
            SELECT t.*,
                   COUNT(pt.problem_uid) AS total,
                   SUM(CASE WHEN pt.is_primary=1 THEN 1 ELSE 0 END) AS primary_count,
                   SUM(CASE WHEN p.has_statement=1 THEN 1 ELSE 0 END) AS with_statement
            FROM topics t
            LEFT JOIN problem_topics pt ON pt.topic_id = t.id
            LEFT JOIN problems p ON p.uid = pt.problem_uid
            GROUP BY t.id ORDER BY t.sort_order
        """):
            result.append({
                "id": row["id"],
                "sort_order": row["sort_order"],
                "label_zh": row["label_zh"],
                "short_zh": row["short_zh"],
                "accent": row["accent"],
                "blurb": row["blurb"],
                "patterns": _json_load(row["patterns_json"], []),
                "tag_weights": _json_load(row["tag_weights_json"], {}),
                "must_ids": _json_load(row["must_ids_json"], []),
                "exclude_tags": _json_load(row["exclude_tags_json"], []),
                "total": row["total"],
                "primary_count": row["primary_count"],
                "with_statement": row["with_statement"],
            })
        return result

    def tags(self, limit: int = 500) -> list[dict]:
        rows = self.conn.execute("""
            SELECT tg.slug, tg.name, tg.name_zh, COUNT(ptg.problem_uid) AS count
            FROM tags tg LEFT JOIN problem_tags ptg ON ptg.tag_slug = tg.slug
            GROUP BY tg.slug ORDER BY count DESC, tg.slug LIMIT ?
        """, (limit,))
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        one = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        counts = {
            "total": one("SELECT COUNT(*) FROM problems"),
            "free_with_statement": one(
                "SELECT COUNT(*) FROM problems WHERE paid_only=0 AND has_statement=1"
            ),
            "paid_metadata_only": one(
                "SELECT COUNT(*) FROM problems WHERE paid_only=1"
            ),
            "in_scope": one("SELECT COUNT(*) FROM problems WHERE in_scope=1"),
            "daily_candidates": one("SELECT COUNT(*) FROM v_daily_candidates"),
            "out_of_scope": one(
                "SELECT COUNT(*) FROM problems WHERE in_scope=0 AND scope!='pending'"
            ),
            "pending_classification": one(
                "SELECT COUNT(*) FROM problems WHERE scope='pending'"
            ),
            "tagged_problems": one("SELECT COUNT(DISTINCT problem_uid) FROM problem_tags"),
        }
        by_scope = {
            r["scope"]: r["n"]
            for r in self.conn.execute(
                "SELECT scope, COUNT(*) AS n FROM problems WHERE in_scope=0 GROUP BY scope"
            )
        }
        by_difficulty = {
            r["difficulty"]: r["n"]
            for r in self.conn.execute(
                "SELECT difficulty, COUNT(*) AS n FROM problems WHERE in_scope=1 GROUP BY difficulty"
            )
        }
        by_language = {
            r["statement_lang"]: r["n"]
            for r in self.conn.execute(
                "SELECT statement_lang, COUNT(*) AS n FROM problems "
                "WHERE has_statement=1 GROUP BY statement_lang"
            )
        }
        fts = self.conn.execute(
            "SELECT value FROM meta WHERE key='fts_tokenizer'"
        ).fetchone()
        return {
            "counts": counts,
            "by_scope": by_scope,
            "by_difficulty_in_scope": by_difficulty,
            "by_statement_language": by_language,
            "fts_tokenizer": fts[0] if fts else None,
            "schema_version": self.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0],
            "generated_at": self.conn.execute(
                "SELECT value FROM meta WHERE key='generated_at'"
            ).fetchone()[0],
        }

    def export_jsonl(self, rows: Iterable[dict], path: str | Path) -> int:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with path.open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
        return n


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no matches)")
        return
    header = ["#", "难度", "主专题", "标题", "slug"]
    body = []
    for row in rows:
        body.append([
            str(row["frontend_id"]),
            {"Easy": "易", "Medium": "中", "Hard": "难"}.get(row["difficulty"], "?"),
            row["primary_topic"] or "-",
            row["title_zh"] or row["title"],
            row["slug"],
        ])
    widths = [max(len(str(x)) for x in col) for col in zip(header, *body)]
    # Chinese glyphs are double-width; approximate padding by display width.
    def width(text: str) -> int:
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    def pad(text: str, target: int) -> str:
        return text + " " * max(0, target - width(text))
    print("  ".join(pad(h, w) for h, w in zip(header, widths)))
    print("  ".join("-" * w for w in widths))
    for row in body:
        print("  ".join(pad(str(c), w) for c, w in zip(row, widths)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Query the local algorithm question bank")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", help="dataset statistics")
    p_stats.add_argument("--json", action="store_true")

    sub.add_parser("topics", help="list categories and counts")
    p_tags = sub.add_parser("tags", help="list tags")
    p_tags.add_argument("--limit", type=int, default=40)

    p_list = sub.add_parser("list", help="query problems")
    p_list.add_argument("--topic")
    p_list.add_argument("--difficulty", choices=["Easy", "Medium", "Hard", "easy", "medium", "hard"])
    p_list.add_argument("--tag")
    p_list.add_argument("--search")
    p_list.add_argument("--free", action="store_true", help="non-premium only")
    p_list.add_argument("--with-statement", action="store_true")
    p_list.add_argument("--primary-only", action="store_true")
    p_list.add_argument("--include-out-of-scope", action="store_true")
    p_list.add_argument("--scope", help="algorithm, pending, database, shell, concurrency, javascript, pandas")
    p_list.add_argument("--lang", choices=["en", "zh"], help="statement language")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--order", choices=["id", "likes", "random"], default="id")
    p_list.add_argument("--json", action="store_true")

    p_random = sub.add_parser("random", help="random matching problem")
    p_random.add_argument("--topic")
    p_random.add_argument("--difficulty", choices=["Easy", "Medium", "Hard", "easy", "medium", "hard"])
    p_random.add_argument("--tag")
    p_random.add_argument("--primary-only", action="store_true")
    p_random.add_argument("--free", action="store_true", help="non-premium only (default)")
    p_random.add_argument("--include-paid", action="store_true")
    p_random.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="show one full problem")
    p_show.add_argument("slug_or_uid")

    p_export = sub.add_parser("export", help="export query results as JSONL")
    p_export.add_argument("--out", required=True)
    p_export.add_argument("--topic")
    p_export.add_argument("--difficulty", choices=["Easy", "Medium", "Hard", "easy", "medium", "hard"])
    p_export.add_argument("--tag")
    p_export.add_argument("--free", action="store_true")
    p_export.add_argument("--primary-only", action="store_true")
    p_export.add_argument("--limit", type=int, default=10000)

    args = parser.parse_args()
    if Path(args.db).resolve() == DEFAULT_DB.resolve() and not DEFAULT_DB.exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        from bootstrap_data import bootstrap

        print("题库未构建，先自动构建（只需一次）……", file=sys.stderr)
        if bootstrap() != 0:
            return 1
    store = ProblemStore(args.db)

    if args.command == "stats":
        stats = store.stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif args.command == "topics":
        for t in store.topics():
            print(f"{t['sort_order']:>2}. {t['label_zh']:<24} "
                  f"total={t['total']:<4} primary={t['primary_count']:<4} "
                  f"with_statement={t['with_statement']}")
    elif args.command == "tags":
        for t in store.tags(args.limit):
            print(f"{t['count']:>4}  {t['slug']:<28} {t['name_zh'] or t['name']}")
    elif args.command == "list":
        rows = store.list(
            topic=args.topic,
            difficulty=args.difficulty,
            free=args.free,
            require_statement=args.with_statement,
            primary_only=args.primary_only,
            tag=args.tag,
            search=args.search,
            include_out_of_scope=args.include_out_of_scope,
            scope=args.scope,
            lang=args.lang,
            limit=args.limit,
            offset=args.offset,
            order=args.order,
        )
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _print_table(rows)
            total = store.count(
                topic=args.topic, difficulty=args.difficulty, free=args.free,
                require_statement=args.with_statement, primary_only=args.primary_only,
                tag=args.tag, search=args.search,
                include_out_of_scope=args.include_out_of_scope,
                scope=args.scope,
                lang=args.lang,
            )
            print(f"\nshowing {len(rows)} / {total} matching problems")
    elif args.command == "random":
        if args.free and args.include_paid:
            parser.error("--free and --include-paid cannot be combined")
        row = store.random(
            topic=args.topic, difficulty=args.difficulty, free=not args.include_paid,
            primary_only=args.primary_only, tag=args.tag,
        )
        if not row:
            print("(no matches)", file=sys.stderr)
            return 1
        print(json.dumps(store.get(row["slug"]), ensure_ascii=False, indent=2)
              if args.json else
              f"[{row['frontend_id']}] {row['title_zh']} ({row['difficulty']}) "
              f"primary={row['primary_topic']} slug={row['slug']}")
    elif args.command == "show":
        record = store.get(args.slug_or_uid)
        if not record:
            print("(not found)", file=sys.stderr)
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2))
    elif args.command == "export":
        rows = store.list(
            topic=args.topic, difficulty=args.difficulty, free=args.free,
            primary_only=args.primary_only, tag=args.tag, limit=args.limit,
        )
        n = store.export_jsonl(rows, args.out)
        print(f"exported {n} rows -> {args.out}")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

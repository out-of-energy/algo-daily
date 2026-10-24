"""SQLite mirror tests: it must not drift from the JSON catalog."""

from __future__ import annotations

import json
import unittest
from collections import Counter

import support


@support.requires_data
class SQLiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = support.load_index()
        cls.by_slug = support.load_index_by_slug()
        cls.conn = support.connect()
        cls.conn.row_factory = __import__("sqlite3").Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()

    def test_integrity_and_counts(self) -> None:
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        one = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        self.assertEqual(one("SELECT COUNT(*) FROM problems"), support.expected()["total"])
        self.assertEqual(
            one("SELECT COUNT(*) FROM problems WHERE has_statement=1"),
            support.expected()["free_with_statement"],
        )
        self.assertEqual(
            one("SELECT COUNT(*) FROM problems WHERE paid_only=1"),
            support.expected()["paid_metadata_only"],
        )
        self.assertEqual(
            one("SELECT COUNT(*) FROM v_daily_candidates"),
            support.expected()["in_scope_free_with_statement"],
        )
        self.assertEqual(one("SELECT COUNT(*) FROM topics"), 15)
        self.assertEqual(
            one("SELECT COUNT(*) FROM problems WHERE in_scope=1"),
            support.expected()["in_scope"],
        )

    def test_paid_never_has_statement(self) -> None:
        bad = self.conn.execute("""
            SELECT COUNT(*) FROM problems
            WHERE paid_only=1 AND (
                statement_html IS NOT NULL OR statement_text IS NOT NULL
                OR statement_lang IS NOT NULL OR has_statement=1
            )
        """).fetchone()[0]
        self.assertEqual(bad, 0)
        empty_free = self.conn.execute("""
            SELECT COUNT(*) FROM problems
            WHERE paid_only=0 AND (
                has_statement=0 OR trim(coalesce(statement_text, ''))=''
                OR trim(coalesce(statement_html, ''))=''
            )
        """).fetchone()[0]
        self.assertEqual(empty_free, 0)

    def test_statement_language_and_placeholders(self) -> None:
        rows = self.conn.execute(
            "SELECT statement_lang, COUNT(*) AS n FROM problems "
            "WHERE has_statement=1 GROUP BY statement_lang"
        ).fetchall()
        self.assertEqual(
            {r["statement_lang"]: r["n"] for r in rows},
            {"en": support.expected()["statement_en"], "zh": support.expected()["statement_zh"]},
        )
        placeholder = self.conn.execute("""
            SELECT COUNT(*) FROM problems
            WHERE lower(statement_text) LIKE '%english description is not available%'
        """).fetchone()[0]
        self.assertEqual(placeholder, 0)
        short = self.conn.execute("""
            SELECT COUNT(*) FROM problems
            WHERE in_scope=1 AND has_statement=1 AND length(trim(statement_text)) < 80
        """).fetchone()[0]
        self.assertEqual(short, 0)

    def test_problem_rows_match_index(self) -> None:
        rows = self.conn.execute("SELECT * FROM problems").fetchall()
        self.assertEqual(len(rows), len(self.index))
        for row in rows:
            record = self.by_slug[row["slug"]]
            self.assertEqual(row["uid"], record["uid"])
            self.assertEqual(row["source_id"], record["source_id"])
            self.assertEqual(row["frontend_id"], record["frontend_id"])
            self.assertEqual(row["title"], record["title"])
            self.assertEqual(row["title_zh"], record["title_zh"])
            self.assertEqual(row["difficulty"], record["difficulty"])
            self.assertEqual(row["difficulty_level"], record["difficulty_level"])
            self.assertEqual(bool(row["paid_only"]), record["paid_only"])
            self.assertEqual(bool(row["has_statement"]), record["has_statement"])
            self.assertEqual(row["statement_lang"], record["statement_lang"])
            self.assertEqual(bool(row["in_scope"]), record["in_scope"])
            self.assertEqual(row["scope"], record["scope"])
            self.assertEqual(row["primary_topic"], record["primary_topic"])
            self.assertEqual(
                row["classification_confidence"],
                record["classification_confidence"],
            )
            self.assertEqual(bool(row["needs_review"]), record["needs_review"])
            self.assertEqual(json.loads(row["topic_ids_json"]), record["topic_ids"])
            self.assertEqual(json.loads(row["topic_scores_json"]), record["topic_scores"])
            self.assertEqual(json.loads(row["tag_slugs_json"]), record["tags"])
            self.assertEqual(row["official_url"], record["official_url"])

    def test_statement_text_matches_full_record(self) -> None:
        slugs = [
            "two-sum",
            "number-of-islands",
            "programmable-robot",
            "0Zeoeg",
            "lru-cache",
            "longest-substring-without-repeating-characters",
        ]
        for slug in slugs:
            full = support.load_full(slug)
            row = self.conn.execute(
                "SELECT statement_html, statement_text, statement_lang "
                "FROM problems WHERE slug=?", (slug,)
            ).fetchone()
            self.assertEqual(row["statement_html"], full["statement"]["html"], slug)
            self.assertEqual(row["statement_text"], full["statement"]["text"], slug)
            self.assertEqual(row["statement_lang"], full["statement"]["lang"], slug)

    def test_topic_join_matches_topic_files(self) -> None:
        topic_cfg = support.load_topic_cfg()
        for category in topic_cfg["categories"]:
            tid = category["id"]
            row = self.conn.execute("""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN is_primary=1 THEN 1 ELSE 0 END) AS primary_n
                FROM problem_topics WHERE topic_id=?
            """, (tid,)).fetchone()
            doc = json.loads(
                (support.NORM / "by-topic" / f"{tid}.json").read_text()
            )
            self.assertEqual(row["total"], doc["count"], tid)
            self.assertEqual(row["primary_n"], doc["primary_count"], tid)
            expected = [r for r in self.index if r["in_scope"] and tid in r["topic_ids"]]
            self.assertEqual(row["total"], len(expected), tid)

    def test_views_and_pending(self) -> None:
        daily = self.conn.execute("SELECT * FROM v_daily_candidates").fetchall()
        self.assertEqual(len(daily), support.expected()["in_scope_free_with_statement"])
        for row in daily:
            self.assertEqual(row["in_scope"], 1)
            self.assertEqual(row["paid_only"], 0)
            self.assertEqual(row["has_statement"], 1)
        pending = self.conn.execute(
            "SELECT * FROM v_pending_classification"
        ).fetchall()
        self.assertEqual(len(pending), support.expected()["pending_classification"])
        self.assertTrue(all(r["scope"] == "pending" for r in pending))
        self.assertTrue(all(r["needs_review"] == 1 for r in pending))

    def test_tags_join_matches_index(self) -> None:
        total_tags = sum(len(r["tags"]) for r in self.index)
        joined = self.conn.execute("SELECT COUNT(*) FROM problem_tags").fetchone()[0]
        self.assertEqual(joined, total_tags)
        tags = {r["slug"] for r in self.conn.execute("SELECT slug FROM tags")}
        index_tags = {tag for r in self.index for tag in r["tags"]}
        self.assertEqual(tags, index_tags)
        dangling = self.conn.execute("""
            SELECT COUNT(*) FROM problem_tags pt
            LEFT JOIN problems p ON p.uid=pt.problem_uid
            LEFT JOIN tags t ON t.slug=pt.tag_slug
            WHERE p.uid IS NULL OR t.slug IS NULL
        """).fetchone()[0]
        self.assertEqual(dangling, 0)

    def test_fts_is_populated_and_searchable(self) -> None:
        count = self.conn.execute("SELECT COUNT(*) FROM problems_fts").fetchone()[0]
        self.assertEqual(count, support.expected()["total"])
        rows = self.conn.execute("""
            SELECT p.slug FROM problems_fts f
            JOIN problems p ON p.uid=f.uid
            WHERE problems_fts MATCH 'island'
            LIMIT 10
        """).fetchall()
        self.assertTrue(rows)
        self.assertTrue(any("island" in r["slug"] for r in rows))

    def test_topic_counts_in_topics_table(self) -> None:
        rows = self.conn.execute("""
            SELECT t.id, t.label_zh, COUNT(pt.problem_uid) AS n
            FROM topics t LEFT JOIN problem_topics pt ON pt.topic_id=t.id
            GROUP BY t.id ORDER BY t.sort_order
        """).fetchall()
        self.assertEqual(len(rows), 15)
        for row in rows:
            expected = sum(
                1 for r in self.index if r["in_scope"] and row["id"] in r["topic_ids"]
            )
            self.assertEqual(row["n"], expected, row["id"])
            self.assertTrue(row["label_zh"])

    def test_source_and_license_are_recorded(self) -> None:
        source = self.conn.execute(
            "SELECT * FROM sources WHERE id='leetcode-cn'"
        ).fetchone()
        self.assertIsNotNone(source)
        self.assertIn("personal study", source["license_note"])
        self.assertIn("free", source["statement_policy"].lower())
        meta = {r["key"]: r["value"] for r in self.conn.execute("SELECT * FROM meta")}
        self.assertEqual(meta["schema_version"], "1.0.0")
        self.assertIn(meta["fts_tokenizer"], {"trigram", "unicode61", "unavailable"})


if __name__ == "__main__":
    unittest.main()

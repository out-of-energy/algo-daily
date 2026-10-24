"""Tests for the public query API used by later programs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support
from algo_store import ProblemStore


@support.requires_data
class StoreAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = ProblemStore()
        cls.index = support.load_index()
        cls.by_slug = support.load_index_by_slug()
        cls.manifest = support.load_manifest()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.store.close()

    def test_topic_filter_and_count(self) -> None:
        rows = self.store.list(
            topic="graph", free=True, require_statement=True, limit=1000
        )
        self.assertEqual(len(rows), 308)
        self.assertTrue(all("graph" in r["topic_ids"] for r in rows))
        self.assertEqual(
            self.store.count(topic="graph", free=True, require_statement=True),
            len(rows),
        )

    def test_primary_only(self) -> None:
        rows = self.store.list(topic="dynamic-programming", primary_only=True, limit=1000)
        self.assertTrue(rows)
        self.assertTrue(all(r["primary_topic"] == "dynamic-programming" for r in rows))
        self.assertEqual(self.store.count(topic="dynamic-programming", primary_only=True), 482)

    def test_difficulty_and_free_filters(self) -> None:
        hard = self.store.list(
            topic="binary-search", difficulty="Hard", free=True,
            require_statement=True, limit=1000,
        )
        self.assertTrue(hard)
        self.assertTrue(all(r["difficulty"] == "Hard" for r in hard))
        self.assertTrue(all(r["paid_only"] is False for r in hard))
        self.assertTrue(all(r["has_statement"] for r in hard))
        self.assertEqual(
            self.store.count(
                topic="binary-search", difficulty="Hard", free=True, require_statement=True
            ),
            len(hard),
        )

    def test_language_filter(self) -> None:
        zh = self.store.list(
            lang="zh", free=True, require_statement=True, limit=1000
        )
        self.assertEqual(len(zh), 268)  # 279 Chinese statements - 11 pending/out-of-scope
        self.assertTrue(all(r["statement_lang"] == "zh" for r in zh))
        all_zh = self.store.list(
            lang="zh", include_out_of_scope=True, free=True,
            require_statement=True, limit=1000,
        )
        self.assertEqual(len(all_zh), 279)
        en = self.store.list(lang="en", free=True, limit=1)
        self.assertEqual(en[0]["statement_lang"], "en")

    def test_scope_filter(self) -> None:
        pending = self.store.list(scope="pending", limit=1000)
        self.assertEqual(len(pending), support.expected()["pending_classification"])
        self.assertTrue(all(r["scope"] == "pending" for r in pending))
        js = self.store.list(scope="javascript", limit=1000)
        self.assertEqual(len(js), 46)
        self.assertTrue(all(r["scope"] == "javascript" for r in js))
        # Default query excludes out-of-scope and pending.
        default = self.store.list(limit=1000)
        self.assertTrue(all(r["in_scope"] for r in default))

    def test_pagination_is_stable(self) -> None:
        first = self.store.list(topic="sorting", limit=10, offset=0, order="id")
        second = self.store.list(topic="sorting", limit=10, offset=10, order="id")
        self.assertEqual(len(first), 10)
        self.assertEqual(len(second), 10)
        self.assertFalse({r["uid"] for r in first} & {r["uid"] for r in second})

    def test_random_returns_a_usable_problem(self) -> None:
        for _ in range(15):
            row = self.store.random(topic="dynamic-programming", free=True)
            self.assertIsNotNone(row)
            self.assertIn("dynamic-programming", row["topic_ids"])
            self.assertFalse(row["paid_only"])
            self.assertTrue(row["has_statement"])
            self.assertTrue(row["in_scope"])

    def test_get_free_problem(self) -> None:
        record = self.store.get("two-sum")
        self.assertIsNotNone(record)
        self.assertEqual(record["slug"], "two-sum")
        self.assertEqual(record["statement_lang"], "en")
        self.assertIn("You are given an array", record["statement"]["text"])
        self.assertIn("python3", record["code_snippets"])
        self.assertEqual(record["primary_topic"], "array-string-hash")
        self.assertTrue(record["in_scope"])

    def test_get_chinese_problem(self) -> None:
        record = self.store.get("programmable-robot")
        self.assertIsNotNone(record)
        self.assertEqual(record["statement_lang"], "zh")
        self.assertRegex(record["statement"]["text"], r"[\u4e00-\u9fff]")

    def test_get_paid_problem_has_no_statement(self) -> None:
        record = self.store.get("status-of-flight-tickets")
        self.assertIsNotNone(record)
        self.assertTrue(record["paid_only"])
        self.assertIsNone(record["statement"])
        self.assertFalse(record["has_statement"])
        self.assertIn("official_url", record["note"])

    def test_get_out_of_scope_problem(self) -> None:
        record = self.store.get("combine-two-tables")
        self.assertIsNotNone(record)
        self.assertFalse(record["in_scope"])
        self.assertEqual(record["scope"], "database")
        self.assertEqual(record["topic_ids"], [])
        self.assertEqual(record["topics"], [])

    def test_get_missing_problem(self) -> None:
        self.assertIsNone(self.store.get("definitely-not-a-real-problem"))

    def test_search_chinese_and_english(self) -> None:
        chinese = self.store.list(search="岛屿", limit=50)
        self.assertTrue(any(r["slug"] == "number-of-islands" for r in chinese))
        english = self.store.list(search="Two Sum", limit=50)
        self.assertTrue(any(r["slug"] == "two-sum" for r in english))
        tag = self.store.list(search="monotonic-stack", limit=50)
        self.assertTrue(tag)
        self.assertTrue(any("monotonic-stack" in r["tags"] for r in tag))

    def test_export_jsonl_roundtrip(self) -> None:
        rows = self.store.list(topic="binary-search", free=True, limit=7, order="id")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.jsonl"
            count = self.store.export_jsonl(rows, path)
            self.assertEqual(count, len(rows))
            lines = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(lines, rows)

    def test_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            self.store.list(difficulty="Impossible")
        with self.assertRaises(ValueError):
            self.store.list(order="sideways")
        with self.assertRaises(ValueError):
            self.store.list(limit=0)

    def test_topics_and_stats(self) -> None:
        topics = self.store.topics()
        self.assertEqual(len(topics), 15)
        self.assertEqual({t["id"] for t in topics}, set(support.topic_ids()))
        for topic in topics:
            self.assertEqual(topic["total"], self.store.count(topic=topic["id"]))
            self.assertGreater(topic["total"], 0)
        stats = self.store.stats()
        self.assertEqual(stats["counts"]["total"], self.manifest["counts"]["total"])
        self.assertEqual(
            stats["counts"]["in_scope"], self.manifest["counts"]["in_scope"]
        )
        self.assertEqual(
            stats["counts"]["out_of_scope"],
            self.manifest["counts"]["out_of_scope"],
        )
        self.assertEqual(
            stats["by_statement_language"],
            {"en": support.expected()["statement_en"], "zh": support.expected()["statement_zh"]},
        )

    def test_tags_query(self) -> None:
        tags = self.store.tags(10)
        self.assertEqual(len(tags), 10)
        self.assertTrue(all(t["count"] > 0 for t in tags))
        self.assertEqual(tags, sorted(tags, key=lambda t: (-t["count"], t["slug"])))


if __name__ == "__main__":
    unittest.main()

"""Cross-file integrity tests for the normalized catalog."""

from __future__ import annotations

import json
import re
import unittest
from collections import Counter

import support


@support.requires_data
class CatalogIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = support.load_index()
        cls.manifest = support.load_manifest()
        cls.by_slug = support.load_index_by_slug()
        cls.topic_cfg = support.load_topic_cfg()
        cls.topic_ids = support.topic_ids()

    def test_snapshot_counts(self) -> None:
        counts = self.manifest["counts"]
        actual = {
            "total": len(self.index),
            "free_with_statement": sum(1 for r in self.index if r["has_statement"]),
            "paid_metadata_only": sum(1 for r in self.index if r["paid_only"]),
            "in_scope": sum(1 for r in self.index if r["in_scope"]),
            "in_scope_free_with_statement": sum(
                1 for r in self.index
                if r["in_scope"] and not r["paid_only"] and r["has_statement"]
            ),
            "pending_classification": sum(1 for r in self.index if r["scope"] == "pending"),
            "out_of_scope": sum(
                1 for r in self.index if not r["in_scope"] and r["scope"] != "pending"
            ),
            "statement_en": sum(1 for r in self.index if r["statement_lang"] == "en"),
            "statement_zh": sum(1 for r in self.index if r["statement_lang"] == "zh"),
            "topics": len(self.topic_ids),
        }
        self.assertEqual(actual, support.expected())
        for key, value in actual.items():
            if key.startswith("statement_"):
                continue
            self.assertEqual(counts.get(key), value, f"manifest mismatch: {key}")
        self.assertEqual(counts["statement_languages"], {
            "en": support.expected()["statement_en"],
            "zh": support.expected()["statement_zh"],
        })

    def test_unique_identifiers(self) -> None:
        self.assertEqual(len({r["uid"] for r in self.index}), len(self.index))
        self.assertEqual(len({r["slug"] for r in self.index}), len(self.index))
        self.assertEqual(len({r["frontend_id"] for r in self.index}), len(self.index))
        for record in self.index:
            self.assertEqual(record["uid"], f"{record['source']}:{record['source_id']}")

    def test_required_fields_and_domains(self) -> None:
        required = {
            "schema_version", "uid", "source", "source_id", "frontend_id", "slug",
            "title", "title_zh", "difficulty", "difficulty_level", "paid_only",
            "has_statement", "statement_lang", "in_scope", "scope", "primary_topic",
            "topic_ids", "topic_scores", "curated_topic_ids",
            "classification_confidence", "needs_review", "tags", "official_url",
        }
        for record in self.index:
            uid = record["uid"]
            self.assertTrue(required.issubset(record), uid)
            self.assertRegex(record["slug"], r"^[A-Za-z0-9][A-Za-z0-9-]*$")
            self.assertEqual(record["source"], "leetcode-cn")
            self.assertIn(record["difficulty"], support.ALLOWED_DIFFICULTIES)
            self.assertEqual(
                record["difficulty_level"],
                {"Easy": 1, "Medium": 2, "Hard": 3}[record["difficulty"]],
            )
            self.assertIn(record["scope"], support.ALLOWED_SCOPES)
            self.assertIsInstance(record["paid_only"], bool)
            self.assertIsInstance(record["has_statement"], bool)
            self.assertIsInstance(record["in_scope"], bool)
            self.assertIsInstance(record["needs_review"], bool)
            self.assertEqual(record["needs_review"], record["scope"] == "pending")
            self.assertEqual(record["in_scope"], record["scope"] == "algorithm")
            self.assertEqual(
                record["official_url"],
                f"https://leetcode.cn/problems/{record['slug']}/",
            )
            self.assertTrue(record["title"].strip(), uid)
            self.assertTrue(record["title_zh"].strip(), uid)
            self.assertEqual(record["source_id"], record["frontend_id"], uid)
            self.assertEqual(len(set(record["tags"])), len(record["tags"]))
            if record["paid_only"]:
                self.assertFalse(record["has_statement"], uid)
                self.assertIsNone(record["statement_lang"], uid)
            else:
                self.assertTrue(record["has_statement"], uid)
                self.assertIn(record["statement_lang"], support.ALLOWED_LANGS, uid)
            if record["ac_rate"] is not None:
                self.assertGreaterEqual(record["ac_rate"], 0)
                self.assertLessEqual(record["ac_rate"], 100)

    def test_classification_structure(self) -> None:
        for record in self.index:
            uid = record["uid"]
            topics = record["topic_ids"]
            scores = record["topic_scores"]
            self.assertEqual(len(set(topics)), len(topics), uid)
            self.assertEqual(set(scores), set(topics), uid)
            self.assertTrue(all(score > 0 for score in scores.values()), uid)
            self.assertTrue(set(topics).issubset(set(self.topic_ids)), uid)
            self.assertTrue(set(record["curated_topic_ids"]).issubset(set(topics)), uid)
            if record["in_scope"]:
                self.assertIn(record["primary_topic"], self.topic_ids, uid)
                self.assertEqual(topics[0], record["primary_topic"], uid)
                self.assertEqual(
                    scores[record["primary_topic"]], max(scores.values()), uid
                )
                self.assertIn(
                    record["classification_confidence"], {"high", "medium", "low"}
                )
            else:
                self.assertIsNone(record["primary_topic"], uid)
                self.assertEqual(topics, [], uid)
                self.assertEqual(scores, {}, uid)
                self.assertEqual(record["classification_confidence"], "none", uid)

    def test_pending_and_out_of_scope_exports(self) -> None:
        pending = support.load_pending()
        out = support.load_out_of_scope()
        index_pending = [r for r in self.index if r["scope"] == "pending"]
        index_out = [
            r for r in self.index if not r["in_scope"] and r["scope"] != "pending"
        ]
        self.assertEqual(pending["count"], len(index_pending))
        self.assertEqual(out["count"], len(index_out))
        self.assertEqual(
            {r["uid"] for r in pending["problems"]}, {r["uid"] for r in index_pending}
        )
        self.assertEqual(
            {r["uid"] for r in out["problems"]}, {r["uid"] for r in index_out}
        )
        for record in pending["problems"]:
            self.assertEqual(record["scope"], "pending")
            self.assertTrue(record["needs_review"])
            self.assertEqual(record["topic_ids"], [])
        for record in out["problems"]:
            self.assertNotEqual(record["scope"], "algorithm")
            self.assertNotEqual(record["scope"], "pending")
            self.assertEqual(record["topic_ids"], [])

    def test_topic_exports_match_index(self) -> None:
        topic_dir = support.NORM / "by-topic"
        files = sorted(topic_dir.glob("*.json"))
        self.assertEqual(len(files), support.expected()["topics"])
        self.assertEqual({p.stem for p in files}, set(self.topic_ids))
        for topic_id in self.topic_ids:
            doc = json.loads((topic_dir / f"{topic_id}.json").read_text())
            expected = [r for r in self.index if r["in_scope"] and topic_id in r["topic_ids"]]
            self.assertEqual(doc["count"], len(expected), topic_id)
            self.assertEqual(
                doc["primary_count"], sum(1 for r in expected if r["primary_topic"] == topic_id)
            )
            self.assertEqual(
                doc["free_with_statement_count"],
                sum(1 for r in expected if r["has_statement"]),
            )
            self.assertEqual(
                {r["uid"] for r in doc["problems"]}, {r["uid"] for r in expected}
            )
            self.assertEqual(len({r["uid"] for r in doc["problems"]}), len(expected))
            self.assertEqual(doc["topic"]["id"], topic_id)
            for row in doc["problems"]:
                self.assertIn(topic_id, row["topic_ids"])
                self.assertEqual(row["topic_match"]["is_primary"], row["primary_topic"] == topic_id)

    def test_jsonl_mirrors_json(self) -> None:
        lines = [
            json.loads(line)
            for line in (support.NORM / "index.jsonl").read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(len(lines), len(self.index))
        self.assertEqual({r["uid"] for r in lines}, {r["uid"] for r in self.index})
        self.assertEqual(
            Counter((r["uid"], r["slug"]) for r in lines),
            Counter((r["uid"], r["slug"]) for r in self.index),
        )

    def test_curated_ids_are_real_and_attached(self) -> None:
        by_source = {r["source_id"]: r for r in self.index}
        for category in self.topic_cfg["categories"]:
            self.assertGreaterEqual(len(category["must"]), 10, category["id"])
            for pid in category["must"]:
                record = by_source.get(str(pid))
                self.assertIsNotNone(record, f"{category['id']}: missing curated id {pid}")
                self.assertIn(
                    category["id"], record["topic_ids"],
                    f"{category['id']}: curated id {pid} detached",
                )

    def test_manifest_describes_files_and_license(self) -> None:
        self.assertEqual(self.manifest["schema_version"], "1.0.0")
        for key in (
            "total", "free_with_statement", "paid_metadata_only", "in_scope",
            "in_scope_free_with_statement", "pending_classification",
            "out_of_scope", "topics",
        ):
            self.assertEqual(self.manifest["counts"][key], support.expected()[key])
        self.assertIn("source", self.manifest)
        self.assertIn("license", self.manifest)
        self.assertIn("personal study", self.manifest["license"]["statements"])
        self.assertEqual(self.manifest["files"]["sqlite"], "data/algorithms.db")
        self.assertIn("statement_languages", self.manifest["counts"])


if __name__ == "__main__":
    unittest.main()

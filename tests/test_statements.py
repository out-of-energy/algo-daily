"""Statement quality and index/full-record consistency tests."""

from __future__ import annotations

import re
import sys
import unittest
from collections import Counter

import support

sys.path.insert(0, str(support.ROOT / "scripts"))
from build_dataset import html_to_text  # noqa: E402


@support.requires_data
class StatementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = support.load_index()
        cls.by_slug = support.load_index_by_slug()
        cls.free = [r for r in cls.index if not r["paid_only"]]
        cls.paid = [r for r in cls.index if r["paid_only"]]
        cls.full = list(support.iter_full())
        cls.full_by_slug = {record["slug"]: record for _, record in cls.full}

    def test_full_record_coverage(self) -> None:
        self.assertEqual(len(self.full), len(self.free))
        self.assertEqual(
            {record["slug"] for _, record in self.full},
            {record["slug"] for record in self.free},
        )
        self.assertEqual(
            set(self.full_by_slug),
            {r["slug"] for r in self.free},
        )

    def test_paid_problems_have_no_local_statement(self) -> None:
        paid_slugs = {r["slug"] for r in self.paid}
        self.assertEqual(len(paid_slugs), support.expected()["paid_metadata_only"])
        for slug in paid_slugs:
            self.assertFalse(
                (support.NORM / "problems" / f"{slug}.json").exists(),
                f"paid statement stored for {slug}",
            )
        for _, record in self.full:
            self.assertFalse(record["paid_only"], record["slug"])
            self.assertTrue(record["has_statement"], record["slug"])
            self.assertIsNotNone(record["statement"])

    def test_statement_languages_and_no_placeholders(self) -> None:
        langs = Counter()
        for _, record in self.full:
            statement = record["statement"]
            self.assertIn(statement["lang"], support.ALLOWED_LANGS, record["slug"])
            langs[statement["lang"]] += 1
            self.assertGreaterEqual(len(statement["text"].strip()), 80, record["slug"])
            self.assertNotIn(
                "english description is not available",
                statement["text"].lower(),
                record["slug"],
            )
            self.assertTrue(statement["html"].strip(), record["slug"])
            self.assertEqual(
                record["statement_lang"], statement["lang"], record["slug"]
            )
            self.assertEqual(
                record["has_statement"], bool(statement["text"].strip()), record["slug"]
            )
        self.assertEqual(langs, {
            "en": support.expected()["statement_en"],
            "zh": support.expected()["statement_zh"],
        })

    def test_full_record_matches_index(self) -> None:
        for _, record in self.full:
            slug = record["slug"]
            index_record = self.by_slug[slug]
            for field in (
                "uid", "source", "source_id", "frontend_id", "title", "title_zh",
                "difficulty", "difficulty_level", "paid_only", "has_statement",
                "statement_lang", "in_scope", "scope", "primary_topic",
                "topic_ids", "topic_scores", "curated_topic_ids",
                "classification_confidence", "needs_review", "ac_rate",
                "likes", "dislikes", "official_url",
            ):
                self.assertEqual(record[field], index_record[field], f"{slug}: {field}")
            self.assertEqual(
                record["classification"]["confidence"],
                index_record["classification_confidence"],
                slug,
            )
            self.assertEqual(
                record["classification"]["needs_review"],
                index_record["needs_review"],
                slug,
            )
            self.assertEqual(record["tag_slugs"], index_record["tags"], slug)
            self.assertEqual([t["slug"] for t in record["tags"]], record["tag_slugs"])
            self.assertEqual(
                [t["name"] for t in record["tags"]], record["tag_names"]
            )
            self.assertEqual(
                [t["name_zh"] for t in record["tags"]], record["tag_names_zh"]
            )
            self.assertEqual(
                record["topics"][0]["id"] if record["topics"] else None,
                record["primary_topic"],
            )
            self.assertEqual(
                sum(1 for topic in record["topics"] if topic["is_primary"]),
                1 if record["in_scope"] else 0,
                slug,
            )

    def test_text_is_derived_from_html(self) -> None:
        for _, record in self.full:
            statement = record["statement"]
            self.assertEqual(
                html_to_text(statement["html"]),
                statement["text"],
                f"{record['slug']}: statement.text is stale",
            )
            self.assertNotIn("<script", statement["html"].lower(), record["slug"])
            self.assertNotIn("javascript:", statement["html"].lower(), record["slug"])

    def test_chinese_backfill_samples(self) -> None:
        sample_slugs = [
            "programmable-robot",
            "0Zeoeg",
            "ju-zhen-zhong-de-lu-jing-lcof",
            "qing-wa-tiao-tai-jie-wen-ti-lcof",
        ]
        for slug in sample_slugs:
            record = self.full_by_slug.get(slug)
            self.assertIsNotNone(record, slug)
            self.assertEqual(record["statement"]["lang"], "zh", slug)
            self.assertTrue(record["statement"]["translated"], slug)
            self.assertRegex(record["statement"]["text"], r"[\u4e00-\u9fff]", slug)

    def test_english_statement_sample(self) -> None:
        record = self.full_by_slug["two-sum"]
        self.assertEqual(record["statement"]["lang"], "en")
        self.assertIn("You are given an array of integers", record["statement"]["text"])
        self.assertIn("Example 1", record["statement"]["text"])
        self.assertIn("[2,7,11,15]", record["statement"]["examples"])
        self.assertTrue(record["statement"]["sample_testcase"])

    def test_algorithm_code_snippets(self) -> None:
        allowed = {
            "python3", "java", "cpp", "javascript", "typescript",
            "golang", "rust", "csharp",
        }
        for _, record in self.full:
            if not record["in_scope"]:
                continue
            self.assertTrue(record["code_snippets"], record["slug"])
            for lang, snippet in record["code_snippets"].items():
                self.assertIn(lang, allowed, record["slug"])
                self.assertTrue(snippet["code"].strip(), f"{record['slug']}:{lang}")
                self.assertIsInstance(snippet["lang"], str)

    def test_html_to_text_preserves_code_blocks(self) -> None:
        html = (
            "<p>Hello <strong>world</strong>.</p>"
            "<pre>nums = [1, 2]\nanswer = 3</pre>"
            "<ul><li>one</li><li>two</li></ul>"
        )
        text = html_to_text(html)
        self.assertIn("Hello world.", text)
        self.assertIn("nums = [1, 2]\nanswer = 3", text)
        self.assertIn("- one", text)
        self.assertIn("- two", text)

    def test_full_filenames_match_slugs(self) -> None:
        for path, record in self.full:
            self.assertEqual(path.stem, record["slug"])
            self.assertTrue(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*\.json", path.name))


if __name__ == "__main__":
    unittest.main()

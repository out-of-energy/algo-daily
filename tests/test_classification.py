"""Topic classification regression tests.

The representative fixtures protect the intended semantics:
  * specific tags beat broad array/string tags;
  * pure tree BFS/DFS stays in tree, not graph;
  * hybrid tree+graph problems keep both memberships;
  * unknown problems stay pending instead of being guessed.
"""

from __future__ import annotations

import sys
import unittest

import support

sys.path.insert(0, str(support.ROOT / "scripts"))
from build_dataset import classify  # noqa: E402


@support.requires_data
class ClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = support.load_index()
        cls.by_slug = support.load_index_by_slug()
        cls.cfg = support.load_topic_cfg()

    def test_representative_primary_topics(self) -> None:
        expected = {
            "two-sum": "array-string-hash",
            "longest-substring-without-repeating-characters": "two-pointers-sliding-window",
            "binary-search": "binary-search",
            "valid-parentheses": "stack-queue-monotonic",
            "reverse-linked-list": "linked-list",
            "binary-tree-level-order-traversal": "tree-bst",
            "course-schedule": "graph",
            "last-stone-weight": "heap",
            "permutations": "backtracking",
            "climbing-stairs": "dynamic-programming",
            "jump-game": "greedy",
            "merge-intervals": "sorting",
            "single-number": "bit-manipulation",
            "reverse-integer": "math",
            "lru-cache": "design",
        }
        for slug, topic in expected.items():
            record = self.by_slug[slug]
            self.assertEqual(record["primary_topic"], topic, slug)
            self.assertIn(topic, record["topic_ids"], slug)

    def test_specific_tags_beat_broad_tags(self) -> None:
        # Two Sum has array + hash: broad fallback is correct.
        self.assertEqual(self.by_slug["two-sum"]["topic_ids"], ["array-string-hash"])
        # Sliding window should not be hidden by array/string.
        longest = self.by_slug["longest-substring-without-repeating-characters"]
        self.assertEqual(longest["primary_topic"], "two-pointers-sliding-window")
        self.assertIn("array-string-hash", longest["topic_ids"])
        # DP should win over array.
        stairs = self.by_slug["climbing-stairs"]
        self.assertEqual(stairs["primary_topic"], "dynamic-programming")
        self.assertIn("math", stairs["topic_ids"])
        # Design should be primary even though a linked list is used.
        lru = self.by_slug["lru-cache"]
        self.assertEqual(lru["primary_topic"], "design")
        self.assertIn("linked-list", lru["topic_ids"])

    def test_tree_bfs_is_not_graph(self) -> None:
        level_order = self.by_slug["binary-tree-level-order-traversal"]
        self.assertEqual(level_order["primary_topic"], "tree-bst")
        self.assertNotIn("graph", level_order["topic_ids"])
        # A problem that is genuinely both keeps both.
        hybrid = self.by_slug["validate-binary-tree-nodes"]
        self.assertIn("tree-bst", hybrid["topic_ids"])
        self.assertIn("graph", hybrid["topic_ids"])

    def test_every_topic_has_a_usable_core(self) -> None:
        for topic in self.cfg["categories"]:
            tid = topic["id"]
            members = [r for r in self.index if r["in_scope"] and tid in r["topic_ids"]]
            primary = [r for r in members if r["primary_topic"] == tid]
            self.assertGreaterEqual(len(primary), 40, tid)
            self.assertGreaterEqual(
                sum(1 for r in primary if r["has_statement"]), 20, tid
            )

    def test_pending_holds_unknown_topics_without_guessing(self) -> None:
        pending = [r for r in self.index if r["scope"] == "pending"]
        self.assertEqual(len(pending), support.expected()["pending_classification"])
        for record in pending:
            self.assertFalse(record["in_scope"], record["slug"])
            self.assertTrue(record["needs_review"], record["slug"])
            self.assertEqual(record["topic_ids"], [], record["slug"])
            self.assertIsNone(record["primary_topic"], record["slug"])

    def test_out_of_scope_categories(self) -> None:
        out = [r for r in self.index if not r["in_scope"] and r["scope"] != "pending"]
        scopes = {r["scope"] for r in out}
        self.assertTrue(scopes.issubset({"database", "shell", "concurrency", "javascript", "pandas"}))
        for scope in scopes:
            self.assertGreaterEqual(sum(1 for r in out if r["scope"] == scope), 4, scope)
        for record in out:
            self.assertFalse(record["needs_review"], record["slug"])
            self.assertEqual(record["topic_ids"], [], record["slug"])

    def test_rule_engine_synthetic_cases(self) -> None:
        def make(tags: list[str], pid: int = 999999) -> dict:
            return {
                "id": pid,
                "slug": "synthetic",
                "tags": [{"slug": tag, "name": tag, "name_zh": tag} for tag in tags],
            }

        dp = classify(make(["dynamic-programming", "array"]), self.cfg)
        self.assertEqual(dp["primary_topic"], "dynamic-programming")
        self.assertTrue(dp["in_scope"])

        pure_tree = classify(make(["tree", "depth-first-search", "breadth-first-search"]), self.cfg)
        self.assertEqual(pure_tree["topic_ids"], ["tree-bst"])

        hybrid = classify(make(["tree", "graph", "depth-first-search"]), self.cfg)
        self.assertIn("tree-bst", hybrid["topic_ids"])
        self.assertIn("graph", hybrid["topic_ids"])

        broad = classify(make(["array"]), self.cfg)
        self.assertEqual(broad["primary_topic"], "array-string-hash")
        self.assertEqual(broad["confidence"], "low")

        unknown = classify(make([]), self.cfg)
        self.assertEqual(unknown["scope"], "pending")
        self.assertFalse(unknown["in_scope"])
        self.assertTrue(unknown["needs_review"])

    def test_language_based_scope_detection(self) -> None:
        js_raw = {"code_snippets": [{"lang_slug": "javascript"}]}
        js = classify(
            {"id": 999998, "slug": "counter", "tags": []},
            self.cfg,
            js_raw,
        )
        self.assertEqual(js["scope"], "javascript")

        sql_raw = {"content_html": "<p>Table: users</p><p>Write a query.</p>", "code_snippets": []}
        sql = classify(
            {"id": 999997, "slug": "report", "tags": []},
            self.cfg,
            sql_raw,
        )
        self.assertEqual(sql["scope"], "database")

        pandas_raw = {"content_html": "<p>Given a DataFrame df.</p>", "code_snippets": []}
        pandas = classify(
            {"id": 999996, "slug": "select-data", "tags": []},
            self.cfg,
            pandas_raw,
        )
        self.assertEqual(pandas["scope"], "pandas")


    def test_stored_classification_is_reproducible_from_rules(self) -> None:
        """Every in-scope row must match a fresh run of the classifier.

        This catches hand-edited JSON, stale exports, or config drift.
        """
        for record in self.index:
            if not record["in_scope"]:
                continue
            sid = record["source_id"]
            pid = int(sid) if sid.isdigit() else -1
            synthetic = {
                "id": pid,
                "slug": record["slug"],
                "tags": [
                    {"slug": tag, "name": tag, "name_zh": tag}
                    for tag in record["tags"]
                ],
            }
            expected = classify(synthetic, self.cfg)
            self.assertEqual(expected["topic_ids"], record["topic_ids"], record["slug"])
            self.assertEqual(expected["primary_topic"], record["primary_topic"], record["slug"])
            self.assertEqual(
                {t["id"]: t["score"] for t in expected["topics"]},
                record["topic_scores"],
                record["slug"],
            )
            self.assertEqual(
                expected["curated_topic_ids"], record["curated_topic_ids"], record["slug"]
            )
            self.assertEqual(
                expected["confidence"], record["classification_confidence"], record["slug"]
            )

    def test_confidence_levels(self) -> None:
        self.assertEqual(self.by_slug["course-schedule"]["classification_confidence"], "high")
        self.assertEqual(self.by_slug["two-sum"]["classification_confidence"], "high")
        self.assertEqual(self.by_slug["reverse-integer"]["classification_confidence"], "high")
        for record in self.index:
            if record["in_scope"]:
                self.assertIn(
                    record["classification_confidence"], {"high", "medium", "low"}
                )
            else:
                self.assertEqual(record["classification_confidence"], "none")


if __name__ == "__main__":
    unittest.main()

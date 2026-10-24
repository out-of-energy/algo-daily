"""Tests for the daily generator: AI normalization, HTML, email and selection."""

from __future__ import annotations

import copy
import json
import os
import random
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

import support

sys.path.insert(0, str(support.ROOT / "scripts"))
from daily_app.ai import (  # noqa: E402
    AIClient,
    AnalysisError,
    load_demo_analysis,
    normalize_analysis,
    normalize_visual,
)
from daily_app.config import Settings  # noqa: E402
from daily_app.emailer import Emailer, build_email_html, static_visual  # noqa: E402
from daily_app.history import History  # noqa: E402
from daily_app.render import render_archive, render_page  # noqa: E402
from daily_app.selector import ProblemSelector  # noqa: E402
from daily_app.verify import compare, parse_official, verify_analysis  # noqa: E402



class DailyAppUnitTests(unittest.TestCase):
    """Pure logic (scene DSL, comparison, JSON repair): runs without the question bank."""

    def test_ai_json_fence_and_bracket_repair(self) -> None:
        parsed = AIClient._parse_json("```json\n{\"ok\": true}\n```")
        self.assertEqual(parsed, {"ok": True})
        # The cheap-model slip seen in practice: a frame closed with "]" instead of "}".
        broken = '{"visual":{"frames":[{"caption":"a","state":{"m":{"items":{}}}},' \
                 '{"caption":"b","state":{"m":{"items":{}}}]},"code":{"source":"x"}}'
        repaired = AIClient._parse_json(broken)
        self.assertTrue(repaired.pop("_json_repaired"))
        self.assertEqual(len(repaired["visual"]["frames"]), 2)
        self.assertEqual(repaired["code"]["source"], "x")

    def _scene(self, frames: list, panels: list | None = None) -> dict:
        frames = [dict(f) for f in frames]
        if frames:
            frames[-1].setdefault("tone", "success")
        return {
            "title": "t",
            "panels": panels or [
                {"id": "a", "kind": "array", "values": [5, 10, 20]},
                {"id": "m", "kind": "map", "items": {"five": 0}},
            ],
            "frames": frames,
        }

    def test_scene_state_inheritance(self) -> None:
        visual = normalize_visual(self._scene([
            {"caption": "init", "state": {"a": {"pointers": {"i": 0}, "done": [0], "active": [0]}}},
            {"caption": "two", "state": {"a": {"set": [[1, "X"]]}, "m": {"items": {"five": 1}}}},
            {"caption": "three", "state": {"a": {"window": [0, 1], "done": []}}},
        ]))
        f0, f1, f2 = (f["state"] for f in visual["frames"])
        self.assertEqual(f0["a"]["active"], [0])
        self.assertEqual(f1["a"]["active"], [], "transient fields reset")
        self.assertEqual(f1["a"]["done"], [0], "persistent fields carry over")
        self.assertEqual(f1["a"]["pointers"], {"i": 0})
        self.assertEqual(f1["a"]["values"], ["5", "X", "20"])
        self.assertEqual(f2["a"]["values"], ["5", "X", "20"], "set patches persist")
        self.assertEqual(f2["a"]["done"], [])
        self.assertEqual(f2["a"]["window"], [0, 1])
        self.assertEqual(f2["m"]["items"], {"five": "1"})

    def test_scene_strict_errors_are_specific(self) -> None:
        bad_cases = [
            ([{"caption": "a", "state": {"a": {"pointers": {"i": 9}}}}], "越界"),
            ([{"caption": "a", "state": {"zz": {}}}], "不存在"),
            ([{"caption": "a"}, {"caption": "a"}], "caption 完全相同"),
            ([{"caption": "a", "state": {"m": {"values": [1]}}}], "不支持帧字段"),
        ]
        for frames, needle in bad_cases:
            frames = frames + [{"caption": f"pad{i}"} for i in range(3)]
            with self.assertRaises(AnalysisError) as ctx:
                normalize_visual(self._scene(frames))
            self.assertIn(needle, str(ctx.exception))
        with self.assertRaises(AnalysisError):
            normalize_visual(self._scene([{"caption": "x"}, {"caption": "y"}]))
        soft = normalize_visual(self._scene([
            {"caption": "a", "state": {"m": {"active": ["ten"]}, "a": {"bad": [9]}}},
            {"caption": "b"}, {"caption": "c"},
        ]))
        self.assertEqual(soft["frames"][0]["state"]["m"]["active"], [])
        self.assertEqual(len(soft["warnings"]), 2, "bad highlights are dropped, not fatal")

    def test_scene_code_line_snippets(self) -> None:
        code = "class Solution:\n    def f(self):\n        five = 0\n        return five"
        visual = normalize_visual(self._scene([
            {"caption": "a", "line": "five = 0"},
            {"caption": "b", "line": "return five"},
            {"caption": "c", "line": ["five = 0", "return five"]},
        ]), code)
        self.assertEqual([f["lines"] for f in visual["frames"]], [[3], [4], [3, 4]])
        for bad_line, needle in ((4, "不能写行号"), ("4", "不能写行号")):
            with self.assertRaises(AnalysisError) as ctx:
                normalize_visual(self._scene([
                    {"caption": "a", "line": bad_line},
                    {"caption": "b", "line": "five = 0"},
                    {"caption": "c", "line": "five = 0"},
                ]), code)
            self.assertIn(needle, str(ctx.exception))
        with self.assertRaises(AnalysisError) as ctx:
            normalize_visual({**self._scene([
                {"caption": "a"}, {"caption": "b"}, {"caption": "c"},
            ]), "frames": [{"caption": "a"}, {"caption": "b"}, {"caption": "c"}]})
        self.assertIn("最后一帧", str(ctx.exception))
        with self.assertRaises(AnalysisError) as ctx:
            normalize_visual(self._scene([
                {"caption": "a", "line": "five"},
                {"caption": "b", "line": "return five"},
                {"caption": "c", "line": "return five"},
            ]), code)
        self.assertIn("恰好 1 次", str(ctx.exception))

    def test_scene_tree_layout_and_linked_list(self) -> None:
        visual = normalize_visual(self._scene(
            [
                {"caption": "a", "state": {"t": {"active": ["1"]}, "l": {"pointers": {"prev": None, "cur": 0}}}},
                {"caption": "b", "state": {"t": {"active_edges": [["1", "3"]]}, "l": {"next": [None, 2, None]}}},
                {"caption": "c", "state": {"t": {"notes": {"2": "d=1"}}}},
            ],
            panels=[
                {"id": "t", "kind": "tree", "nodes": ["1", "2", "3", "4"],
                 "edges": [["1", "2", "L"], ["1", "3", "R"], ["2", "4", "R"]]},
                {"id": "l", "kind": "linked_list", "nodes": ["1", "2", "3"]},
            ],
        ))
        tree = visual["panels"][0]
        pos = {n["id"]: (n["x"], n["y"]) for n in tree["nodes"]}
        self.assertEqual(tree["layout"], "tree")
        self.assertLess(pos["1"][1], pos["2"][1])
        self.assertLess(pos["2"][0], pos["1"][0])
        self.assertLess(pos["1"][0], pos["3"][0])
        self.assertLess(pos["2"][0], pos["4"][0])
        frames = visual["frames"]
        self.assertEqual(frames[0]["state"]["l"]["next"], [1, 2, None])
        self.assertEqual(frames[0]["state"]["l"]["pointers"], {"prev": None, "cur": 0})
        self.assertEqual(frames[1]["state"]["l"]["next"], [None, 2, None])
        self.assertEqual(frames[1]["state"]["t"]["active_edges"], [1])
        self.assertEqual(frames[2]["state"]["t"]["active_edges"], [])

    def test_legacy_visual_upgrades_for_old_pages(self) -> None:
        legacy = {
            "type": "array", "title": "old",
            "data": {"values": [5, 10], "labels": ["a", "b"]},
            "frames": [
                {"caption": "a", "pointers": {"i": 0}, "map": {"five": "1"}},
                {"caption": "b", "pointers": {"i": 7}, "map": {"five": "0"}},
            ],
        }
        visual = normalize_visual(legacy)
        self.assertEqual([p["kind"] for p in visual["panels"]], ["array", "map"])
        self.assertEqual(visual["panels"][0]["labels"], ["a", "b"])
        self.assertEqual(visual["frames"][1]["state"]["arr"]["pointers"], {}, "lenient drop")

    def test_visual_normalization_rejects_too_few_frames(self) -> None:
        with self.assertRaises(AnalysisError):
            normalize_visual({
                "type": "array",
                "data": {"values": [1, 2]},
                "frames": [{"caption": "only one"}],
            })

    def test_compare_modes(self) -> None:
        self.assertTrue(compare([[1, 2], [3]], [[3], [1, 2]], "unordered"))
        self.assertFalse(compare([[1, 2], [3]], [[3], [2, 1]], "unordered"))
        self.assertTrue(compare([[1, 2], [3]], [[3], [2, 1]], "unordered_deep"))
        self.assertTrue(compare(0.3333333, 1 / 3, "float"))
        self.assertTrue(compare([0, 1], [1, 0], "exact", order_free=True))
        self.assertFalse(compare([0, 1], [1, 0], "exact"))
        self.assertTrue(compare(1, 2, "run_only"))


@support.requires_data
class DailyAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = Settings()
        cls.selector = ProblemSelector(
            cls.settings, db_path=support.ROOT / "data" / "algorithms.db"
        )
        cls.problem = cls.selector.load_problem(
            "longest-substring-without-repeating-characters"
        )
        cls.problem["topic_label"] = cls.settings.topic_label(
            cls.problem.get("primary_topic")
        )
        cls.analysis = load_demo_analysis(cls.problem, model="demo-fixture")
        cls.day = date(2026, 10, 24)
        cls.page = render_page(cls.settings, cls.problem, cls.analysis, cls.day, demo=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.selector.close()

    def test_demo_analysis_is_normalized(self) -> None:
        self.assertEqual(self.analysis["problem"]["slug"], self.problem["slug"])
        visual = self.analysis["visual"]
        self.assertEqual(visual["version"], 2)
        self.assertEqual([p["kind"] for p in visual["panels"]], ["array", "map", "map"])
        self.assertGreaterEqual(len(visual["frames"]), 6)
        self.assertTrue(all(f["lines"] for f in visual["frames"]))
        self.assertIn("last[ch] >= left", self.analysis["code"]["source"])
        self.assertTrue(self.analysis["checks"])
        self.assertEqual(self.analysis["meta"]["model"], "demo-fixture")
        self.assertTrue(self.analysis["sources"][0]["url"].startswith("https://leetcode.cn/"))

    def test_ai_json_rejects_garbage_and_bad_visual_type(self) -> None:
        with self.assertRaises(AnalysisError):
            AIClient._parse_json("not json at all")
        raw = copy.deepcopy(self.analysis)
        raw["visual"] = {"type": "not-a-type", "data": {}, "frames": [{"caption": "a"}, {"caption": "b"}]}
        with self.assertRaises(AnalysisError):
            normalize_visual(raw["visual"])

    def test_all_visual_types_normalize_and_render(self) -> None:
        variants = {
            "grid": {
                "type": "grid", "title": "grid",
                "data": {"grid": [["1", "0"], ["0", "1"]]},
                "frames": [
                    {"caption": "a", "active": [[0, 0]]},
                    {"caption": "b", "visited": [[1, 1]]},
                ],
            },
            "linked_list": {
                "type": "linked_list", "title": "list",
                "data": {"nodes": ["1", "2", "3"]},
                "frames": [
                    {"caption": "a", "pointers": {"cur": 0}, "active": [0]},
                    {"caption": "b", "pointers": {"prev": 0, "cur": 1}, "reversed": [0]},
                ],
            },
            "graph": {
                "type": "graph", "title": "graph",
                "data": {
                    "nodes": [{"id": "A"}, {"id": "B"}],
                    "edges": [["A", "B"]],
                    "directed": True,
                },
                "frames": [
                    {"caption": "a", "active_nodes": ["A"]},
                    {"caption": "b", "visited": ["A"], "active_edges": [["A", "B"]]},
                ],
            },
            "table": {
                "type": "table", "title": "table",
                "data": {"headers": ["i", "0"], "rows": [["dp", "0"], ["dp", "1"]]},
                "frames": [
                    {"caption": "a", "active_cells": [[0, 0]]},
                    {"caption": "b", "done_cells": [[0, 0]], "active_cells": [[1, 1]]},
                ],
            },
            "flow": {
                "type": "flow", "title": "flow",
                "data": {"stages": [{"title": "A", "detail": "a"}, {"title": "B", "detail": "b"}]},
                "frames": [
                    {"caption": "a", "active_stage": 0},
                    {"caption": "b", "active_stage": 1},
                ],
            },
            "stack": {
                "type": "stack", "title": "stack",
                "data": {"input": ["1", "2"]},
                "frames": [
                    {"caption": "a", "stack": ["1"], "queue": ["2"], "active": "1"},
                    {"caption": "b", "stack": ["1", "2"], "queue": [], "active": "2"},
                ],
            },
        }
        expected_kind = {"flow": "cards"}
        for kind, raw in variants.items():
            normalized = normalize_visual(raw)
            kinds = [p["kind"] for p in normalized["panels"]]
            self.assertIn(expected_kind.get(kind, kind), kinds)
            analysis = copy.deepcopy(self.analysis)
            analysis["visual"] = normalized
            page = render_page(self.settings, self.problem, analysis, self.day, demo=True)
            self.assertIn('id="visual-data"', page)
            self.assertIn("visual-stage", page)
            self.assertIsInstance(static_visual(analysis), str)

    def test_analysis_requires_code_and_meaning(self) -> None:
        raw = json.loads(json.dumps({
            "hook": "x",
            "real_meaning": {"plain": "人话"},
            "approach": {"steps": ["a"], "complexity": {}},
            "visual": {
                "type": "array", "data": {"values": [1]},
                "frames": [{"caption": "a"}, {"caption": "b"}],
            },
            "code": {"source": "class Solution: pass"},
            "checks": [],
            "takeaway": "x",
        }))
        result = normalize_analysis(raw, self.problem, "双指针", "test-model")
        self.assertEqual(result["code"]["language"], "Python 3")
        self.assertEqual(result["approach"]["complexity"]["time"], "未给出")
        raw["code"]["source"] = ""
        with self.assertRaises(AnalysisError):
            normalize_analysis(raw, self.problem, "双指针", "test-model")

    def test_page_contains_interactive_visual_and_code(self) -> None:
        self.assertIn("id=\"visual-data\"", self.page)
        self.assertIn("id=\"visual-stage\"", self.page)
        self.assertIn("id=\"solution-code\"", self.page)
        self.assertIn("窗口扩张与收缩", self.page)
        self.assertIn("无重复字符的最长子串", self.page)
        self.assertIn("示例", self.page)

    def test_page_escapes_model_text(self) -> None:
        dangerous = copy.deepcopy(self.analysis)
        dangerous["hook"] = "</script><script>alert(1)</script>"
        dangerous["real_meaning"]["plain"] = "<img src=x onerror=alert(1)>"
        page = render_page(self.settings, self.problem, dangerous, self.day, demo=True)
        self.assertNotIn("alert(1)</script>", page)
        self.assertIn("&lt;/script&gt;", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)

    def test_page_visual_json_is_parseable(self) -> None:
        marker = '<script id="visual-data" type="application/json">'
        start = self.page.index(marker) + len(marker)
        end = self.page.index("</script>", start)
        data = json.loads(self.page[start:end].replace("<\\/", "</"))
        self.assertEqual(data["version"], 2)
        self.assertEqual(len(data["frames"]), 8)
        self.assertEqual(data["frames"][-1]["state"]["vars"]["items"]["best"], "5")

    def test_archive_renders_entries(self) -> None:
        html = render_archive(self.settings, [{
            "date": "2026-10-24",
            "title_zh": "无重复字符的最长子串",
            "topic": "two-pointers-sliding-window",
            "topic_label": "双指针 · 滑动窗口",
            "difficulty": "Medium",
        }])
        self.assertIn("无重复字符的最长子串", html)
        self.assertIn("2026-10-24", html)
        self.assertIn("双指针", html)
        self.assertNotIn("<script", html)

    def test_static_email_visual_and_html(self) -> None:
        visual = static_visual(self.analysis)
        self.assertIn("关键一步", visual)
        self.assertIn("left", visual)
        self.assertIn("last[ch] + 1", self.analysis["code"]["source"])
        email = build_email_html(
            self.analysis, self.problem, self.day, public_url="", demo=True
        )
        self.assertIn("无重复字符的最长子串", email)
        self.assertIn("完整可交互页面在附件", email)
        self.assertIn("示例预览", email)
        self.assertNotIn("<script", email.lower())

    def test_email_dry_run_without_credentials(self) -> None:
        clean = {
            "SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASS": "",
            "SMTP_PASSWORD": "", "SMTP_PASSWORD_FILE": "",
            "MAIL_FROM": "", "MAIL_TO": "", "LOVE_MAIL_TO": "",
            "SMTP_FROM": "", "MAIL_DRY_RUN": "1",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, clean, clear=False):
            settings = Settings()
            settings.root = Path(tmp)  # type: ignore[assignment]
            emailer = Emailer(settings)
            result = emailer.send(
                self.analysis,
                self.problem,
                self.page,
                self.day,
                dry_run=True,
                demo=True,
            )
            self.assertEqual(result["status"], "dry-run")
            path = Path(result["path"])
            self.assertTrue(path.exists())
            self.assertIn("无重复字符的最长子串", result["subject"])
            self.assertEqual(result["to"], ["demo@localhost"])

            msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            self.assertIn("无重复字符的最长子串", msg["Subject"])
            parts = list(msg.walk())
            self.assertTrue(any(
                p.get_content_type() == "text/html" and not p.get_filename()
                for p in parts
            ))
            attachments = [p for p in parts if p.get_filename()]
            self.assertEqual(len(attachments), 1)
            self.assertTrue(attachments[0].get_filename().endswith(".html"))
            self.assertIn("id=\"visual-data\"", attachments[0].get_content())

    def test_email_dry_run_requires_no_smtp(self) -> None:
        clean = {
            "SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASS": "",
            "SMTP_PASSWORD": "", "SMTP_PASSWORD_FILE": "",
            "MAIL_FROM": "", "MAIL_TO": "", "LOVE_MAIL_TO": "",
            "SMTP_FROM": "", "MAIL_DRY_RUN": "1",
        }
        with patch.dict(os.environ, clean, clear=False):
            self.assertFalse(Settings().smtp_configured)
            self.assertTrue(Settings().mail_dry_run)

    def test_selector_returns_valid_daily_problem(self) -> None:
        pick = self.selector.pick(date(2026, 1, 1))
        problem = self.selector.load_problem(pick["slug"])
        self.assertTrue(problem["in_scope"])
        self.assertFalse(problem["paid_only"])
        self.assertTrue(problem["statement"]["text"])
        self.assertIn(pick["topic_id"], problem["topic_ids"])
        self.assertEqual(problem["primary_topic"], pick["topic_id"])

    def test_selector_avoids_recent_and_balances_topics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp) / "history.json")
            original = self.selector.history
            self.selector.history = history
            try:
                first = self.selector.pick(date(2026, 1, 1))
                history.upsert({
                    "date": "2026-01-01",
                    "slug": first["slug"],
                    "topic": first["topic_id"],
                })
                second = self.selector.pick(date(2026, 1, 2))
                self.assertNotEqual(second["topic_id"], first["topic_id"])
                self.assertNotEqual(second["slug"], first["slug"])

                repeated = self.selector.pick(date(2026, 1, 2), topic=first["topic_id"])
                self.assertNotEqual(repeated["slug"], first["slug"])
            finally:
                self.selector.history = original

    def test_thirty_day_topic_coverage_is_guaranteed(self) -> None:
        """One pick/day must cover all 15 topics twice in a 30-day window."""
        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp) / "history.json")
            original = self.selector.history
            state = random.getstate()
            self.selector.history = history
            try:
                random.seed(20261024)
                chosen = []
                slugs = []
                start = date(2026, 2, 1)
                for offset in range(30):
                    day = start + timedelta(days=offset)
                    pick = self.selector.pick(day)
                    self.assertTrue(pick["selection"]["coverage_window_days"] == 30)
                    self.assertIn("topic_count_in_window", pick["selection"])
                    self.assertNotIn(pick["slug"], slugs)
                    slugs.append(pick["slug"])
                    chosen.append(pick["topic_id"])
                    history.upsert({
                        "date": day.isoformat(),
                        "slug": pick["slug"],
                        "topic": pick["topic_id"],
                        "coverage_topic": pick["selection"]["coverage_topic"],
                        "title_zh": pick["title_zh"],
                    })
                counts = Counter(chosen)
                self.assertEqual(set(counts), set(support.topic_ids()))
                self.assertEqual(set(counts.values()), {2})
                self.assertEqual(len(slugs), len(set(slugs)))
            finally:
                self.selector.history = original
                random.setstate(state)

    def test_selector_slug_override(self) -> None:
        pick = self.selector.pick(date(2026, 1, 1), slug="two-sum")
        self.assertEqual(pick["slug"], "two-sum")

    def test_file_based_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "deepseek-key"
            pass_file = Path(tmp) / "smtp-password"
            key_file.write_text("sk-test-file\n")
            pass_file.write_text("smtp-auth-file\n")
            env = {
                "AI_API_KEY": "", "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": "",
                "AI_API_KEY_FILE": str(key_file),
                "SMTP_HOST": "smtp.qq.com", "SMTP_USER": "test@example.com",
                "SMTP_PASS": "", "SMTP_PASSWORD": "",
                "SMTP_PASSWORD_FILE": str(pass_file),
                "SMTP_FROM": "test@example.com",
                "MAIL_TO": "test@example.com", "LOVE_MAIL_TO": "",
            }
            with patch.dict(os.environ, env, clear=False):
                settings = Settings()
                self.assertEqual(settings.ai_api_key, "sk-test-file")
                self.assertEqual(settings.smtp_password, "smtp-auth-file")
                self.assertTrue(settings.ai_configured)
                self.assertTrue(settings.smtp_configured)
                self.assertIn("file:", settings.ai_key_source)
                self.assertIn("file:", settings.smtp_password_source)
                self.assertNotIn("sk-test-file", json.dumps(settings.redacted()))

    def test_last_used_by_topic_uses_most_recent_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp) / "history.json")
            history.upsert({"date": "2026-01-01", "slug": "a", "topic": "graph"})
            history.upsert({"date": "2026-01-10", "slug": "b", "topic": "graph"})
            history.upsert({"slug": "no-date", "topic": "graph"})
            self.assertEqual(history.last_used_by_topic(date(2026, 1, 12))["graph"], 2)

    def test_history_counts_coverage_topic_not_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp) / "history.json")
            history.upsert({
                "date": "2026-01-01", "slug": "a",
                "topic": "array-string-hash", "coverage_topic": "graph",
            })
            counts = history.topic_counts(date(2026, 1, 2), 30)
            self.assertEqual(counts.get("graph"), 1)
            self.assertNotIn("array-string-hash", counts)
            self.assertEqual(history.last_used_by_topic(date(2026, 1, 2)), {"graph": 1})

    def test_strict_rotation_over_120_days(self) -> None:
        topics = support.topic_ids()
        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp) / "history.json")
            original = self.selector.history
            state = random.getstate()
            self.selector.history = history
            try:
                random.seed(20261025)
                start = date(2026, 3, 1)
                chosen: list[str] = []
                slugs: list[str] = []
                for offset in range(120):
                    day = start + timedelta(days=offset)
                    pick = self.selector.pick(day)
                    selection = pick["selection"]
                    self.assertTrue(selection["coverage_satisfied"])
                    chosen.append(selection["coverage_topic"])
                    slugs.append(pick["slug"])
                    history.upsert({
                        "date": day.isoformat(),
                        "slug": pick["slug"],
                        "topic": pick["topic_id"],
                        "coverage_topic": selection["coverage_topic"],
                    })
                self.assertEqual(len(slugs), len(set(slugs)))
                for i in range(1, len(chosen)):
                    self.assertNotEqual(chosen[i], chosen[i - 1])
                for i in range(len(chosen) - 14):
                    self.assertEqual(set(chosen[i:i + 15]), set(topics))
                for i in range(len(chosen) - 29):
                    self.assertEqual(Counter(chosen[i:i + 30]), Counter({t: 2 for t in topics}))
            finally:
                self.selector.history = original
                random.setstate(state)

    def test_secondary_membership_still_credits_target_topic(self) -> None:
        original = self.selector._candidates

        def secondary_only(topic, difficulty, exclude, primary_only, curated_only, limit=200):
            if primary_only:
                return []
            return original(topic, difficulty, exclude, primary_only, curated_only, limit)

        with patch.object(self.selector, "_candidates", side_effect=secondary_only):
            pick = self.selector.pick(date(2026, 1, 1), topic="graph")
        selection = pick["selection"]
        self.assertEqual(selection["coverage_topic"], "graph")
        self.assertTrue(selection["coverage_satisfied"])
        self.assertFalse(selection["primary_match"])

    def test_any_topic_fallback_is_marked_unsatisfied(self) -> None:
        original = self.selector._candidates

        def only_any_topic(topic, difficulty, exclude, primary_only, curated_only, limit=200):
            if topic is not None:
                return []
            return original(topic, difficulty, exclude, primary_only, curated_only, limit)

        with patch.object(self.selector, "_candidates", side_effect=only_any_topic):
            pick = self.selector.pick(date(2026, 1, 1))
        selection = pick["selection"]
        self.assertEqual(selection["topic"], "any")
        self.assertIsNotNone(selection["target_topic"])
        self.assertFalse(selection["coverage_satisfied"])
        self.assertEqual(selection["coverage_topic"], pick["topic_id"])

    def test_difficulty_mix_is_soft_and_keeps_rotation(self) -> None:
        topics = support.topic_ids()
        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp) / "history.json")
            original = self.selector.history
            state = random.getstate()
            self.selector.history = history
            try:
                random.seed(20261026)
                start = date(2026, 4, 1)
                chosen: list[str] = []
                difficulties: list[str] = []
                for offset in range(28):
                    day = start + timedelta(days=offset)
                    pick = self.selector.pick(day)
                    selection = pick["selection"]
                    self.assertTrue(selection["coverage_satisfied"])
                    chosen.append(selection["coverage_topic"])
                    difficulties.append(pick["difficulty"])
                    history.upsert({
                        "date": day.isoformat(),
                        "slug": pick["slug"],
                        "topic": pick["topic_id"],
                        "coverage_topic": selection["coverage_topic"],
                        "difficulty": pick["difficulty"],
                    })
                for i in range(len(chosen) - 14):
                    self.assertEqual(set(chosen[i:i + 15]), set(topics))
                counts = Counter(difficulties)
                self.assertTrue(6 <= counts["Easy"] <= 10, counts)
                self.assertTrue(13 <= counts["Medium"] <= 19, counts)
                self.assertTrue(2 <= counts["Hard"] <= 6, counts)
                for a, b in zip(difficulties, difficulties[1:]):
                    self.assertFalse(a == b == "Hard", difficulties)
            finally:
                self.selector.history = original
                random.setstate(state)

    def test_difficulty_relaxes_without_changing_topic(self) -> None:
        original = self.selector._candidates

        def no_hard_in_design(topic, difficulty, exclude, primary_only, curated_only, limit=200):
            if topic == "design" and difficulty is not None:
                return []
            return original(topic, difficulty, exclude, primary_only, curated_only, limit)

        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp) / "history.json")
            prior = self.selector.history
            self.selector.history = history
            try:
                for topic_id in support.topic_ids():
                    if topic_id != "design":
                        history.upsert({
                            "date": "2026-05-01", "slug": f"x-{topic_id}",
                            "topic": topic_id, "coverage_topic": topic_id,
                        })
                with patch.object(self.selector, "_candidates", side_effect=no_hard_in_design):
                    pick = self.selector.pick(date(2026, 5, 2))
            finally:
                self.selector.history = prior
        selection = pick["selection"]
        self.assertEqual(selection["coverage_topic"], "design")
        self.assertTrue(selection["coverage_satisfied"])
        self.assertTrue(selection["difficulty_relaxed"])

    def _demo_raw(self) -> dict:
        return json.loads((support.ROOT / "scripts" / "daily_app" / "demo_analysis.json").read_text())

    def test_verify_demo_passes_official_and_model_tests(self) -> None:
        report = verify_analysis(self._demo_raw(), self.problem)
        self.assertEqual(report["status"], "passed")
        self.assertGreaterEqual(report["official_with_expected"], 3)
        self.assertEqual(report["tests"], 3)
        self.assertTrue(report["visual"])

    def test_verify_rejects_buggy_code_with_actionable_message(self) -> None:
        raw = self._demo_raw()
        raw["code"]["source"] = raw["code"]["source"].replace(" and last[ch] >= left", "")
        with self.assertRaises(AnalysisError) as ctx:
            verify_analysis(raw, self.problem)
        message = str(ctx.exception)
        self.assertIn("abba", message)
        self.assertIn("代码实际输出", message)

    def test_verify_rejects_animation_with_wrong_result(self) -> None:
        raw = self._demo_raw()
        raw["visual"]["test"]["expected"] = 4
        with self.assertRaises(AnalysisError) as ctx:
            verify_analysis(raw, self.problem)
        self.assertIn("动画例子不一致", str(ctx.exception))
        raw = self._demo_raw()
        raw["visual"]["frames"][-1]["badge"] = "结束"
        raw["visual"]["frames"][-1]["caption"] = "扫描结束。"
        with self.assertRaises(AnalysisError) as ctx:
            verify_analysis(raw, self.problem)
        self.assertIn("最后一帧没有写出返回值", str(ctx.exception))

    def test_verify_requires_tests_and_visual_test(self) -> None:
        raw = self._demo_raw()
        raw["tests"] = raw["tests"][:1]
        with self.assertRaises(AnalysisError):
            verify_analysis(raw, self.problem)
        raw = self._demo_raw()
        del raw["visual"]["test"]
        with self.assertRaises(AnalysisError):
            verify_analysis(raw, self.problem)
        raw = self._demo_raw()
        raw["tests"][0]["input"] = {"str": "x"}
        with self.assertRaises(AnalysisError) as ctx:
            verify_analysis(raw, self.problem)
        self.assertIn("['s']", str(ctx.exception))

    def test_verify_stops_infinite_loops(self) -> None:
        raw = self._demo_raw()
        raw["code"]["source"] = (
            "class Solution:\n    def lengthOfLongestSubstring(self, s):\n        while True:\n            pass\n"
        )
        with self.assertRaises(AnalysisError) as ctx:
            verify_analysis(raw, self.problem, {"case_timeout_seconds": 0.5, "timeout_seconds": 6})
        self.assertIn("超时", str(ctx.exception))

    def test_verify_design_problem_catches_real_bug(self) -> None:
        problem = self.selector.load_problem("data-stream-as-disjoint-intervals")
        cases = parse_official(problem)
        self.assertEqual(cases[0]["expected"][-1], [[1, 3], [6, 7]])
        code = (
            "from bisect import bisect_left\n"
            "class SummaryRanges:\n"
            "    def __init__(self):\n        self.iv = []\n"
            "    def addNum(self, value):\n"
            "        i = bisect_left(self.iv, [value, value])\n"
            "        if i < len(self.iv) and self.iv[i][0] == value:\n            return\n"
            "        left = i > 0 and self.iv[i - 1][1] + 1 == value\n"
            "        right = i < len(self.iv) and self.iv[i][0] - 1 == value\n"
            "        if left and right:\n"
            "            self.iv[i - 1][1] = self.iv[i][1]\n            self.iv.pop(i)\n"
            "        elif left:\n            self.iv[i - 1][1] = value\n"
            "        elif right:\n            self.iv[i][0] = value\n"
            "        else:\n            self.iv.insert(i, [value, value])\n"
            "    def getIntervals(self):\n        return [x[:] for x in self.iv]\n"
        )
        ops = ["SummaryRanges", "addNum", "addNum", "addNum", "addNum", "getIntervals"]
        raw = {
            "code": {"source": code},
            "tests": [
                {"input": {"ops": ops, "args": [[], [1], [3], [2], [2], []]},
                 "expected": [None, None, None, None, None, [[1, 3]]]},
                {"input": {"ops": ["SummaryRanges", "getIntervals"], "args": [[], []]}, "expected": [None, []]},
            ],
            "visual": {"test": {"input": {"ops": ["SummaryRanges", "addNum", "getIntervals"], "args": [[], [5], []]},
                                "expected": [None, None, [[5, 5]]]}, "frames": []},
        }
        with self.assertRaises(AnalysisError) as ctx:
            verify_analysis(raw, problem)
        self.assertIn("[[1,3],[2,2]]", str(ctx.exception))
        fixed = code.replace("if i < len(self.iv) and self.iv[i][0] == value:",
                             "if (i < len(self.iv) and self.iv[i][0] == value) or (i > 0 and self.iv[i - 1][1] >= value):")
        raw["code"]["source"] = fixed
        self.assertEqual(verify_analysis(raw, problem)["status"], "passed")

    def test_verify_linked_list_and_in_place_problems(self) -> None:
        problem = self.selector.load_problem("reverse-linked-list")
        raw = {
            "code": {"source": (
                "class Solution:\n    def reverseList(self, head):\n        prev = None\n"
                "        while head:\n            head.next, prev, head = prev, head, head.next\n        return prev\n")},
            "tests": [{"input": {"head": []}, "expected": []}, {"input": {"head": [7]}, "expected": [7]}],
            "visual": {"test": {"input": {"head": [1, 2, 3]}, "expected": [3, 2, 1]},
                       "frames": [{"caption": "done", "badge": "return [3,2,1]"}]},
        }
        self.assertEqual(verify_analysis(raw, problem)["status"], "passed")
        problem = self.selector.load_problem("rotate-array")
        raw = {
            "code": {"source": (
                "class Solution:\n    def rotate(self, nums, k):\n        k %= len(nums)\n"
                "        nums[:] = nums[-k:] + nums[:-k]\n")},
            "tests": [{"input": {"nums": [1, 2], "k": 3}, "expected": [2, 1]},
                      {"input": {"nums": [1], "k": 0}, "expected": [1]}],
            "visual": {"test": {"input": {"nums": [1, 2, 3], "k": 1}, "expected": [3, 1, 2]},
                       "frames": [{"caption": "nums 变成 [3,1,2]"}]},
        }
        self.assertEqual(verify_analysis(raw, problem)["status"], "passed")

    def test_selector_pick_respects_exclude(self) -> None:
        first = self.selector.pick(date(2026, 6, 1), topic="design")
        second = self.selector.pick(date(2026, 6, 1), topic="design", exclude={first["slug"]})
        self.assertNotEqual(first["slug"], second["slug"])

    def test_page_shows_verification_status(self) -> None:
        analysis = copy.deepcopy(self.analysis)
        analysis["verification"] = verify_analysis(self._demo_raw(), self.problem)
        page = render_page(self.settings, self.problem, analysis, self.day, demo=True)
        self.assertIn("代码已在沙箱中实际运行", page)
        analysis["verification"] = {"status": "skipped", "reason": "题目缺少 meta_data"}
        page = render_page(self.settings, self.problem, analysis, self.day, demo=True)
        self.assertIn("未自动验证", page)

    def test_settings_do_not_expose_secrets(self) -> None:
        redacted = self.settings.redacted()
        self.assertNotIn("AI_API_KEY", redacted)
        self.assertNotIn("SMTP_PASS", redacted)
        self.assertEqual(redacted["timezone"], "Asia/Shanghai")


if __name__ == "__main__":
    unittest.main()

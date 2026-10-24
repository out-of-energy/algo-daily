"""Offline regressions for delivery, refresh and dataset publication."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import support

sys.path.insert(0, str(support.ROOT / "scripts"))
import bootstrap_data
import build_dataset
import fetch_leetcode
import run_daily
from daily_app.emailer import EmailError
from daily_app.history import History


class DeliveryTests(unittest.TestCase):
    def test_transient_failure_retries_once(self) -> None:
        with patch.object(run_daily, "Emailer") as emailer, patch.object(run_daily.time, "sleep") as sleep:
            emailer.return_value.send.side_effect = [EmailError("temporary"), {"status": "sent"}]
            result = run_daily.send_email(Mock(), {}, {}, "page", date(2026, 5, 2))
        self.assertEqual(result["status"], "sent")
        self.assertEqual(emailer.return_value.send.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_persistent_failure_is_not_suppressed(self) -> None:
        with patch.object(run_daily, "Emailer") as emailer, patch.object(run_daily.time, "sleep"):
            emailer.return_value.send.side_effect = EmailError("offline")
            with self.assertRaisesRegex(EmailError, "offline"):
                run_daily.send_email(Mock(), {}, {}, "page", date(2026, 5, 2))
        self.assertEqual(emailer.return_value.send.call_count, 2)

    def test_existing_delivery_states(self) -> None:
        for status, no_email, dry_run, resend, failure, expected_calls, expected_code in [
            ("failed", False, False, False, False, 1, 0),
            ("failed", False, False, False, True, 1, 1),
            ("failed", True, False, False, False, 0, 0),
            ("failed", False, True, False, False, 1, 0),
            ("sent", False, False, False, False, 0, 0),
            ("sent", False, False, True, False, 1, 0),
            ("dry-run", False, False, False, False, 0, 0),
        ]:
            with self.subTest(status=status, no_email=no_email, failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                day = date(2026, 5, 2)
                analysis_path = root / "data/daily/2026-05-02.json"
                page_path = root / "site/day/2026-05-02.html"
                analysis_path.parent.mkdir(parents=True)
                page_path.parent.mkdir(parents=True)
                page_path.write_text("existing page")
                analysis_path.write_text(json.dumps({
                    "slug": "one", "analysis": {"hook": "unchanged"}, "email": {"status": status},
                }))
                settings = SimpleNamespace(
                    root=root, timezone="UTC", app_name="test", topic_label=lambda topic: topic,
                    public_day_url=lambda day: "",
                )
                args = argparse.Namespace(
                    force=False, reroll=False, resend=resend, send=False,
                    no_email=no_email, dry_run=dry_run, json=False,
                )
                selector = Mock()
                selector.load_problem.return_value = {"slug": "one", "primary_topic": "array"}
                history = History(analysis_path.parent / "history.json")
                with patch.object(run_daily, "send_email") as send, patch.object(run_daily, "AIClient") as ai:
                    send.return_value = {"status": "dry-run" if dry_run else "sent"}
                    if failure:
                        send.side_effect = EmailError("still offline")
                    code = run_daily.generate_normal(args, settings, day, selector, history)
                self.assertEqual(code, expected_code)
                self.assertEqual(send.call_count, expected_calls)
                ai.assert_not_called()
                saved = json.loads(analysis_path.read_text())
                self.assertEqual(saved["analysis"], {"hook": "unchanged"})
                expected_status = ("failed" if failure else ("dry-run" if dry_run else "sent")) if expected_calls else status
                self.assertEqual(saved["email"]["status"], expected_status)
                if expected_calls:
                    self.assertEqual(send.call_args.kwargs["dry_run"], True if dry_run else None)
                    self.assertEqual(history.get(day.isoformat())["email_status"], expected_status)


class FetchReliabilityTests(unittest.TestCase):
    @staticmethod
    def response(*slugs: str) -> dict:
        return {"data": {f"q{i}": {"titleSlug": slug} for i, slug in enumerate(slugs)}}

    def test_cache_matches_requested_slugs_not_batch_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(fetch_leetcode, "BATCH_CACHE", Path(tmp)):
            cache = Path(tmp) / "batch_0000.json"
            cache.write_text(json.dumps(self.response("old")))
            with patch.object(fetch_leetcode, "batch_query", return_value=self.response("new")) as query:
                result = fetch_leetcode.fetch_batch(0, ["new"])
            query.assert_called_once_with(["new"])
            self.assertEqual(result, self.response("new"))

    def test_refresh_bypasses_valid_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(fetch_leetcode, "BATCH_CACHE", Path(tmp)):
            cache = Path(tmp) / "batch_0000.json"
            cache.write_text(json.dumps(self.response("one")))
            with patch.object(fetch_leetcode, "batch_query", return_value=self.response("one")) as query:
                fetch_leetcode.fetch_batch(0, ["one"])
                query.assert_not_called()
                fetch_leetcode.fetch_batch(0, ["one"], refresh=True)
                query.assert_called_once_with(["one"])

    def test_partial_response_fails_without_caching(self) -> None:
        for response in [
            {"data": {"q0": None}},
            self.response("wrong"),
            {**self.response("one"), "errors": [{"message": "partial"}]},
        ]:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as tmp:
                with patch.object(fetch_leetcode, "BATCH_CACHE", Path(tmp)), \
                     patch.object(fetch_leetcode, "batch_query", return_value=response), \
                     patch.object(fetch_leetcode.time, "sleep"):
                    with self.assertRaisesRegex(RuntimeError, "failed"):
                        fetch_leetcode.fetch_batch(0, ["one"], retries=1)
                self.assertFalse((Path(tmp) / "batch_0000.json").exists())

    def test_failed_fetch_retains_previous_index_and_returns_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index.json"
            index.write_text("previous index")
            metadata = {"questions": [{"titleSlug": "one", "frontendQuestionId": "1", "paidOnly": False}]}
            with patch.object(fetch_leetcode, "DATA", root), \
                 patch.object(fetch_leetcode, "PROBLEMS", root / "problems"), \
                 patch.object(fetch_leetcode, "load_topics", return_value={"categories": []}), \
                 patch.object(fetch_leetcode, "fetch_metadata", return_value=metadata), \
                 patch.object(fetch_leetcode, "fetch_batch", side_effect=RuntimeError("offline")), \
                 patch.object(sys, "argv", ["fetch_leetcode.py", "--refresh", "--workers", "1"]):
                code = fetch_leetcode.main()
            self.assertEqual(code, 1)
            self.assertEqual(index.read_text(), "previous index")
            self.assertFalse((root / "meta.json").exists())


class DatasetPublicationTests(unittest.TestCase):
    @support.requires_data
    def test_nonempty_dataset_can_be_published_and_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out, db = root / "normalized", root / "algorithms.db"
            with patch.object(build_dataset, "OUT", out), patch.object(build_dataset, "DB_PATH", db):
                build_dataset.publish_dataset([support.load_full("two-sum")], support.load_topic_cfg(), "new")
            with patch.object(bootstrap_data, "DB", db), \
                 patch.object(bootstrap_data, "MANIFEST", out / "manifest.json"):
                self.assertTrue(bootstrap_data.dataset_status()[0])
            self.assertEqual(json.loads((out / "problems/two-sum.json").read_text())["slug"], "two-sum")
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0], 1)

    def test_missing_raw_statement_stops_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index.json"
            index.write_text(json.dumps([{
                "slug": "missing", "id": 1, "frontend_id": "1", "paid_only": False,
            }]))
            with patch.object(build_dataset, "RAW_INDEX", index), \
                 patch.object(build_dataset, "RAW_PROBLEMS", root / "problems"), \
                 patch.object(build_dataset, "build_full", return_value={}), \
                 patch.object(build_dataset, "classify", return_value={}), \
                 patch.object(build_dataset, "publish_dataset") as publish, \
                 patch.object(sys, "argv", ["build_dataset.py"]):
                self.assertEqual(build_dataset.main(), 1)
            publish.assert_not_called()

    def test_build_failure_preserves_previous_dataset(self) -> None:
        for failing_step in ("write_outputs", "build_sqlite"):
            with self.subTest(step=failing_step), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                out, db = root / "normalized", root / "algorithms.db"
                out.mkdir()
                (out / "old.json").write_text("old JSON")
                db.write_text("old DB")
                with patch.object(build_dataset, "OUT", out), patch.object(build_dataset, "DB_PATH", db), \
                     patch.object(build_dataset, failing_step, side_effect=OSError("disk full")):
                    with self.assertRaisesRegex(OSError, "disk full"):
                        build_dataset.publish_dataset([], {"categories": [], "classification": {}}, "new")
                self.assertEqual((out / "old.json").read_text(), "old JSON")
                self.assertEqual(db.read_text(), "old DB")
                self.assertEqual(sorted(p.name for p in root.iterdir()), ["algorithms.db", "normalized"])

    def test_publish_failure_rolls_back_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out, db = root / "normalized", root / "algorithms.db"
            out.mkdir()
            (out / "old.json").write_text("old JSON")
            db.write_text("old DB")
            replace = Path.replace

            def fail_database(path: Path, target: Path) -> Path:
                if target == db:
                    raise OSError("publish failed")
                return replace(path, target)

            with patch.object(build_dataset, "OUT", out), patch.object(build_dataset, "DB_PATH", db), \
                 patch.object(Path, "replace", fail_database):
                with self.assertRaisesRegex(OSError, "publish failed"):
                    build_dataset.publish_dataset([], {"categories": [], "classification": {}}, "new")
            self.assertEqual((out / "old.json").read_text(), "old JSON")
            self.assertEqual(db.read_text(), "old DB")

    def test_success_replaces_outputs_and_removes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out, db = root / "normalized", root / "algorithms.db"
            out.mkdir()
            (out / "stale.json").write_text("old")
            db.write_text("old DB")
            with patch.object(build_dataset, "OUT", out), patch.object(build_dataset, "DB_PATH", db):
                build_dataset.publish_dataset([], {"categories": [], "classification": {}}, "new")
            self.assertFalse((out / "stale.json").exists())
            self.assertEqual(json.loads((out / "manifest.json").read_text())["generated_at"], "new")
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT value FROM meta WHERE key='generated_at'").fetchone()[0], "new")


class DatasetReadinessTests(unittest.TestCase):
    def test_missing_files_and_generation_mismatch_are_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normalized = root / "normalized"
            normalized.mkdir()
            manifest = normalized / "manifest.json"
            manifest.write_text(json.dumps({
                "generated_at": "current", "counts": {"total": 1, "free_with_statement": 1},
                "topics": {"array": {}},
            }))
            db = root / "algorithms.db"
            with sqlite3.connect(db) as conn:
                conn.executescript("""
                    CREATE TABLE problems (slug TEXT, has_statement INTEGER);
                    INSERT INTO problems VALUES ('one', 1);
                    CREATE TABLE meta (key TEXT, value TEXT);
                    INSERT INTO meta VALUES ('generated_at', 'current');
                """)
            files = ["index.json", "index.jsonl", "topics.json", "problems/one.json", "by-topic/array.json"]
            for filename in files:
                path = normalized / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
            with patch.object(bootstrap_data, "DB", db), patch.object(bootstrap_data, "MANIFEST", manifest):
                self.assertTrue(bootstrap_data.dataset_status()[0])
                (normalized / "problems/one.json").unlink()
                ready, detail = bootstrap_data.dataset_status()
                self.assertFalse(ready)
                self.assertIn("one.json", detail)
                (normalized / "problems/one.json").write_text("{}")
                with sqlite3.connect(db) as conn:
                    conn.execute("UPDATE meta SET value='old'")
                ready, detail = bootstrap_data.dataset_status()
                self.assertFalse(ready)
                self.assertIn("版本不一致", detail)


if __name__ == "__main__":
    unittest.main()

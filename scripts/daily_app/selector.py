"""Pick one problem for the day, balancing topic coverage and avoiding repeats."""

from __future__ import annotations

import json
import math
import random
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from .config import Settings
from .history import History


class SelectionError(RuntimeError):
    pass


class ProblemSelector:
    def __init__(self, settings: Settings, db_path: Path | None = None) -> None:
        self.settings = settings
        self.db_path = db_path or settings.root / "data" / "algorithms.db"
        if not self.db_path.exists():
            raise SelectionError(
                f"题目数据库不存在：{self.db_path}；先运行 scripts/build_dataset.py"
            )
        self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self.history = History(settings.root / "data" / "daily" / "history.json")

    def close(self) -> None:
        self.conn.close()

    def topics(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, label_zh, short_zh, accent FROM topics ORDER BY sort_order"
        ).fetchall()
        return [dict(r) for r in rows]

    def _row_to_pick(self, row: sqlite3.Row) -> dict:
        topic = row["primary_topic"]
        return {
            "slug": row["slug"],
            "frontend_id": row["frontend_id"],
            "title": row["title"],
            "title_zh": row["title_zh"],
            "difficulty": row["difficulty"],
            "topic_id": topic,
            "topic_label": self.settings.topic_label(topic),
            "topic_accent": self.settings.topic_accent(topic),
            "patterns": self.settings.topic_patterns(topic),
            "official_url": row["official_url"],
            "statement_lang": row["statement_lang"],
            "curated": bool(row["curated"]) if "curated" in row.keys() else False,
        }

    def _candidates(
        self,
        topic: str | None,
        difficulty: str | None,
        exclude: set[str],
        primary_only: bool,
        curated_only: bool,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        clauses = [
            "p.in_scope=1",
            "p.paid_only=0",
            "p.has_statement=1",
        ]
        params: list[Any] = []
        joins = ""
        if topic:
            joins = " JOIN problem_topics pt ON pt.problem_uid=p.uid AND pt.topic_id=?"
            params.append(topic)
            if primary_only:
                clauses.append("pt.is_primary=1")
            if curated_only:
                clauses.append("pt.curated=1")
        if difficulty:
            clauses.append("p.difficulty=?")
            params.append(difficulty)
        if exclude:
            marks = ",".join("?" for _ in exclude)
            clauses.append(f"p.slug NOT IN ({marks})")
            params.extend(sorted(exclude))
        sql = (
            "SELECT p.slug,p.frontend_id,p.title,p.title_zh,p.difficulty,"
            "p.primary_topic,p.official_url,p.statement_lang,p.likes"
            + (",pt.curated" if topic else ",0 AS curated")
            + " FROM problems p"
            + joins
            + " WHERE " + " AND ".join(clauses)
            + " ORDER BY RANDOM() LIMIT ?"
        )
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    @staticmethod
    def _weighted_choice(rows: list[sqlite3.Row]) -> sqlite3.Row:
        """Sample among quality candidates instead of uniform random.

        Curated problems get a small boost; within a pool, problems with more
        likes are modestly more likely, but a low-like problem can still win.
        The log scaling stops a 20k-like classic from completely drowning out
        a good 500-like problem.
        """
        weights = []
        for row in rows:
            weight = 1.0
            if "curated" in row.keys() and row["curated"]:
                weight += 0.8
            likes = row["likes"] if "likes" in row.keys() else 0
            if likes:
                weight *= 1.0 + min(math.log10(1 + likes) / 3.0, 1.2)
            weights.append(weight)
        return random.choices(rows, weights=weights, k=1)[0]

    def _coverage_window(self) -> int:
        return int(self.settings.app.get("selection", {}).get("coverage_window_days", 30))

    def _difficulty_order(self, as_of: date) -> list[str | None]:
        """Difficulties ordered by how far each is below its target share.

        Ends with None (any difficulty) so a thin topic never blocks a pick.
        """
        cfg = self.settings.app.get("selection", {}).get("difficulty_mix")
        if not cfg:
            return [None]
        levels = ["Easy", "Medium", "Hard"]
        targets = {d: float(cfg.get(d, 0)) for d in levels if float(cfg.get(d, 0)) > 0}
        total = sum(targets.values())
        if not total:
            return [None]
        counts = self.history.difficulty_counts(as_of, int(cfg.get("window_days", 7)))
        slots = sum(counts.values()) + 1
        deficit = {d: targets[d] / total * slots - counts.get(d, 0) for d in targets}
        order = sorted(deficit, key=lambda d: (-deficit[d], levels.index(d)))
        return [*order, None]

    def _topic_order(self, as_of: date, topics: list[dict]) -> list[str]:
        """Strict rotation: least covered first, then least recently used.

        Random is only an exact-tie breaker (e.g. topics never used yet), so
        each topic recurs every len(topics) days when the job runs daily.
        """
        counts = self.history.topic_counts(as_of, self._coverage_window())
        ages = self.history.last_used_by_topic(as_of)
        never = 10**6
        tiebreak = {t["id"]: random.random() for t in topics}
        ordered = sorted(
            topics,
            key=lambda t: (
                counts.get(t["id"], 0),
                -ages.get(t["id"], never),
                tiebreak[t["id"]],
            ),
        )
        return [t["id"] for t in ordered]

    def _choose_topic(self, as_of: date, topics: list[dict]) -> str:
        return self._topic_order(as_of, topics)[0]

    def pick(
        self,
        as_of: date,
        topic: str | None = None,
        difficulty: str | None = None,
        slug: str | None = None,
        exclude: set[str] | None = None,
    ) -> dict:
        if difficulty and difficulty not in {"Easy", "Medium", "Hard"}:
            raise SelectionError("difficulty 必须是 Easy / Medium / Hard")
        if slug:
            row = self.conn.execute(
                "SELECT p.slug,p.frontend_id,p.title,p.title_zh,p.difficulty,"
                "p.primary_topic,p.official_url,p.statement_lang,0 AS curated "
                "FROM problems p WHERE p.slug=?", (slug,)
            ).fetchone()
            if not row:
                raise SelectionError(f"找不到题目：{slug}")
            if row["frontend_id"] and not row["official_url"]:
                raise SelectionError("题目记录不完整")
            return self._row_to_pick(row)

        topics = self.topics()
        topic_ids = {t["id"] for t in topics}
        if topic and topic not in topic_ids:
            raise SelectionError(f"未知专题：{topic}")

        recent_days = int(self.settings.app.get("selection", {}).get("repeat_window_days", 180))
        recent = self.history.recent_slugs(as_of, recent_days)
        prefer_curated = bool(
            self.settings.app.get("selection", {}).get("prefer_curated", True)
        )
        strategy = self.settings.app.get("selection", {}).get("strategy", "balanced-random")

        target_topic: str | None = None
        attempts: list[str | None]
        if topic:
            target_topic = topic
            attempts = [topic]
        elif strategy == "random":
            attempts = [None]
        else:
            ordered = self._topic_order(as_of, topics)
            target_topic = ordered[0]
            attempts = [*ordered, None]

        difficulties = [difficulty] if difficulty else self._difficulty_order(as_of)

        for ignore_recent in (False, True):
            excluded = (set() if ignore_recent else set(recent)) | set(exclude or ())
            for attempt in attempts:
                # Difficulty is a soft preference inside the chosen topic: it is
                # relaxed before we ever move on to another topic.
                for diff in difficulties:
                    for curated_only in ([True, False] if prefer_curated else [False]):
                        # First prefer primary memberships; then allow secondary.
                        for primary_only in (True, False):
                            if curated_only and not primary_only:
                                continue
                            rows = self._candidates(
                                attempt,
                                diff,
                                excluded,
                                primary_only=primary_only,
                                curated_only=curated_only,
                            )
                            if rows:
                                chosen_row = self._weighted_choice(rows)
                                pick = self._row_to_pick(chosen_row)
                                # Coverage is credited to the topic we were filling,
                                # not to the problem's primary topic.
                                coverage_topic = attempt or pick["topic_id"]
                                window = self._coverage_window()
                                counts = self.history.topic_counts(as_of, window)
                                last_used = self.history.last_used_by_topic(as_of)
                                pick["coverage_topic"] = coverage_topic
                                pick["selection"] = {
                                    "strategy": strategy,
                                    "topic": attempt or "any",
                                    "target_topic": target_topic,
                                    "coverage_topic": coverage_topic,
                                    "coverage_satisfied": target_topic is None or attempt == target_topic,
                                    "primary_match": primary_only,
                                    "difficulty": difficulty,
                                    "difficulty_target": difficulties[0],
                                    "difficulty_relaxed": diff != difficulties[0],
                                    "recent_excluded": len(recent),
                                    "ignore_recent": ignore_recent,
                                    "coverage_window_days": window,
                                    "topic_count_in_window": counts.get(coverage_topic, 0),
                                    "topic_last_used_days_ago": last_used.get(coverage_topic),
                                    "curated": pick["curated"],
                                    "candidate_pool": len(rows),
                                }
                                return pick

        raise SelectionError("没有符合条件的题目；检查难度/专题筛选条件")

    def load_problem(self, slug: str) -> dict:
        path = self.settings.root / "data" / "normalized" / "problems" / f"{slug}.json"
        if not path.exists():
            raise SelectionError(f"题目没有本地正文：{slug}")
        record = json.loads(path.read_text())
        if record.get("paid_only") or not record.get("statement"):
            raise SelectionError(f"题目不可离线使用：{slug}")
        return record

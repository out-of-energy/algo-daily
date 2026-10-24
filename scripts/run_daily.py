#!/usr/bin/env python3
"""Generate one daily algorithm page and email it.

Examples
--------
    python3 scripts/run_daily.py --demo
    python3 scripts/run_daily.py --dry-run
    python3 scripts/run_daily.py --topic graph --difficulty Medium
    python3 scripts/run_daily.py --resend
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from daily_app.ai import AIClient, AIError, AnalysisError, load_demo_analysis  # noqa: E402
from daily_app.config import Settings  # noqa: E402
from daily_app.emailer import EmailError, Emailer  # noqa: E402
from daily_app.history import History  # noqa: E402
from daily_app.render import render_archive, render_page  # noqa: E402
from daily_app.selector import ProblemSelector, SelectionError  # noqa: E402
from bootstrap_data import bootstrap, dataset_status  # noqa: E402

DEMO_SLUG = "longest-substring-without-repeating-characters"


def today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def rebuild_archive(settings: Settings) -> Path:
    history = History(settings.root / "data" / "daily" / "history.json")
    path = settings.root / "site" / "index.html"
    write_text(path, render_archive(settings, history.all()))
    return path


def ensure_dataset(settings: Settings) -> bool:
    """The question bank is not committed; build it once on first run."""
    ready, detail = dataset_status()
    if ready:
        return True
    if not settings.app.get("data", {}).get("auto_bootstrap", True):
        print(f"题库{detail}。请先运行 `make data`。", file=sys.stderr)
        return False
    print(f"题库{detail}，先自动构建（只需一次）……", file=sys.stderr)
    return bootstrap() == 0


def public_or_empty(settings: Settings, day: date, demo: bool) -> str:
    return "" if demo else settings.public_day_url(day.isoformat())


def build_demo(args: argparse.Namespace, settings: Settings, day: date, selector: ProblemSelector) -> int:
    problem = selector.load_problem(DEMO_SLUG)
    problem["topic_label"] = settings.topic_label(problem.get("primary_topic"))
    analysis = load_demo_analysis(problem, model="demo-fixture")
    page = render_page(settings, problem, analysis, day, demo=True)
    page_path = settings.root / "site" / "demo.html"
    write_text(page_path, page)

    email_result = {"status": "skipped", "path": "", "subject": "", "to": []}
    if not args.no_email:
        emailer = Emailer(settings)
        email_result = emailer.send(
            analysis, problem, page, day,
            dry_run=not args.send,
            public_url="",
            demo=True,
        )
    result = {
        "mode": "demo",
        "date": day.isoformat(),
        "slug": problem["slug"],
        "title": problem.get("title_zh"),
        "page": str(page_path),
        "email": email_result,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"示例页面：{page_path}")
        if email_result.get("path"):
            print(f"预览邮件：{email_result['path']}")
        elif email_result.get("status") == "sent":
            print(f"测试邮件已发送：{', '.join(email_result.get('to', []))}")
        print("这是内置示例，不会写入每日历史。")
    return 0


def generate_normal(
    args: argparse.Namespace,
    settings: Settings,
    day: date,
    selector: ProblemSelector,
    history: History,
) -> int:
    date_str = day.isoformat()
    analysis_path = settings.root / "data" / "daily" / f"{date_str}.json"
    page_path = settings.root / "site" / "day" / f"{date_str}.html"
    existing = None
    if analysis_path.exists():
        try:
            existing = json.loads(analysis_path.read_text())
        except json.JSONDecodeError:
            existing = None

    if existing and not args.force and not args.reroll:
        if not page_path.exists():
            problem = selector.load_problem(existing["slug"])
            problem["topic_label"] = settings.topic_label(problem.get("primary_topic"))
            write_text(page_path, render_page(settings, problem, existing["analysis"], day))
        if args.resend or args.send:
            problem = selector.load_problem(existing["slug"])
            problem["topic_label"] = settings.topic_label(problem.get("primary_topic"))
            page = page_path.read_text()
            email_result = Emailer(settings).send(
                existing["analysis"], problem, page, day,
                dry_run=(False if args.send else (True if args.dry_run else None)),
                public_url=public_or_empty(settings, day, False),
            )
            existing["email"] = email_result
            write_json(analysis_path, existing)
            _record_history(
                settings, history, day, problem, analysis_path, page_path,
                email_result, existing.get("selection"),
            )
            if args.json:
                print(json.dumps({
                    "mode": "resend",
                    "date": date_str,
                    "slug": problem["slug"],
                    "page": str(page_path),
                    "email": email_result,
                }, ensure_ascii=False, indent=2))
            else:
                print(f"已重发已有分析：{email_result}")
            return 0
        print(f"{date_str} 已有分析：{analysis_path}")
        print("如需重新生成，加 --force；如需重发邮件，加 --resend。")
        return 0

    slug = args.slug
    problem = None
    selection = None
    reuse_selection = None
    if existing and args.force and not args.reroll:
        slug = existing["slug"]
        reuse_selection = existing.get("selection")
    if slug:
        problem = selector.load_problem(slug)
        selection = reuse_selection or {
            "strategy": "manual",
            "topic": problem.get("primary_topic"),
            "coverage_topic": problem.get("primary_topic"),
            "coverage_satisfied": True,
        }
    else:
        pick = selector.pick(
            day,
            topic=args.topic,
            difficulty=args.difficulty,
        )
        problem = selector.load_problem(pick["slug"])
        selection = pick.get("selection")
    problem["topic_label"] = settings.topic_label(problem.get("primary_topic"))

    if args.demo:
        analysis = load_demo_analysis(problem, model="demo-fixture")
    else:
        if not settings.ai_configured:
            print(
                "未配置 AI_API_KEY。请复制 .env.example 为 .env，填入 AI_API_KEY；"
                "或者先运行 `python3 scripts/run_daily.py --demo` 预览。",
                file=sys.stderr,
            )
            return 2
        failed: list[str] = []
        reroll_limit = int(settings.app.get("verify", {}).get("reroll_limit", 2))
        while True:
            try:
                analysis = AIClient(settings).generate(
                    problem,
                    problem["topic_label"],
                    settings.topic_patterns(problem.get("primary_topic")),
                    date_str,
                )
                break
            except (AIError, AnalysisError) as exc:
                print(f"AI 生成失败（{problem['slug']}）：{exc}", file=sys.stderr)
                failed.append(problem["slug"])
                # A manually chosen problem is never swapped; unverified content is never published.
                if slug or len(failed) > reroll_limit:
                    return 1
                try:
                    pick = selector.pick(
                        day,
                        topic=(selection or {}).get("coverage_topic") or args.topic,
                        difficulty=args.difficulty,
                        exclude=set(failed),
                    )
                except Exception as pick_exc:  # noqa: BLE001
                    print(f"换题失败：{pick_exc}", file=sys.stderr)
                    return 1
                problem = selector.load_problem(pick["slug"])
                problem["topic_label"] = settings.topic_label(problem.get("primary_topic"))
                selection = {**(pick.get("selection") or {}), "rerolled_from": list(failed)}
                print(f"换一道同专题的题重试：{problem.get('title_zh')}", file=sys.stderr)

    page = render_page(settings, problem, analysis, day, demo=args.demo)
    write_text(page_path, page)
    # A convenient stable link for local preview.
    write_text(settings.root / "site" / "latest.html", page)
    analysis_doc = {
        "date": date_str,
        "slug": problem["slug"],
        "title_zh": problem.get("title_zh"),
        "topic": problem.get("primary_topic"),
        "selection": selection,
        "analysis": analysis,
        "email": {"status": "skipped", "path": "", "subject": "", "to": []},
    }

    if not args.no_email:
        try:
            email_result = Emailer(settings).send(
                analysis, problem, page, day,
                dry_run=(False if args.send else (True if args.dry_run else None)),
                public_url=public_or_empty(settings, day, args.demo),
                demo=args.demo,
            )
        except EmailError as exc:
            print(f"邮件发送失败：{exc}", file=sys.stderr)
            email_result = {"status": "failed", "error": str(exc), "path": "", "subject": "", "to": []}
            analysis_doc["email"] = email_result
            write_json(analysis_path, analysis_doc)
            _record_history(
                settings, history, day, problem, analysis_path, page_path,
                email_result, selection,
            )
            rebuild_archive(settings)
            return 1
    else:
        email_result = {"status": "skipped", "path": "", "subject": "", "to": []}
    analysis_doc["email"] = email_result
    write_json(analysis_path, analysis_doc)
    _record_history(
        settings, history, day, problem, analysis_path, page_path,
        email_result, selection,
    )
    archive_path = rebuild_archive(settings)

    result = {
        "mode": "daily",
        "date": date_str,
        "slug": problem["slug"],
        "title": problem.get("title_zh"),
        "topic": problem.get("topic_label"),
        "page": str(page_path),
        "latest": str(settings.root / "site" / "latest.html"),
        "archive": str(archive_path),
        "selection": selection,
        "email": email_result,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"今日题目：{problem.get('title_zh')}（{problem.get('difficulty')}）")
        if selection and selection.get("strategy") != "manual":
            coverage_label = settings.topic_label(selection.get("coverage_topic"))
            last_used = selection.get("topic_last_used_days_ago")
            last_text = "从未" if last_used is None else f"{last_used} 天前"
            print(
                f"选题依据：近 {selection.get('coverage_window_days')} 天，"
                f"{coverage_label} 出现 {selection.get('topic_count_in_window', 0)} 次；"
                f"距上次出现：{last_text}；"
                f"候选池 {selection.get('candidate_pool', 0)} 道，精选={selection.get('curated')}"
            )
            if selection.get("coverage_satisfied") is False:
                print("警告：目标专题没有可用题目，本次未满足专题覆盖约束。", file=sys.stderr)
        print(f"页面：{page_path}")
        print(f"归档：{archive_path}")
        if email_result.get("status") == "dry-run":
            print(f"邮件预览（未发送）：{email_result['path']}")
        elif email_result.get("status") == "sent":
            print(f"邮件已发送：{', '.join(email_result.get('to', []))}")
        elif email_result.get("status") == "skipped":
            print("邮件：已跳过")
    return 0


def _record_history(
    settings: Settings,
    history: History,
    day: date,
    problem: dict,
    analysis_path: Path,
    page_path: Path,
    email_result: dict,
    selection: dict | None = None,
) -> None:
    history.upsert({
        "date": day.isoformat(),
        "slug": problem["slug"],
        "title": problem.get("title"),
        "title_zh": problem.get("title_zh"),
        "difficulty": problem.get("difficulty"),
        "topic": problem.get("primary_topic"),
        "coverage_topic": (selection or {}).get("coverage_topic") or problem.get("primary_topic"),
        "topic_label": problem.get("topic_label") or settings.topic_label(problem.get("primary_topic")),
        "analysis": str(analysis_path.relative_to(settings.root)),
        "page": str(page_path.relative_to(settings.root)),
        "email_status": email_result.get("status", "unknown"),
        "email_path": email_result.get("path", ""),
        "selection": selection or {},
        "sent_at": datetime.now(ZoneInfo(settings.timezone)).replace(microsecond=0).isoformat(),
    })


def main() -> int:
    settings = Settings()
    parser = argparse.ArgumentParser(description="生成每日算法题页面并推送邮件")
    parser.add_argument("--date", help="YYYY-MM-DD，默认是配置时区的今天")
    parser.add_argument("--slug", help="指定题目 slug")
    parser.add_argument("--topic", help="指定专题 id，例如 graph")
    parser.add_argument("--difficulty", choices=["Easy", "Medium", "Hard"])
    parser.add_argument("--demo", action="store_true", help="使用内置示例，不调用 AI、不写历史")
    parser.add_argument("--force", action="store_true", help="重新生成已有日期的分析")
    parser.add_argument("--reroll", action="store_true", help="与 --force 一起重新随机选题")
    parser.add_argument("--resend", action="store_true", help="复用已有分析，只重发邮件")
    parser.add_argument("--dry-run", action="store_true", help="只写 .eml，不真的发信")
    parser.add_argument("--send", action="store_true", help="强制真实发送（忽略 MAIL_DRY_RUN）")
    parser.add_argument("--no-email", action="store_true", help="只生成页面，不生成/发送邮件")
    parser.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = parser.parse_args()
    if args.send and args.dry_run:
        parser.error("--send 和 --dry-run 不能同时使用")
    if args.no_email and args.send:
        parser.error("--no-email 和 --send 不能同时使用")

    try:
        day = date.fromisoformat(args.date) if args.date else today(settings)
    except ValueError:
        parser.error("--date 必须是 YYYY-MM-DD")

    if not ensure_dataset(settings):
        return 1
    selector = ProblemSelector(settings)
    try:
        if args.demo:
            return build_demo(args, settings, day, selector)
        history = History(settings.root / "data" / "daily" / "history.json")
        return generate_normal(args, settings, day, selector, history)
    except (SelectionError, EmailError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    finally:
        selector.close()


if __name__ == "__main__":
    sys.exit(main())

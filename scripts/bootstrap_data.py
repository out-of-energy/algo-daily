#!/usr/bin/env python3
"""Build the local question bank on first run (the dataset is not committed).

    python3 scripts/bootstrap_data.py            # build only if missing
    python3 scripts/bootstrap_data.py --check    # report status, exit 1 if missing
    python3 scripts/bootstrap_data.py --rebuild  # rebuild from local raw data (offline)
    python3 scripts/bootstrap_data.py --refetch  # fetch fresh data from LeetCode, then rebuild

Pipeline: fetch_leetcode.py -> backfill_translations.py -> build_dataset.py -> validate_dataset.py
The fetch step needs network access to leetcode.cn and is cached under .cache/,
so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DB = ROOT / "data" / "algorithms.db"
MANIFEST = ROOT / "data" / "normalized" / "manifest.json"
RAW_INDEX = ROOT / "data" / "raw" / "leetcode" / "index.json"


def dataset_status() -> tuple[bool, str]:
    if not DB.exists() or not MANIFEST.exists():
        return False, "未构建（缺少 data/algorithms.db 或 data/normalized/）"
    try:
        manifest = json.loads(MANIFEST.read_text())
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
            total = conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
            slugs = [row[0] for row in conn.execute("SELECT slug FROM problems WHERE has_statement = 1")]
            generation = conn.execute("SELECT value FROM meta WHERE key='generated_at'").fetchone()
        if not generation or generation[0] != manifest["generated_at"]:
            return False, "数据库与 JSON 构建版本不一致，请运行 make build"
        if total != manifest["counts"]["total"] or len(slugs) != manifest["counts"]["free_with_statement"]:
            return False, "数据库与 manifest 数量不一致，请运行 make build"
        normalized = MANIFEST.parent
        required = [
            normalized / "index.json", normalized / "index.jsonl", normalized / "topics.json",
            *(normalized / "problems" / f"{slug}.json" for slug in slugs),
            *(normalized / "by-topic" / f"{topic}.json" for topic in manifest["topics"]),
        ]
        missing = next((path for path in required if not path.is_file()), None)
        if missing:
            return False, f"题库文件缺失：{missing}，请运行 make build"
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        return False, f"题库无法读取：{exc}"
    if not total:
        return False, "数据库为空"
    return True, f"{total} 道题，其中 {len(slugs)} 道有完整题面"


def log(message: str = "") -> None:
    # Progress goes to stderr so `--json` callers keep a clean stdout.
    print(message, file=sys.stderr, flush=True)


def run_step(label: str, args: list[str]) -> None:
    log(f"\n==> {label}")
    started = time.monotonic()
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, stdout=sys.stderr)
    if proc.returncode != 0:
        raise SystemExit(
            f"\n{label} 失败（退出码 {proc.returncode}）。"
            "修复后重新运行刚才的命令；make refresh 会忽略旧缓存重新抓取，普通抓取可复用匹配的缓存。"
        )
    log(f"    完成，用时 {time.monotonic() - started:.0f} 秒")


def bootstrap(refetch: bool = False, rebuild: bool = False, workers: int = 6) -> int:
    ready, detail = dataset_status()
    if ready and not (refetch or rebuild):
        log(f"题库已就绪：{detail}")
        return 0

    need_fetch = refetch or not RAW_INDEX.exists()
    if rebuild and not RAW_INDEX.exists():
        log("本地没有原始数据（data/raw/leetcode/），改为联网抓取。")
    started = time.monotonic()
    if need_fetch:
        log("首次构建题库：从 leetcode.cn 抓取公开题目数据（只抓免费题题面，付费题只记元数据）。")
        log("需要联网，通常几分钟；中断后重跑会从缓存续传。")
        fetch = [str(SCRIPTS / "fetch_leetcode.py"), "--workers", str(workers)]
        if refetch:
            fetch.append("--refresh")
        run_step("1/4 抓取题目", fetch)
        run_step("2/4 回填中文题面", [str(SCRIPTS / "backfill_translations.py")])
    else:
        log("使用本地已有的原始数据离线构建（不联网）。")
    run_step("3/4 规范化并生成 SQLite", [str(SCRIPTS / "build_dataset.py")])
    run_step("4/4 校验数据一致性", [str(SCRIPTS / "validate_dataset.py")])

    ready, detail = dataset_status()
    if not ready:
        log(f"\n构建结束但题库仍不可用：{detail}")
        return 1
    log(f"\n题库已就绪：{detail}（总用时 {time.monotonic() - started:.0f} 秒）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="只检查状态；缺失时退出码为 1")
    group.add_argument("--rebuild", action="store_true", help="用本地原始数据重新构建（不联网）")
    group.add_argument("--refetch", action="store_true", help="重新从 LeetCode 抓取最新数据并重建")
    parser.add_argument("--workers", type=int, default=6, help="抓取并发数")
    args = parser.parse_args()
    if args.check:
        ready, detail = dataset_status()
        print(("OK  " if ready else "!!  ") + "题库：" + detail)
        if not ready:
            print("    运行 `make data` 构建。")
        return 0 if ready else 1
    return bootstrap(refetch=args.refetch, rebuild=args.rebuild, workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())

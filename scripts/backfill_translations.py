#!/usr/bin/env python3
"""Backfill Chinese statements for free problems whose English body is a
placeholder ("English description is not available...")."""

from __future__ import annotations

import concurrent.futures
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "leetcode"
PROBLEMS = RAW / "problems"
CACHE = ROOT / ".cache" / "leetcode" / "translations"
URL = "https://leetcode.cn/graphql/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"
BATCH = 20
PLACEHOLDER = "english description is not available"


def clean(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"(?is)<(script|style|iframe|object|embed)\b.*?</\1>", "", value)
    value = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", value)
    return value.strip()


def http_json(payload: dict) -> dict:
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Referer": "https://leetcode.cn/problemset/",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode())


def fetch(slugs: list[str], index: int) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"batch_{index:04d}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if data.get("data"):
                return data
        except Exception:  # noqa: BLE001
            pass
    aliases = []
    for i, slug in enumerate(slugs):
        aliases.append(
            f'q{i}: question(titleSlug: "{slug}") {{ title translatedTitle translatedContent }}'
        )
    query = "query { " + " ".join(aliases) + " }"
    last = None
    for attempt in range(5):
        try:
            data = http_json({"query": query})
            if not data.get("data"):
                raise RuntimeError(str(data)[:300])
            path.write_text(json.dumps(data, ensure_ascii=False))
            time.sleep(0.2)
            return data
        except urllib.error.HTTPError as exc:
            last = exc
            wait = max(2 ** attempt, 12 if exc.code == 429 else 0)
            print(f"  batch {index:02d}: HTTP {exc.code}, retry in {wait}s", flush=True)
            time.sleep(wait)
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = 2 ** attempt
            print(f"  batch {index:02d}: {type(exc).__name__}, retry in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"batch {index} failed: {last}")


def main() -> int:
    targets = []
    for path in sorted(PROBLEMS.glob("*.json")):
        record = json.loads(path.read_text())
        if record.get("paid_only"):
            continue
        html_body = (record.get("content_html") or "").lower()
        translated = record.get("translated_content_html") or ""
        if PLACEHOLDER in html_body and not translated:
            targets.append(record["slug"])
    print(f"targets: {len(targets)} free problems with placeholder English statement")
    if not targets:
        return 0

    batches = [targets[i:i + BATCH] for i in range(0, len(targets), BATCH)]
    updates = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(fetch, slugs, i): slugs
            for i, slugs in enumerate(batches)
        }
        for future in concurrent.futures.as_completed(futures):
            slugs = futures[future]
            try:
                data = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED batch {slugs[:2]}...: {exc}")
                continue
            for i, slug in enumerate(slugs):
                question = (data.get("data") or {}).get(f"q{i}") or {}
                value = clean(question.get("translatedContent"))
                if value:
                    updates[slug] = value
                else:
                    print(f"  no Chinese content for {slug}")
            print(f"  fetched batch {len(updates)} translations so far", flush=True)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    written = 0
    for slug, translated in updates.items():
        path = PROBLEMS / f"{slug}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text())
        record["translated_content_html"] = translated
        record["translated_fetched_at"] = now
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        written += 1
    print(f"updated {written}/{len(targets)} raw records")
    missing = [s for s in targets if s not in updates]
    if missing:
        print(f"missing translations: {len(missing)}; first: {missing[:5]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

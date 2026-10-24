#!/usr/bin/env python3
"""Build a local index of LeetCode problems + full statements for free problems.

Policy:
  * All public problem metadata is indexed (including premium titles).
  * Full statements are fetched only when LeetCode marks a question as free.
  * Premium statements are never requested or stored. Use the official link.

Data source:
  * https://leetcode.cn/graphql/  (public metadata for every question,
    full statements only for questions LeetCode marks as free)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "raw" / "leetcode"
PROBLEMS = DATA / "problems"
CACHE = ROOT / ".cache" / "leetcode"
BATCH_CACHE = CACHE / "batches"
LIST_URL = "https://leetcode.cn/graphql/"
LIST_PAGE_SIZE = 100
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"

ALLOWED_LANGS = {
    "python3", "java", "cpp", "javascript", "typescript", "golang", "rust", "csharp",
}

QUESTION_FIELDS = """
  questionId
  title
  translatedTitle
  titleSlug
  difficulty
  isPaidOnly
  content
  translatedContent
  hints
  topicTags { name slug }
  codeSnippets { lang langSlug code }
  sampleTestCase
  exampleTestcases
  metaData
  stats
  likes
  dislikes
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def http_json(url: str, payload: dict | None = None, timeout: int = 90) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Referer"] = "https://leetcode.cn/problemset/"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def fetch_metadata(refresh: bool = False) -> dict:
    """Fetch every public question's metadata (including premium) without content."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE / "cn_problemset.json"
    if cache_path.exists() and not refresh:
        age = time.time() - cache_path.stat().st_mtime
        if age < 86400:
            log(f"metadata: using cache ({age / 3600:.1f}h old)")
            return json.loads(cache_path.read_text())

    log("metadata: downloading LeetCode CN problem index ...")
    questions: list[dict] = []
    total = None
    skip = 0
    query = """
    query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
      problemsetQuestionList(categorySlug: $categorySlug, limit: $limit, skip: $skip, filters: $filters) {
        total
        questions {
          frontendQuestionId
          title
          titleCn
          titleSlug
          difficulty
          paidOnly
          topicTags { name slug nameTranslated }
          acRate
        }
      }
    }
    """
    while True:
        payload = {
            "query": query,
            "variables": {
                "categorySlug": "all-code-essentials",
                "limit": LIST_PAGE_SIZE,
                "skip": skip,
                "filters": {},
            },
        }
        page = None
        last_err = None
        for attempt in range(5):
            try:
                data = http_json(LIST_URL, payload, timeout=60)
                page = (data.get("data") or {}).get("problemsetQuestionList") or {}
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(2 ** attempt + random.random())
        if page is None:
            raise RuntimeError(f"could not fetch metadata page at skip={skip}: {last_err}")
        batch = page.get("questions") or []
        total = page.get("total") or total
        questions.extend(batch)
        log(f"  metadata: {len(questions)}/{total or '?'}")
        if not batch or (total and len(questions) >= total):
            break
        skip += LIST_PAGE_SIZE
        time.sleep(0.15)

    out = {
        "total": total or len(questions),
        "generated_at": iso_now(),
        "source": LIST_URL,
        "questions": questions,
    }
    cache_path.write_text(json.dumps(out, ensure_ascii=False))
    log(f"metadata: {len(questions)} problems")
    return out


def batch_query(slugs: list[str]) -> dict:
    parts = []
    for i, slug in enumerate(slugs):
        # Slugs are from LeetCode's own index and constrained to [a-z0-9-].
        parts.append(f'q{i}: question(titleSlug: "{slug}") {{ {QUESTION_FIELDS} }}')
    query = "query { " + " ".join(parts) + " }"
    return http_json(LIST_URL, {"query": query})


def valid_batch_data(raw: dict, slugs: list[str]) -> bool:
    data = raw.get("data") or {}
    return (
        not raw.get("errors")
        and len(data) == len(slugs)
        and all(isinstance(q, dict) for q in data.values())
        and {q.get("titleSlug") for q in data.values()} == set(slugs)
    )


def valid_batch_file(path: Path, slugs: list[str]) -> bool:
    try:
        raw = json.loads(path.read_text())
        return isinstance(raw, dict) and valid_batch_data(raw, slugs)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def fetch_batch(index: int, slugs: list[str], retries: int = 5, refresh: bool = False) -> dict:
    BATCH_CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = BATCH_CACHE / f"batch_{index:04d}.json"
    if not refresh and valid_batch_file(cache_path, slugs):
        return json.loads(cache_path.read_text())

    last_err = None
    for attempt in range(retries):
        try:
            data = batch_query(slugs)
            if not valid_batch_data(data, slugs):
                raise RuntimeError(f"incomplete or mismatched GraphQL response: {str(data)[:300]}")
            cache_path.write_text(json.dumps(data, ensure_ascii=False))
            return data
        except urllib.error.HTTPError as exc:
            last_err = exc
            wait = 2 ** attempt + random.random()
            if exc.code == 429:
                wait = max(wait, 15)
            log(f"  batch {index:04d}: HTTP {exc.code}, retry in {wait:.1f}s")
            time.sleep(wait)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            wait = 2 ** attempt + random.random()
            log(f"  batch {index:04d}: {type(exc).__name__}, retry in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"batch {index:04d} failed: {last_err}")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    # Official statements are trusted, but local pages must never execute markup.
    value = re.sub(r"(?is)<(script|style|iframe|object|embed)\b.*?</\1>", "", value)
    value = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", value)
    return value.strip()


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_topics() -> dict:
    return json.loads((ROOT / "config" / "topics.json").read_text())


def compute_tracks(problem_id: int, tags: list[dict], topics: dict) -> list[str]:
    tag_slugs = {t["slug"] for t in tags}
    tracks = []
    for cat in topics["categories"]:
        if problem_id in cat["must"] or tag_slugs.intersection(cat["tags"]):
            tracks.append(cat["id"])
    return tracks


def build_problem_record(q: dict, meta: dict, fetched_at: str) -> dict:
    snippets = []
    for s in q.get("codeSnippets") or []:
        if s.get("langSlug") in ALLOWED_LANGS:
            snippets.append({
                "lang": s.get("lang"),
                "lang_slug": s.get("langSlug"),
                "code": s.get("code", "").replace("\r\n", "\n"),
            })
    try:
        meta_data = json.loads(q.get("metaData") or "{}")
    except json.JSONDecodeError:
        meta_data = {}
    stats = q.get("stats") or {}
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except json.JSONDecodeError:
            stats = {}
    ac_rate = stats.get("acRate")
    if isinstance(ac_rate, str):
        ac_rate = ac_rate.strip().rstrip("%")
        try:
            ac_rate = float(ac_rate)
        except ValueError:
            ac_rate = None
    return {
        "id": meta["id"],
        "frontend_id": meta["frontend_id"],
        "slug": q["titleSlug"],
        "title": html.unescape(q.get("title") or ""),
        "title_zh": html.unescape(q.get("translatedTitle") or q.get("title") or ""),
        "difficulty": q.get("difficulty", "Unknown"),
        "paid_only": bool(q.get("isPaidOnly")),
        "tags": [
            {
                "name": t.get("name", ""),
                "slug": t.get("slug", ""),
                "name_zh": t.get("nameTranslated") or t.get("name", ""),
            }
            for t in (q.get("topicTags") or [])
        ],
        "content_html": clean_text(q.get("content")),
        "translated_content_html": clean_text(q.get("translatedContent")),
        "hints": [clean_text(h) for h in (q.get("hints") or []) if h],
        "code_snippets": snippets,
        "sample_testcase": q.get("sampleTestCase") or "",
        "example_testcases": q.get("exampleTestcases") or "",
        "meta_data": meta_data,
        "stats": {
            "ac_rate": ac_rate,
            "total_accepted": stats.get("totalAccepted"),
            "total_submission": stats.get("totalSubmission"),
        },
        "likes": q.get("likes"),
        "dislikes": q.get("dislikes"),
        "source": "leetcode.cn",
        "source_url": f"https://leetcode.cn/problems/{q['titleSlug']}/",
        "fetched_at": fetched_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch local LeetCode index + free statements")
    parser.add_argument("--refresh", action="store_true", help="ignore metadata and statement batch caches")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0, help="debug: only N free problems")
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()

    topics = load_topics()
    metadata = fetch_metadata(refresh=args.refresh)
    rows = metadata.get("questions", [])
    if not rows:
        raise SystemExit("No problems returned")

    fetched_at = iso_now()
    index: list[dict] = []
    by_slug: dict[str, dict] = {}
    skipped = 0
    extra_seq = 0
    for q in rows:
        slug = q.get("titleSlug")
        frontend_raw = str(q.get("frontendQuestionId") or "").strip()
        if not slug or not frontend_raw:
            skipped += 1
            continue
        if frontend_raw.isdigit():
            internal_id = int(frontend_raw)
        else:
            # LCP / LCR / LCS / 面试题: keep a numeric internal id for sorting and
            # preserve the real label separately for display.
            extra_seq += 1
            internal_id = 100000 + extra_seq
        difficulty = str(q.get("difficulty") or "Unknown").title()
        ac_rate = q.get("acRate")
        if isinstance(ac_rate, (int, float)):
            ac_rate = round(ac_rate * 100, 1) if ac_rate <= 1 else round(ac_rate, 1)
        tags = [
            {
                "name": t.get("name", ""),
                "slug": t.get("slug", ""),
                "name_zh": t.get("nameTranslated") or t.get("name", ""),
            }
            for t in (q.get("topicTags") or [])
        ]
        item = {
            "id": internal_id,
            "frontend_id": frontend_raw,
            "slug": slug,
            "title": html.unescape(q.get("title") or ""),
            "title_zh": html.unescape(q.get("titleCn") or q.get("title") or ""),
            "difficulty": difficulty,
            "paid_only": bool(q.get("paidOnly")),
            "has_content": False,
            "ac_rate": ac_rate,
            "tags": tags,
            "tracks": [],
            "url": f"https://leetcode.cn/problems/{slug}/",
        }
        index.append(item)
        by_slug[slug] = item
    if skipped:
        log(f"metadata: skipped {skipped} rows without a usable id/slug")

    # A few duplicate frontend IDs can occur in old archived rows; de-dupe by slug first,
    # then keep the lowest-id record for each numeric ID.
    free_slugs = [it["slug"] for it in index if not it["paid_only"]]
    free_slugs = list(dict.fromkeys(free_slugs))
    if args.limit:
        free_slugs = free_slugs[: args.limit]
    log(f"free statements: {len(free_slugs)} to fetch" + (" (metadata only)" if args.metadata_only else ""))

    if not args.metadata_only:
        PROBLEMS.mkdir(parents=True, exist_ok=True)
        batches = [free_slugs[i:i + args.batch_size] for i in range(0, len(free_slugs), args.batch_size)]
        log(f"batches: {len(batches)} x {args.batch_size}")

        def work(pair: tuple[int, list[str]]) -> tuple[int, dict]:
            i, slugs = pair
            try:
                result = fetch_batch(i, slugs, refresh=args.refresh)
                return i, result
            except Exception as exc:  # noqa: BLE001
                log(f"  batch {i:04d} FAILED: {exc}")
                return i, {}

        done = 0
        failed_batches = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for i, result in pool.map(work, list(enumerate(batches))):
                done += 1
                if done % 10 == 0 or done == len(batches):
                    log(f"  fetched {done}/{len(batches)} batches")
                data = result.get("data") or {}
                if not data:
                    failed_batches.append(i)
                for qi, q in data.items():
                    if not q or q.get("isPaidOnly"):
                        continue
                    slug = q.get("titleSlug")
                    if slug not in by_slug:
                        continue
                    item = by_slug[slug]
                    if item["paid_only"]:
                        continue
                    rec = build_problem_record(q, item, fetched_at)
                    # Preserve Chinese tag labels from the list endpoint; the
                    # per-question TopicTagNode does not expose nameTranslated.
                    zh_by_slug = {t["slug"]: t.get("name_zh", t["name"]) for t in item.get("tags", [])}
                    for tag in rec["tags"]:
                        tag["name_zh"] = zh_by_slug.get(tag["slug"], tag["name"])
                    path = PROBLEMS / f"{slug}.json"
                    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
                    item["has_content"] = bool(rec["content_html"])
                    item["title"] = rec["title"]
                    item["title_zh"] = rec["title_zh"]
                    item["tags"] = rec["tags"]
                    item["likes"] = rec["likes"]
                    item["dislikes"] = rec["dislikes"]
                    if rec["stats"]["ac_rate"] is not None:
                        item["ac_rate"] = rec["stats"]["ac_rate"]

        if failed_batches:
            log(f"fetch failed: {len(failed_batches)} batches incomplete; previous index retained. Rerun to retry.")
            return 1

    for item in index:
        item["tracks"] = compute_tracks(item["id"], item.get("tags", []), topics)
        if item.get("tags"):
            item["tag_slugs"] = [t["slug"] for t in item["tags"]]
            item["tag_names"] = [t["name"] for t in item["tags"]]
            item["primary_track"] = item["tracks"][0] if item["tracks"] else None

    # Stable ordering by frontend id; remove exact duplicate slugs.
    dedup = {}
    for item in index:
        dedup.setdefault(item["slug"], item)
    index = sorted(dedup.values(), key=lambda x: (x["id"], x["slug"]))
    index_path = DATA / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1))

    with_content = sum(1 for x in index if x["has_content"])
    paid = sum(1 for x in index if x["paid_only"])
    meta = {
        "generated_at": fetched_at,
        "source": "leetcode.cn public GraphQL (metadata for all; free statements only)",
        "license_note": "Problem statements remain LeetCode's copyrighted content; local personal study only. Do not redistribute.",
        "total": len(index),
        "free_with_content": with_content,
        "paid_metadata_only": paid,
        "tracks": [
            {
                "id": c["id"],
                "label": c["label"],
                "count": sum(1 for x in index if c["id"] in x.get("tracks", [])),
                "free_count": sum(1 for x in index if c["id"] in x.get("tracks", []) and not x["paid_only"]),
            }
            for c in topics["categories"]
        ],
    }
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    log(json.dumps(meta, ensure_ascii=False, indent=2))
    log(f"wrote {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

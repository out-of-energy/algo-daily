"""OpenAI-compatible AI client + strict normalization of the analysis JSON."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from .verify import verify_analysis
from .visual import AnalysisError, normalize_visual

__all__ = ["AIClient", "AIError", "AnalysisError", "load_demo_analysis", "normalize_analysis", "normalize_visual"]


class AIError(RuntimeError):
    pass


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def normalize_analysis(data: dict, problem: dict, topic_label: str, model: str) -> dict:
    if not isinstance(data, dict):
        raise AnalysisError("AI 返回的 JSON 顶层不是对象")
    real = _as_dict(data.get("real_meaning"))
    approach = _as_dict(data.get("approach"))
    code = _as_dict(data.get("code"))
    complexity = _as_dict(approach.get("complexity"))

    plain = _as_text(real.get("plain"))
    if not plain:
        raise AnalysisError("real_meaning.plain 缺失")
    code_source = _as_text(code.get("source"))
    if not code_source:
        raise AnalysisError("code.source 缺失")
    steps = [_as_text(x) for x in _as_list(approach.get("steps")) if _as_text(x)]
    if not steps:
        raise AnalysisError("approach.steps 不能为空")

    visual = normalize_visual(data.get("visual"), code_source)

    checks = []
    for item in _as_list(data.get("checks")):
        if isinstance(item, dict):
            q, a = _as_text(item.get("q")), _as_text(item.get("a"))
            if q and a:
                checks.append({"q": q, "a": a})
        elif isinstance(item, str) and item.strip():
            checks.append({"q": item.strip(), "a": "先自己回答，再对照代码。"})
    followups = []
    for item in _as_list(data.get("followups")):
        if isinstance(item, dict):
            title = _as_text(item.get("title"))
            idea = _as_text(item.get("idea"))
            if title:
                followups.append({"title": title, "idea": idea})
        elif isinstance(item, str) and item.strip():
            followups.append({"title": item.strip(), "idea": ""})

    traps = [_as_text(x) for x in _as_list(real.get("traps")) if _as_text(x)]
    pitfalls = [_as_text(x) for x in _as_list(approach.get("pitfalls")) if _as_text(x)]

    return {
        "schema_version": 1,
        "problem": {
            "slug": problem["slug"],
            "frontend_id": problem.get("frontend_id"),
            "title": problem.get("title"),
            "title_zh": problem.get("title_zh"),
            "difficulty": problem.get("difficulty"),
            "topic_id": problem.get("primary_topic"),
            "topic_label": topic_label,
            "official_url": problem.get("official_url"),
        },
        "hook": _as_text(data.get("hook"), _as_text(data.get("takeaway"))),
        "real_meaning": {
            "plain": plain,
            "analogy": _as_text(real.get("analogy")),
            "input_output": _as_text(real.get("input_output")),
            "test": _as_text(real.get("test")),
            "traps": traps,
        },
        "approach": {
            "name": _as_text(approach.get("name"), "算法思路"),
            "one_line": _as_text(approach.get("one_line")),
            "why_it_works": _as_text(approach.get("why_it_works")),
            "steps": steps,
            "complexity": {
                "time": _as_text(complexity.get("time"), "未给出"),
                "space": _as_text(complexity.get("space"), "未给出"),
                "time_why": _as_text(complexity.get("time_why")),
                "space_why": _as_text(complexity.get("space_why")),
            },
            "pitfalls": pitfalls,
        },
        "visual": visual,
        "code": {
            "language": _as_text(code.get("language"), "Python 3"),
            "source": code_source,
            "walkthrough": _as_text(code.get("walkthrough")),
        },
        "checks": checks,
        "takeaway": _as_text(data.get("takeaway")),
        "followups": followups,
        "sources": [{
            "label": "LeetCode 原题",
            "url": problem.get("official_url", ""),
        }],
        "meta": {
            "model": model,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "prompt_version": PROMPT_VERSION,
        },
    }


def load_demo_analysis(problem: dict, model: str = "demo") -> dict:
    path = Path(__file__).with_name("demo_analysis.json")
    data = json.loads(path.read_text())
    return normalize_analysis(data, problem, problem.get("topic_label", ""), model)


class AIClient:
    def __init__(self, settings: Settings, raw_dir: Path | None = None) -> None:
        self.settings = settings
        self.raw_dir = raw_dir or (settings.root / "data" / "daily" / "raw")
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _endpoint(self) -> str:
        base = self.settings.ai_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _post(
        self,
        messages: list[dict],
        json_mode: bool,
        disable_thinking: bool | None = None,
    ) -> tuple[str, dict, str]:
        if disable_thinking is None:
            disable_thinking = self.settings.ai_disable_thinking
        payload = {
            "model": self.settings.ai_model,
            "messages": messages,
            "temperature": self.settings.ai_temperature,
            "max_tokens": self.settings.ai_max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if disable_thinking:
            # DeepSeek V4 exposes a thinking switch. Structured JSON tasks do not
            # need reasoning tokens; if the endpoint rejects the field we retry
            # without it below.
            payload["thinking"] = {"type": "disabled"}
        body = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(
            self._endpoint(),
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.ai_api_key}",
                "User-Agent": "algo-daily/0.1",
            },
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.settings.ai_timeout) as resp:
                    raw_text = resp.read().decode("utf-8")
                response = json.loads(raw_text)
                content = (
                    response.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if not content:
                    raise AIError("AI 返回内容为空")
                usage = response.get("usage") or {}
                return content, usage, raw_text
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8")[:800]
                except Exception:  # noqa: BLE001
                    pass
                last_error = AIError(f"AI HTTP {exc.code}: {detail}")
                if exc.code == 400 and disable_thinking:
                    # Deployment rejected the thinking control; retry once
                    # without it, as the word-learning app does.
                    return self._post(messages, json_mode, disable_thinking=False)
                # Some OpenAI-compatible endpoints reject response_format.
                if exc.code == 400 and json_mode and "response_format" in detail:
                    return self._post(messages, json_mode=False, disable_thinking=disable_thinking)
                if exc.code in {429, 500, 502, 503, 504}:
                    time.sleep(2 ** attempt)
                    continue
                raise last_error
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = AIError(f"AI 请求失败：{exc}")
                time.sleep(2 ** attempt)
        raise last_error or AIError("AI 请求失败")

    @staticmethod
    def _repair_json(body: str, max_fixes: int = 6) -> Any | None:
        """Fix unbalanced closing brackets, the cheap models' most common JSON slip.

        At each decode error, try deleting or inserting one '}' / ']' near the
        error and keep the variant that parses furthest. Structural validation
        and code execution still run afterwards, so a wrong guess cannot slip
        through as published content.
        """
        text = body
        for _ in range(max_fixes):
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                pos = exc.pos
            best_text, best_pos = None, pos
            lo = max(0, pos - 16)
            for q in range(pos, lo - 1, -1):
                variants = [text[:q] + c + text[q:] for c in "}]"]
                if q < len(text) and text[q] in "}]":
                    variants.append(text[:q] + text[q + 1:])
                for cand in variants:
                    try:
                        return json.loads(cand)
                    except json.JSONDecodeError as exc2:
                        if exc2.pos > best_pos + 1:
                            best_text, best_pos = cand, exc2.pos
            if best_text is None:
                return None
            text = best_text
        return None

    @staticmethod
    def _parse_json(content: str) -> dict:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise AnalysisError("AI 输出中没有 JSON 对象")
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            body = text[start:end + 1]
            value = AIClient._repair_json(body)
            if isinstance(value, dict):
                value["_json_repaired"] = True
                return value
            near = body[max(0, exc.pos - 80):exc.pos + 40]
            raise AnalysisError(
                f"AI JSON 解析失败：{exc.msg}（第 {exc.pos} 个字符附近：…{near}…）。"
                "最常见原因是 frames 里某一帧的括号没有闭合：每一帧必须以 "
                "…}}} 结束（面板字段 } + state } + 帧 }），再接 , 或 ]。"
            ) from exc
        if not isinstance(value, dict):
            raise AnalysisError("AI JSON 顶层不是对象")
        return value

    def generate(
        self,
        problem: dict,
        topic_label: str,
        patterns: list[str],
        date_str: str,
    ) -> dict:
        if not self.settings.ai_configured:
            raise AIError(
                "未配置 AI_API_KEY。复制 .env.example 为 .env，填入 AI_API_KEY 后再运行；"
                "没有 key 可先用 `python3 scripts/run_daily.py --demo` 查看示例。"
            )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(problem, topic_label, patterns),
            },
        ]
        last_error: Exception | None = None
        content = ""
        for attempt in range(3):
            try:
                content, usage, raw_text = self._post(messages, json_mode=True)
                raw_path = self.raw_dir / f"{date_str}-{problem['slug']}-attempt{attempt + 1}.json"
                raw_path.write_text(raw_text)
                data = self._parse_json(content)
                repaired = bool(data.pop("_json_repaired", False))
                analysis = normalize_analysis(data, problem, topic_label, self.settings.ai_model)
                analysis["verification"] = verify_analysis(data, problem, self.settings.app.get("verify"))
                analysis["meta"]["json_repaired"] = repaired
                analysis["meta"]["usage"] = usage
                analysis["meta"]["attempt"] = attempt + 1
                return analysis
            except AnalysisError as exc:
                last_error = exc
                if not content:
                    continue
                # Feed the concrete validation error back so the retry fixes it
                # instead of regenerating blindly.
                messages = messages[:2] + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "上面的 JSON 没有通过校验，错误：" + str(exc) + "\n"
                            "请只修正出错的部分，其余内容保持不变，重新输出完整 JSON。"
                            "不要 markdown，不要解释。"
                        ),
                    },
                ]
        raise AIError(f"AI 分析失败：{last_error}")

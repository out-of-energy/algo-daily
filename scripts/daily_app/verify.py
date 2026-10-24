"""Execute the generated solution in a sandboxed subprocess before publishing.

Gate 1: the code must reproduce the official examples (inputs come from the
dataset, expected outputs are parsed from the statement) and agree with the
edge-case tests the model wrote from the traps it listed.
Gate 2: the input animated in ``visual.test`` must produce the result the
animation claims, and the last frame must state that result.

Any mismatch becomes an ``AnalysisError`` whose message tells the model the
input, the expected value and the actual value, so a retry can fix it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any

from .visual import AnalysisError

COMPARE_MODES = {"exact", "unordered", "unordered_deep", "float", "run_only"}
SUPPORTED_NODE_TYPES = {"ListNode", "ListNode[]", "TreeNode"}
AMBIGUOUS_PATTERNS = (
    r"return any (?:of them|valid|one)",
    r"any (?:of them|valid answer|valid solution)",
    r"multiple (?:valid )?(?:answers|solutions)",
    r"任意一个|任一|多个(?:有效)?答案",
)
ORDER_FREE_PATTERNS = (r"\bany order\b", r"任意顺序")

RUNNER = r'''
import io, json, resource, signal, sys

def _limits(cpu, mem):
    for res, val in ((resource.RLIMIT_CPU, cpu), (resource.RLIMIT_FSIZE, 1 << 20), (resource.RLIMIT_AS, mem)):
        try:
            resource.setrlimit(res, (val, val))
        except (ValueError, OSError):
            pass

payload = json.loads(sys.stdin.read())
_limits(int(payload["cpu"]), int(payload["mem"]))
real_stdout = sys.stdout
sys.stdout = io.StringIO()

PRELUDE = """
from typing import *
import collections, heapq, bisect, math, functools, itertools, string, re, operator, random
from collections import *
from heapq import *
from bisect import *
from functools import *
from itertools import *
from math import inf

class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
"""

ns = {"__name__": "solution"}
exec(PRELUDE, ns)

def to_list_node(values):
    head = cur = None
    for v in values or []:
        node = ns["ListNode"](v)
        if head is None:
            head = cur = node
        else:
            cur.next = node
            cur = node
    return head

def from_list_node(node):
    out, seen = [], 0
    while node is not None and seen < 10000:
        out.append(node.val)
        node = node.next
        seen += 1
    return out

def to_tree(values):
    if not values or values[0] is None:
        return None
    T = ns["TreeNode"]
    root = T(values[0])
    queue, i = [root], 1
    while queue and i < len(values):
        node = queue.pop(0)
        for side in ("left", "right"):
            if i < len(values) and values[i] is not None:
                child = T(values[i])
                setattr(node, side, child)
                queue.append(child)
            i += 1
    return root

def from_tree(root):
    out, queue = [], [root]
    while queue:
        node = queue.pop(0)
        if node is None:
            out.append(None)
            continue
        out.append(node.val)
        queue.append(node.left)
        queue.append(node.right)
    while out and out[-1] is None:
        out.pop()
    return out

def convert_in(value, typ):
    if typ == "ListNode":
        return to_list_node(value)
    if typ == "ListNode[]":
        return [to_list_node(v) for v in value]
    if typ == "TreeNode":
        return to_tree(value)
    return value

def convert_out(value):
    if isinstance(value, ns["ListNode"]):
        return from_list_node(value)
    if isinstance(value, ns["TreeNode"]):
        return from_tree(value)
    if isinstance(value, (list, tuple)):
        return [convert_out(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(convert_out(v) for v in value)
    if isinstance(value, dict):
        return {str(k): convert_out(v) for k, v in value.items()}
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return str(value)
    return value

class CaseTimeout(Exception):
    pass

def _alarm(signum, frame):
    raise CaseTimeout()

signal.signal(signal.SIGALRM, _alarm)
results = []
try:
    exec(compile(payload["code"], "solution.py", "exec"), ns)
    compiled = True
except BaseException as exc:
    compiled = False
    results = [{"id": c["id"], "ok": False, "error": f"代码无法编译/加载：{type(exc).__name__}: {exc}"} for c in payload["cases"]]

meta = payload["meta"]
timed_out = False
if compiled:
    for case in payload["cases"]:
        signal.setitimer(signal.ITIMER_REAL, float(payload["case_timeout"]))
        try:
            if meta.get("systemdesign"):
                cls = ns.get(meta["classname"])
                if cls is None:
                    raise NameError(f"代码里没有类 {meta['classname']}")
                ops, args = case["ops"], case["args"]
                obj, out = None, []
                for op, arg in zip(ops, args):
                    if op == meta["classname"]:
                        obj = cls(*arg)
                        out.append(None)
                    else:
                        out.append(convert_out(getattr(obj, op)(*arg)))
                value = out
            else:
                sol = ns.get("Solution")
                if sol is None:
                    raise NameError("代码里没有 class Solution")
                params = meta.get("params", [])
                args = [convert_in(a, p.get("type")) for a, p in zip(case["args"], params)]
                ret = getattr(sol(), meta["name"])(*args)
                rtype = (meta.get("return") or {}).get("type")
                out_idx = (meta.get("output") or {}).get("paramindex")
                if rtype == "void" and out_idx is not None:
                    ret = args[int(out_idx)]
                    rtype = params[int(out_idx)].get("type")
                if ret is None and rtype in ("ListNode", "TreeNode"):
                    ret = []
                value = convert_out(ret)
            json.dumps(value)
            results.append({"id": case["id"], "ok": True, "value": value})
        except CaseTimeout:
            results.append({"id": case["id"], "ok": False, "error": f"超时（>{payload['case_timeout']} 秒），可能有死循环"})
            timed_out = True
        except BaseException as exc:
            results.append({"id": case["id"], "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        if timed_out:
            break

real_stdout.write("\n@@RESULT@@" + json.dumps(results, ensure_ascii=False))
real_stdout.flush()
'''


# ---------------------------------------------------------------- helpers

def _short(value: Any, limit: int = 160) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit] + "…"


def _sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _deep_sort(value: Any) -> Any:
    if isinstance(value, list):
        return sorted((_deep_sort(v) for v in value), key=_sort_key)
    return value


def _float_equal(a: Any, b: Any, tol: float = 1e-5) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_float_equal(x, y, tol) for x, y in zip(a, b))
    return a == b


def _normalize(value: Any) -> Any:
    return json.loads(json.dumps(value))


def compare(expected: Any, actual: Any, mode: str, order_free: bool = False) -> bool:
    expected, actual = _normalize(expected), _normalize(actual)
    if mode == "run_only":
        return True
    if mode == "float":
        return _float_equal(expected, actual)
    if mode == "unordered" and isinstance(expected, list) and isinstance(actual, list):
        return sorted(expected, key=_sort_key) == sorted(actual, key=_sort_key)
    if mode == "unordered_deep":
        return _deep_sort(expected) == _deep_sort(actual)
    if expected == actual:
        return True
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) and not isinstance(expected, bool):
        if _float_equal(expected, actual):
            return True
    return order_free and _deep_sort(expected) == _deep_sort(actual)


def _has(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def supported(meta: Any) -> tuple[bool, str]:
    if not isinstance(meta, dict) or not meta:
        return False, "题目缺少 meta_data"
    if meta.get("manual"):
        return False, "题目使用特殊判题（manual）"
    if meta.get("systemdesign"):
        if not meta.get("classname"):
            return False, "设计题缺少 classname"
        return True, ""
    if not meta.get("name"):
        return False, "缺少方法名"
    types = [p.get("type", "") for p in meta.get("params", [])]
    types.append((meta.get("return") or {}).get("type", ""))
    for typ in types:
        if "Node" in typ and typ not in SUPPORTED_NODE_TYPES:
            return False, f"不支持的参数类型 {typ}"
    return True, ""


def parse_official(problem: dict) -> list[dict]:
    """Official example inputs from the dataset + outputs parsed from the statement."""
    meta = problem.get("meta_data") or {}
    statement = problem.get("statement") or {}
    lines = [ln.strip() for ln in (statement.get("examples") or "").splitlines() if ln.strip()]
    try:
        values = [json.loads(ln) for ln in lines]
    except json.JSONDecodeError:
        return []
    group = 2 if meta.get("systemdesign") else len(meta.get("params") or [])
    if not values or group == 0 or len(values) % group:
        return []
    cases = []
    for i in range(0, len(values), group):
        chunk = values[i:i + group]
        if meta.get("systemdesign"):
            if not (isinstance(chunk[0], list) and isinstance(chunk[1], list) and len(chunk[0]) == len(chunk[1])):
                return []
            cases.append({"ops": chunk[0], "args": chunk[1]})
        else:
            cases.append({"args": chunk})

    text = statement.get("text") or ""
    outputs = []
    for m in re.finditer(r"(?:Output|输出)\s*[:：]?\s*\n?\s*([^\n]+)", text):
        raw = m.group(1).strip()
        try:
            outputs.append(json.loads(raw))
        except json.JSONDecodeError:
            outputs = None
            break
    if outputs is not None and len(outputs) == len(cases):
        for case, out in zip(cases, outputs):
            case["expected"] = out
    return cases


def _model_case(meta: dict, raw: Any, where: str) -> dict:
    """Turn a model-written test (dict keyed by param name, or ops/args) into a runner case."""
    if not isinstance(raw, dict) or "input" not in raw or "expected" not in raw:
        raise AnalysisError(f"{where} 必须是 {{\"input\": ..., \"expected\": ..., \"why\": ...}}")
    inp = raw["input"]
    if meta.get("systemdesign"):
        if not (isinstance(inp, dict) and isinstance(inp.get("ops"), list) and isinstance(inp.get("args"), list)
                and len(inp["ops"]) == len(inp["args"])):
            raise AnalysisError(f"{where}.input 设计题必须写成 {{\"ops\": [...], \"args\": [[...], ...]}}，两者等长")
        if not inp["ops"] or inp["ops"][0] != meta["classname"]:
            raise AnalysisError(f"{where}.input.ops 第一个必须是构造函数 {meta['classname']!r}")
        return {"ops": inp["ops"], "args": inp["args"], "expected": raw["expected"]}
    names = [p.get("name") for p in meta.get("params", [])]
    if not isinstance(inp, dict) or set(inp) != set(names):
        raise AnalysisError(f"{where}.input 必须是按参数名写的对象，键恰好为 {names}，收到 {_short(inp, 80)}")
    return {"args": [inp[n] for n in names], "expected": raw["expected"]}


def _describe(case: dict, meta: dict) -> str:
    if meta.get("systemdesign"):
        return f"ops={_short(case['ops'], 120)} args={_short(case['args'], 120)}"
    names = [p.get("name") for p in meta.get("params", [])]
    return ", ".join(f"{n}={_short(v, 80)}" for n, v in zip(names, case["args"]))


def _mentions(text: str, value: Any) -> bool:
    squeeze = re.sub(r"\s+", "", text).lower()
    forms = {json.dumps(value, ensure_ascii=False, separators=(",", ":")).lower(), repr(value).replace(" ", "").lower()}
    if isinstance(value, str):
        forms.add(value.lower())
    return any(f and f in squeeze for f in forms)


# ---------------------------------------------------------------- execution

def run_cases(code: str, meta: dict, cases: list[dict], timeout: float = 10.0, case_timeout: float = 2.0) -> list[dict]:
    payload = {
        "code": code,
        "meta": meta,
        "cases": [{k: v for k, v in c.items() if k in {"id", "ops", "args"}} for c in cases],
        "case_timeout": case_timeout,
        "cpu": int(timeout) + 1,
        "mem": 1 << 30,
    }
    with tempfile.TemporaryDirectory(prefix="algo-verify-") as tmp:
        runner = os.path.join(tmp, "runner.py")
        with open(runner, "w") as fh:
            fh.write(RUNNER)
        env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
        try:
            proc = subprocess.run(
                [sys.executable, "-I", runner],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return [{"id": c["id"], "ok": False, "error": f"整体运行超过 {timeout} 秒"} for c in cases]
    marker = proc.stdout.rfind("@@RESULT@@")
    if marker < 0:
        detail = (proc.stderr or "").strip().splitlines()[-1:] or [f"退出码 {proc.returncode}"]
        return [{"id": c["id"], "ok": False, "error": f"运行崩溃：{detail[0][:200]}"} for c in cases]
    return json.loads(proc.stdout[marker + len("@@RESULT@@"):])


def verify_analysis(data: dict, problem: dict, settings_verify: dict | None = None) -> dict:
    """Run gates 1+2. Returns a report; raises AnalysisError with retry guidance on failure."""
    cfg = settings_verify or {}
    if not cfg.get("enabled", True):
        return {"status": "disabled"}
    meta = problem.get("meta_data") or {}
    ok, reason = supported(meta)
    if not ok:
        return {"status": "skipped", "reason": reason}

    code = ((data.get("code") or {}).get("source") or "").strip()
    text = (problem.get("statement") or {}).get("text") or ""
    ambiguous = _has(AMBIGUOUS_PATTERNS, text)
    order_free = _has(ORDER_FREE_PATTERNS, text)
    judge = data.get("judge") if isinstance(data.get("judge"), dict) else {}
    mode = str(judge.get("compare") or "exact").lower()
    if mode not in COMPARE_MODES:
        raise AnalysisError(f"judge.compare 只能是 {sorted(COMPARE_MODES)}，收到 {mode!r}")
    if ambiguous and mode == "exact":
        mode = "run_only"

    tests = data.get("tests")
    if not isinstance(tests, list) or len(tests) < 2:
        raise AnalysisError("tests 至少需要 2 个用例，覆盖 traps 里的边界情况")
    if len(tests) > 8:
        tests = tests[:8]
    visual_test = (data.get("visual") or {}).get("test")
    if visual_test is None:
        raise AnalysisError("visual.test 缺失：必须写出动画所用例子的结构化 input 和 expected")

    cases: list[dict] = []
    for i, case in enumerate(parse_official(problem)):
        case.update(id=f"official-{i + 1}", source="official", label=f"官方示例 {i + 1}")
        cases.append(case)
    for i, raw in enumerate(tests):
        case = _model_case(meta, raw, f"tests[{i}]")
        why = raw.get("why") if isinstance(raw, dict) else ""
        case.update(id=f"test-{i + 1}", source="model", label=f"tests[{i}]" + (f"（{why}）" if why else ""))
        cases.append(case)
    vcase = _model_case(meta, visual_test, "visual.test")
    vcase.update(id="visual", source="visual", label="visual.test（动画所用的例子）")
    cases.append(vcase)

    results = {r["id"]: r for r in run_cases(
        code, meta, cases,
        timeout=float(cfg.get("timeout_seconds", 10)),
        case_timeout=float(cfg.get("case_timeout_seconds", 2)),
    )}

    errors: list[str] = []
    seen_errors: set[str] = set()
    checked = {"official": 0, "model": 0, "visual": 0}
    for case in cases:
        res = results.get(case["id"]) or {"ok": False, "error": "未运行（前面的用例已超时或崩溃）"}
        desc = _describe(case, meta)
        if not res["ok"]:
            if res["error"] not in seen_errors and not res["error"].startswith("未运行"):
                seen_errors.add(res["error"])
                errors.append(f"{case['label']} 运行出错：输入 {desc}；错误 {res['error']}。请修正 code.source。")
            continue
        actual = res["value"]
        case["actual"] = actual
        if "expected" not in case:
            checked[case["source"]] += 1
            continue
        case_mode = "run_only" if ambiguous else mode
        if compare(case["expected"], actual, case_mode, order_free):
            checked[case["source"]] += 1
            continue
        if case["source"] == "official":
            errors.append(
                f"{case['label']} 不通过：输入 {desc}；题面期望 {_short(case['expected'])}，"
                f"代码实际输出 {_short(actual)}。代码有 bug，请修正 code.source（动画也要按新代码核对）。"
            )
        elif case["source"] == "model":
            errors.append(
                f"{case['label']} 不一致：输入 {desc}；你写的 expected={_short(case['expected'])}，"
                f"代码实际输出 {_short(actual)}。请按题意重新推算，判断是代码错还是 expected 错，并修正使两者一致。"
            )
        else:
            errors.append(
                f"动画例子不一致：输入 {desc}；visual.test.expected={_short(case['expected'])}，"
                f"代码实际输出 {_short(actual)}。说明动画是按错误的推演画的，请按代码真实执行重做 visual 各帧。"
            )

    if not errors and not meta.get("systemdesign") and mode != "run_only":
        frames = (data.get("visual") or {}).get("frames") or []
        last = frames[-1] if frames and isinstance(frames[-1], dict) else {}
        actual = vcase.get("actual")
        claim = f"{last.get('caption', '')} {last.get('badge', '')}"
        if len(_short(actual, 999)) <= 60 and not _mentions(claim, actual):
            errors.append(
                f"动画最后一帧没有写出返回值：代码对 visual.test 的实际输出是 {_short(actual)}，"
                "请在最后一帧的 badge 里写出它（如 return 9）。"
            )

    if errors:
        raise AnalysisError("代码执行校验未通过：\n- " + "\n- ".join(errors[:6]))
    return {
        "status": "passed" if mode != "run_only" else "partial",
        "compare": mode,
        "official": checked["official"],
        "official_with_expected": sum(1 for c in cases if c["source"] == "official" and "expected" in c),
        "tests": checked["model"],
        "visual": checked["visual"] == 1,
    }

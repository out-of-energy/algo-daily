"""Scene DSL (visual v2): validation, state resolution, layout and legacy upgrade.

A visual is a set of panels (array, map, stack, graph, ...) plus frames.
Each frame only writes the fields that change; ``normalize_visual`` resolves
them into full per-frame snapshots so renderers never have to diff or guess:

* persistent fields (values, pointers, window, done, visited, items, ...)
  carry over from the previous frame until rewritten;
* transient fields (active, bad, active_edges, deps) reset every frame;
* ``set`` patches single cells/values and the patch persists.
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

VERSION = 2

KINDS = {"array", "grid", "table", "linked_list", "graph", "map", "stack", "queue", "cards"}
KIND_ALIASES = {
    "tree": "graph",
    "list": "linked_list",
    "linkedlist": "linked_list",
    "hash": "map",
    "hashmap": "map",
    "dict": "map",
    "vars": "map",
    "counter": "map",
    "flow": "cards",
    "choices": "cards",
    "dp": "table",
    "matrix": "grid",
    "deque": "queue",
    "heap": "array",
}
WIDE_KINDS = {"array", "grid", "table", "linked_list", "graph"}
TONES = {"info", "decision", "success", "fail"}
LEGACY_TYPES = {"array", "grid", "linked_list", "graph", "tree", "table", "flow", "stack"}

MAX_PANELS = 4
MIN_FRAMES = 3
MAX_FRAMES = 16
MAX_ARRAY = 30
MAX_GRID = 12
MAX_TABLE_ROWS = 12
MAX_TABLE_COLS = 12
MAX_LIST = 12
MAX_GRAPH_NODES = 15
MAX_ITEMS = 24
MAX_CARDS = 6

# persistent / transient fields per kind (set is handled separately)
PERSISTENT = {
    "array": ("values", "pointers", "window", "done", "good"),
    "grid": ("grid", "visited", "path", "done", "good"),
    "table": ("rows", "done", "good"),
    "linked_list": ("nodes", "next", "pointers", "done", "good"),
    "graph": ("visited", "done", "good", "good_edges", "notes", "labels"),
    "map": ("items",),
    "stack": ("items",),
    "queue": ("items",),
    "cards": ("done", "good"),
}
TRANSIENT = {
    "array": ("active", "bad"),
    "grid": ("active", "bad"),
    "table": ("active", "bad", "deps"),
    "linked_list": ("active", "bad"),
    "graph": ("active", "bad", "active_edges"),
    "map": ("active", "bad", "good"),
    "stack": ("active", "bad", "good"),
    "queue": ("active", "bad", "good"),
    "cards": ("active", "bad"),
}
HIGHLIGHT_FIELDS = {"active", "bad", "deps", "active_edges"}  # plus "good" on map/stack/queue
EXTRA_FIELDS = {"array": {"set"}, "grid": {"set"}, "table": {"set"}}


class AnalysisError(RuntimeError):
    pass


# ---------------------------------------------------------------- primitives

def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _cell(value: Any) -> str:
    """Display text for one cell/value. Short JSON for nested structures."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float, str)):
        return str(value).strip() if isinstance(value, str) else str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


class _Ctx:
    """Error collector: strict mode raises, lenient (legacy) mode drops.

    ``soft`` is used for highlight-only fields: a bad highlight is dropped and
    recorded as a warning instead of failing the whole generation.
    """

    def __init__(self, strict: bool, warnings: list[str] | None = None) -> None:
        self.strict = strict
        self.warnings = warnings if warnings is not None else []

    def fail(self, where: str, message: str) -> None:
        if self.strict:
            raise AnalysisError(f"visual {where}: {message}")
        self.warnings.append(f"{where}: {message}")

    @property
    def soft(self) -> "_Ctx":
        return _Ctx(False, self.warnings)


def _indices(ctx: _Ctx, where: str, value: Any, size: int) -> list[int]:
    out: list[int] = []
    for item in _list(value):
        n = _int(item)
        if n is None or not 0 <= n < size:
            ctx.fail(where, f"下标 {item!r} 越界（有效范围 0..{size - 1}）")
            continue
        if n not in out:
            out.append(n)
    return out


def _cells(ctx: _Ctx, where: str, value: Any, rows: int, cols_of) -> list[list[int]]:
    out: list[list[int]] = []
    items = _list(value)
    if len(items) == 2 and all(_int(x) is not None for x in items):
        items = [items]  # a single bare [r, c] is unambiguous
    for item in items:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            ctx.fail(where, f"单元格必须写成 [r, c]，收到 {item!r}")
            continue
        r, c = _int(item[0]), _int(item[1])
        if r is None or c is None or not 0 <= r < rows or not 0 <= c < cols_of(r):
            ctx.fail(where, f"单元格 {item!r} 越界")
            continue
        if [r, c] not in out:
            out.append([r, c])
    return out


def _keys(value: Any) -> list[str]:
    out = []
    for item in _list(value):
        key = _cell(item)
        if key not in out:
            out.append(key)
    return out


# ---------------------------------------------------------------- panel init

def _kind(raw: Any) -> str:
    kind = _text(raw).lower().replace("-", "_")
    return KIND_ALIASES.get(kind, kind)


def _init_panel(ctx: _Ctx, index: int, raw: Any) -> dict:
    raw = _dict(raw)
    raw_kind = _text(raw.get("kind") or raw.get("type")).lower()
    kind = _kind(raw_kind)
    where = f"panels[{index}]"
    if kind not in KINDS:
        raise AnalysisError(f"{where}.kind 不支持：{raw_kind!r}；允许：{sorted(KINDS)}")
    pid = _text(raw.get("id")) or f"p{index}"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,23}", pid):
        raise AnalysisError(f"{where}.id 只能用字母数字下划线：{pid!r}")
    panel: dict[str, Any] = {"id": pid, "kind": kind, "label": _text(raw.get("label"))}

    if kind == "array":
        values = [_cell(x) for x in _list(raw.get("values"))]
        if len(values) > MAX_ARRAY:
            raise AnalysisError(f"{where}.values 最多 {MAX_ARRAY} 个，收到 {len(values)}")
        panel["values"] = values
        panel["labels"] = [_cell(x) for x in _list(raw.get("labels"))][:MAX_ARRAY]
    elif kind == "grid":
        grid = [[_cell(c) for c in row] for row in _list(raw.get("grid")) if isinstance(row, list)]
        if not grid:
            raise AnalysisError(f"{where}.grid 不能为空")
        if len(grid) > MAX_GRID or max(len(r) for r in grid) > MAX_GRID:
            raise AnalysisError(f"{where}.grid 最大 {MAX_GRID}x{MAX_GRID}")
        panel["grid"] = grid
        panel["row_labels"] = [_cell(x) for x in _list(raw.get("row_labels"))][:MAX_GRID]
        panel["col_labels"] = [_cell(x) for x in _list(raw.get("col_labels"))][:MAX_GRID]
    elif kind == "table":
        rows = [[_cell(c) for c in row] for row in _list(raw.get("rows")) if isinstance(row, list)]
        if not rows:
            raise AnalysisError(f"{where}.rows 不能为空")
        if len(rows) > MAX_TABLE_ROWS or max(len(r) for r in rows) > MAX_TABLE_COLS:
            raise AnalysisError(f"{where}.rows 最多 {MAX_TABLE_ROWS} 行 {MAX_TABLE_COLS} 列")
        panel["headers"] = [_cell(x) for x in _list(raw.get("headers"))][:MAX_TABLE_COLS]
        panel["rows"] = rows
    elif kind == "linked_list":
        nodes = [_cell(x) for x in _list(raw.get("nodes"))]
        if not nodes:
            raise AnalysisError(f"{where}.nodes 不能为空")
        if len(nodes) > MAX_LIST:
            raise AnalysisError(f"{where}.nodes 最多 {MAX_LIST} 个")
        panel["nodes"] = nodes
    elif kind == "graph":
        _init_graph(ctx, where, raw, panel, raw_kind == "tree")
    elif kind == "map":
        panel["items"] = {_cell(k): _cell(v) for k, v in _dict(raw.get("items")).items()}
    elif kind in {"stack", "queue"}:
        panel["items"] = [_cell(x) for x in _list(raw.get("items"))][:MAX_ITEMS]
    elif kind == "cards":
        cards = []
        for item in _list(raw.get("items"))[:MAX_CARDS]:
            if isinstance(item, dict):
                cards.append({"title": _text(item.get("title"), "步骤"), "detail": _text(item.get("detail"))})
            elif _text(item):
                cards.append({"title": _text(item), "detail": ""})
        if len(cards) < 2:
            raise AnalysisError(
                f"{where}.items 至少需要 2 张卡片；cards 内容固定不变，"
                "会随帧变化的列表请改用 array 面板（每帧写 values）"
            )
        panel["items"] = cards
    return panel


def _init_graph(ctx: _Ctx, where: str, raw: dict, panel: dict, is_tree: bool) -> None:
    nodes = []
    seen = set()
    for item in _list(raw.get("nodes")):
        if isinstance(item, dict):
            nid = _cell(item.get("id")) if item.get("id") is not None else ""
            label = _cell(item.get("label")) if item.get("label") is not None else nid
            x, y = item.get("x"), item.get("y")
        else:
            nid = label = _cell(item)
            x = y = None
        if not nid or nid in seen:
            continue
        seen.add(nid)
        node = {"id": nid, "label": label}
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
            node["x"], node["y"] = float(x), float(y)
        nodes.append(node)
    if not nodes:
        raise AnalysisError(f"{where}.nodes 不能为空")
    if len(nodes) > MAX_GRAPH_NODES:
        raise AnalysisError(f"{where}.nodes 最多 {MAX_GRAPH_NODES} 个")
    layout = _text(raw.get("layout")).lower()
    if layout not in {"tree", "layered", "circle", "manual"}:
        if all("x" in n for n in nodes):
            layout = "manual"
        elif is_tree or _looks_like_tree(nodes, raw.get("edges")):
            layout = "tree"
        else:
            layout = "circle"

    edges = []
    for i, item in enumerate(_list(raw.get("edges"))):
        if isinstance(item, dict):
            a, b, extra = item.get("from"), item.get("to"), item.get("label")
        elif isinstance(item, (list, tuple)) and len(item) in (2, 3):
            a, b = item[0], item[1]
            extra = item[2] if len(item) == 3 else None
        else:
            ctx.fail(f"{where}.edges[{i}]", "边必须写成 [from, to] 或 [from, to, label]")
            continue
        a, b = _cell(a), _cell(b)
        if a not in seen or b not in seen:
            ctx.fail(f"{where}.edges[{i}]", f"边 {a}->{b} 引用了不存在的节点")
            continue
        edge = {"from": a, "to": b, "label": "", "side": ""}
        extra_text = _cell(extra) if extra is not None else ""
        if layout == "tree" and extra_text.upper() in {"L", "R"}:
            edge["side"] = extra_text.upper()
        else:
            edge["label"] = extra_text
        edges.append(edge)
    panel["directed"] = bool(raw.get("directed", layout in {"tree", "layered"}))
    panel["layout"] = layout
    panel["nodes"] = nodes
    panel["edges"] = edges
    _layout_graph(panel)


# ---------------------------------------------------------------- graph layout

def _looks_like_tree(nodes: list[dict], raw_edges: Any) -> bool:
    """Single root, every other node has exactly one parent: a rooted tree."""
    ids = {n["id"] for n in nodes}
    parent: dict[str, str] = {}
    count = 0
    for item in _list(raw_edges):
        if isinstance(item, dict):
            a, b = item.get("from"), item.get("to")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            a, b = item[0], item[1]
        else:
            return False
        a, b = _cell(a), _cell(b)
        if a not in ids or b not in ids or a == b or b in parent:
            return False
        parent[b] = a
        count += 1
    return len(ids) >= 3 and count == len(ids) - 1


def _node_radius(label: str) -> float:
    return max(19.0, min(34.0, len(label) * 4.4 + 9))


def _text_px(text: str, ascii_px: float = 6.8, wide_px: float = 11.5) -> float:
    return sum(wide_px if ord(ch) > 0x2E80 else ascii_px for ch in text)


def _layout_graph(panel: dict, note_px: float = 0.0) -> None:
    nodes, edges, layout = panel["nodes"], panel["edges"], panel["layout"]
    for node in nodes:
        node["r"] = _node_radius(node["label"])
    pos: dict[str, tuple[float, float]] | None = None
    min_unit = max(2 * max(n["r"] for n in nodes) + 12, note_px + 10)
    if layout == "manual":
        pos = {n["id"]: (n["x"], n["y"]) for n in nodes}
    elif layout == "tree":
        pos = _tree_positions(nodes, edges, min_unit)
    elif layout == "layered":
        pos = _layered_positions(nodes, edges)
    if pos is None:
        panel["layout"] = "circle" if layout != "manual" else layout
        pos = _circle_positions(nodes)
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    pad = 34
    min_x, min_y = min(xs), min(ys)
    for node in nodes:
        x, y = pos[node["id"]]
        node["x"] = round(x - min_x + pad, 1)
        node["y"] = round(y - min_y + pad, 1)
    panel["width"] = round(max(xs) - min_x + pad * 2, 1)
    panel["height"] = round(max(ys) - min_y + pad * 2 + 14, 1)


def _circle_positions(nodes: list[dict]) -> dict[str, tuple[float, float]]:
    n = len(nodes)
    radius = max(70.0, 26 * n / math.pi + 30)
    return {
        node["id"]: (
            radius + math.cos(-math.pi / 2 + 2 * math.pi * i / max(n, 1)) * radius,
            radius + math.sin(-math.pi / 2 + 2 * math.pi * i / max(n, 1)) * radius,
        )
        for i, node in enumerate(nodes)
    }


def _tree_positions(nodes: list[dict], edges: list[dict], min_unit: float = 40.0) -> dict[str, tuple[float, float]] | None:
    ids = [n["id"] for n in nodes]
    children: dict[str, list[tuple[str, str]]] = {i: [] for i in ids}
    parent: dict[str, str] = {}
    for edge in edges:
        a, b = edge["from"], edge["to"]
        if b in parent or a == b:
            return None
        parent[b] = a
        children[a].append((b, edge["side"]))
    for kids in children.values():
        kids.sort(key=lambda kid: {"L": 0, "": 1, "R": 2}[kid[1]])
    roots = [i for i in ids if i not in parent]
    if not roots:
        return None
    xs: dict[str, float] = {}
    depth: dict[str, int] = {}
    cursor = [0.0]
    visiting: set[str] = set()
    # Binary trees (edges marked L/R): one column per node in in-order, so a
    # lone right child never collides with the left sibling's subtree.
    binary = any(side for kids in children.values() for _, side in kids)

    def place(node: str, d: int) -> bool:
        if node in visiting or node in xs:
            return False
        visiting.add(node)
        depth[node] = d
        kids = children[node]
        if binary:
            left = [k for k, side in kids if side == "L"]
            right = [k for k, side in kids if side == "R"]
            free = [k for k, side in kids if not side]
            if not left and free:
                left.append(free.pop(0))
            right.extend(free)
            for kid in left:
                if not place(kid, d + 1):
                    return False
            xs[node] = cursor[0]
            cursor[0] += 1
            for kid in right:
                if not place(kid, d + 1):
                    return False
        else:
            for kid, _ in kids:
                if not place(kid, d + 1):
                    return False
            if not kids:
                xs[node] = cursor[0]
                cursor[0] += 1
            else:
                xs[node] = (xs[kids[0][0]] + xs[kids[-1][0]]) / 2
        visiting.discard(node)
        return True

    for root in roots:
        if not place(root, 0):
            return None
        cursor[0] += 0.5
    if len(xs) != len(ids):
        return None
    span = max(xs.values()) - min(xs.values()) or 1
    unit = max(min_unit, min(64.0, 600 / span))
    level = max(72.0, min_unit + 26)
    return {i: (xs[i] * unit, depth[i] * level) for i in ids}


def _layered_positions(nodes: list[dict], edges: list[dict]) -> dict[str, tuple[float, float]] | None:
    ids = [n["id"] for n in nodes]
    indeg = {i: 0 for i in ids}
    out: dict[str, list[str]] = {i: [] for i in ids}
    for edge in edges:
        out[edge["from"]].append(edge["to"])
        indeg[edge["to"]] += 1
    level = {i: 0 for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    seen = 0
    while queue:
        node = queue.pop(0)
        seen += 1
        for nxt in out[node]:
            level[nxt] = max(level[nxt], level[node] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen != len(ids):
        return None
    by_level: dict[int, list[str]] = {}
    for i in ids:
        by_level.setdefault(level[i], []).append(i)
    widest = max(len(v) for v in by_level.values())
    pos = {}
    for lv, members in by_level.items():
        offset = (widest - len(members)) / 2
        for k, node in enumerate(members):
            pos[node] = ((k + offset) * 84.0, lv * 76.0)
    return pos


# ---------------------------------------------------------------- frame state

def _initial_state(panel: dict) -> dict:
    kind = panel["kind"]
    if kind == "array":
        return {"values": list(panel["values"]), "pointers": {}, "window": None, "done": [], "good": []}
    if kind == "grid":
        return {"grid": copy.deepcopy(panel["grid"]), "visited": [], "path": [], "done": [], "good": []}
    if kind == "table":
        return {"rows": copy.deepcopy(panel["rows"]), "done": [], "good": []}
    if kind == "linked_list":
        n = len(panel["nodes"])
        return {
            "nodes": list(panel["nodes"]),
            "next": [i + 1 if i + 1 < n else None for i in range(n)],
            "pointers": {},
            "done": [],
            "good": [],
        }
    if kind == "graph":
        return {"visited": [], "done": [], "good": [], "good_edges": [], "notes": {}, "labels": {}}
    if kind == "map":
        return {"items": dict(panel["items"])}
    if kind in {"stack", "queue"}:
        return {"items": list(panel["items"])}
    return {"done": [], "good": []}


def _edge_index(panel: dict, edge: Any) -> int | None:
    if not (isinstance(edge, (list, tuple)) and len(edge) >= 2):
        return None
    a, b = _cell(edge[0]), _cell(edge[1])
    for i, item in enumerate(panel["edges"]):
        if item["from"] == a and item["to"] == b:
            return i
    if not panel["directed"]:
        for i, item in enumerate(panel["edges"]):
            if item["from"] == b and item["to"] == a:
                return i
    return None


def _apply(ctx: _Ctx, where: str, panel: dict, prev: dict, raw: dict) -> dict:
    kind = panel["kind"]
    allowed = set(PERSISTENT[kind]) | set(TRANSIENT[kind]) | EXTRA_FIELDS.get(kind, set())
    for field in raw:
        if field not in allowed:
            hint = "；cards 内容固定不变，会随帧变化的列表请改用 array 面板（每帧写 values）" if kind == "cards" else ""
            ctx.fail(f"{where}.{field}", f"{kind} 面板不支持帧字段 {field!r}，可用字段：{sorted(allowed)}{hint}")
    state = copy.deepcopy(prev)
    for field in TRANSIENT[kind]:
        state[field] = []

    def has(field: str) -> bool:
        return field in raw

    def hc(field: str) -> _Ctx:
        soft = field in HIGHLIGHT_FIELDS or (field == "good" and kind in {"map", "stack", "queue"})
        return ctx.soft if soft else ctx

    if kind == "array":
        if has("values"):
            values = [_cell(x) for x in _list(raw["values"])]
            if len(values) > MAX_ARRAY:
                ctx.fail(f"{where}.values", f"最多 {MAX_ARRAY} 个")
                values = values[:MAX_ARRAY]
            state["values"] = values
        for i, item in enumerate(_list(raw.get("set"))):
            if isinstance(item, (list, tuple)) and len(item) == 2:
                idx = _int(item[0])
                if idx is not None and 0 <= idx < len(state["values"]):
                    state["values"][idx] = _cell(item[1])
                    continue
            ctx.fail(f"{where}.set[{i}]", f"应为 [下标, 值] 且下标有效，收到 {item!r}")
        size = len(state["values"])
        if has("pointers"):
            state["pointers"] = _pointers(ctx, f"{where}.pointers", raw["pointers"], size, allow_null=False)
        else:
            state["pointers"] = {k: v for k, v in state["pointers"].items() if v < size}
        if has("window"):
            window = raw["window"]
            if window is None or window == []:
                state["window"] = None
            else:
                pair = [_int(x) for x in _list(window)]
                if len(pair) == 2 and None not in pair and 0 <= min(pair) and max(pair) < size:
                    state["window"] = [min(pair), max(pair)]
                else:
                    ctx.fail(f"{where}.window", f"应为 [left, right] 闭区间且在 0..{size - 1} 内，收到 {window!r}")
                    state["window"] = None
        for field in ("done", "good", "active", "bad"):
            if has(field):
                state[field] = _indices(hc(field), f"{where}.{field}", raw[field], size)
            elif field in PERSISTENT[kind]:
                state[field] = [i for i in state[field] if i < size]
    elif kind in {"grid", "table"}:
        key = "grid" if kind == "grid" else "rows"
        if has(key):
            matrix = [[_cell(c) for c in row] for row in _list(raw[key]) if isinstance(row, list)]
            if matrix:
                state[key] = matrix
            else:
                ctx.fail(f"{where}.{key}", "不能为空")
        matrix = state[key]
        for i, item in enumerate(_list(raw.get("set"))):
            if isinstance(item, (list, tuple)) and len(item) == 3:
                r, c = _int(item[0]), _int(item[1])
                if r is not None and c is not None and 0 <= r < len(matrix) and 0 <= c < len(matrix[r]):
                    matrix[r][c] = _cell(item[2])
                    continue
            ctx.fail(f"{where}.set[{i}]", f"应为 [r, c, 值] 且单元格有效，收到 {item!r}")
        cols = lambda r: len(matrix[r])  # noqa: E731
        fields = ("visited", "path", "done", "good", "active", "bad") if kind == "grid" else ("done", "good", "active", "bad", "deps")
        for field in fields:
            if has(field):
                state[field] = _cells(hc(field), f"{where}.{field}", raw[field], len(matrix), cols)
    elif kind == "linked_list":
        if has("nodes"):
            nodes = [_cell(x) for x in _list(raw["nodes"])][:MAX_LIST]
            if nodes:
                if len(nodes) != len(state["nodes"]) and not has("next"):
                    state["next"] = [i + 1 if i + 1 < len(nodes) else None for i in range(len(nodes))]
                state["nodes"] = nodes
        size = len(state["nodes"])
        if has("next"):
            nxt = _list(raw["next"])
            if len(nxt) != size:
                ctx.fail(f"{where}.next", f"长度必须等于节点数 {size}，每项是下标或 null")
            parsed = []
            for i in range(size):
                item = nxt[i] if i < len(nxt) else None
                n = _int(item)
                if item is not None and (n is None or not 0 <= n < size):
                    ctx.fail(f"{where}.next[{i}]", f"{item!r} 不是有效下标")
                    n = None
                parsed.append(n)
            state["next"] = parsed
        if has("pointers"):
            state["pointers"] = _pointers(ctx, f"{where}.pointers", raw["pointers"], size, allow_null=True)
        for field in ("done", "good", "active", "bad"):
            if has(field):
                state[field] = _indices(hc(field), f"{where}.{field}", raw[field], size)
    elif kind == "graph":
        ids = {n["id"] for n in panel["nodes"]}
        for field in ("visited", "done", "good", "active", "bad"):
            if has(field):
                vals = []
                for item in _list(raw[field]):
                    nid = _cell(item)
                    if nid not in ids:
                        hc(field).fail(f"{where}.{field}", f"节点 {nid!r} 不存在")
                    elif nid not in vals:
                        vals.append(nid)
                state[field] = vals
        for field in ("active_edges", "good_edges"):
            if has(field):
                vals = []
                for item in _list(raw[field]):
                    idx = _edge_index(panel, item)
                    if idx is None:
                        hc(field).fail(f"{where}.{field}", f"边 {item!r} 不在 edges 里")
                    elif idx not in vals:
                        vals.append(idx)
                state[field] = vals
        for field in ("notes", "labels"):
            if has(field):
                vals = {}
                for k, v in _dict(raw[field]).items():
                    if _cell(k) in ids:
                        vals[_cell(k)] = _cell(v)
                    else:
                        hc(field).fail(f"{where}.{field}", f"节点 {k!r} 不存在")
                state[field] = vals
    elif kind == "map":
        if has("items"):
            if isinstance(raw["items"], dict):
                for k, v in raw["items"].items():
                    if isinstance(v, dict):
                        ctx.fail(f"{where}.items.{k}", "map 的值不能是对象；字典状态（如 dp、计数表）请单独开一个 map 面板")
                state["items"] = {_cell(k): _cell(v) for k, v in raw["items"].items()}
            else:
                ctx.fail(f"{where}.items", "map 的 items 必须是对象")
        keys = set(state["items"])
        for field in ("active", "bad", "good"):
            if has(field):
                vals = []
                for key in _keys(raw[field]):
                    if key in keys:
                        vals.append(key)
                    else:
                        hc(field).fail(f"{where}.{field}", f"key {key!r} 不在当前 items 里")
                state[field] = vals
    elif kind in {"stack", "queue"}:
        if has("items"):
            state["items"] = [_cell(x) for x in _list(raw["items"])][:MAX_ITEMS]
        size = len(state["items"])
        for field in ("active", "bad", "good"):
            if has(field):
                state[field] = _indices(hc(field), f"{where}.{field}", raw[field], size)
    else:  # cards
        size = len(panel["items"])
        for field in ("done", "good", "active", "bad"):
            if has(field):
                state[field] = _indices(hc(field), f"{where}.{field}", raw[field], size)
    return state


def _pointers(ctx: _Ctx, where: str, value: Any, size: int, allow_null: bool) -> dict:
    if not isinstance(value, dict):
        ctx.fail(where, "pointers 必须是对象，如 {\"L\": 0}")
        return {}
    out: dict[str, int | None] = {}
    for name, item in value.items():
        if item is None:
            if allow_null:
                out[_cell(name)] = None
            continue
        n = _int(item)
        if n is None or not 0 <= n < size:
            hint = "数组当前为空，这一帧不要放指针" if size == 0 else "越界的指针请省略"
            ctx.fail(f"{where}.{name}", f"{item!r} 越界（当前长度 {size}；{hint}）")
            continue
        out[_cell(name)] = n
    return out


def _lines(ctx: _Ctx, where: str, value: Any, code: list[str] | None) -> list[int]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    out = []
    for item in raw:
        n = None
        is_snippet = isinstance(item, str) and not re.fullmatch(r"\s*\d+\s*", item)
        if ctx.strict and not is_snippet:
            # Models miscount line numbers; only verifiable snippets are accepted.
            ctx.fail(where, f"line 必须是 code.source 中的代码片段文字，不能写行号：{item!r}")
            continue
        if is_snippet and code is not None:
            needle = item.strip()
            squeeze = re.sub(r"\s+", "", needle)
            hits = [i + 1 for i, line in enumerate(code) if squeeze and squeeze in re.sub(r"\s+", "", line)]
            if len(hits) != 1:
                ctx.fail(where, f"代码片段 {needle!r} 在 code.source 中出现 {len(hits)} 次，必须恰好 1 次")
                continue
            n = hits[0]
        elif not is_snippet:
            n = _int(item)
        if n is None or n < 1 or (code is not None and (n > len(code) or not code[n - 1].strip())):
            ctx.fail(where, f"{item!r} 不是有效的代码行")
            continue
        if n not in out:
            out.append(n)
    return out


# ---------------------------------------------------------------- public API

def normalize_visual(value: Any, code_source: str | None = None) -> dict:
    """Validate a visual and resolve every frame into full panel snapshots."""
    value = _dict(value)
    if value.get("version") == VERSION and value.get("resolved"):
        return value
    strict = "panels" in value
    if not strict:
        value = upgrade_legacy(value)
    ctx = _Ctx(strict)
    raw_panels = _list(value.get("panels"))
    if not raw_panels:
        raise AnalysisError("visual.panels 不能为空")
    if len(raw_panels) > MAX_PANELS:
        raise AnalysisError(f"visual.panels 最多 {MAX_PANELS} 个，收到 {len(raw_panels)}")
    panels = [_init_panel(ctx, i, p) for i, p in enumerate(raw_panels)]
    ids = [p["id"] for p in panels]
    if len(set(ids)) != len(ids):
        raise AnalysisError(f"visual.panels 的 id 重复：{ids}")
    by_id = {p["id"]: p for p in panels}

    frames_raw = _list(value.get("frames"))
    min_frames = MIN_FRAMES if strict else 2
    if len(frames_raw) < min_frames:
        raise AnalysisError(f"visual.frames 至少需要 {min_frames} 帧")
    if len(frames_raw) > MAX_FRAMES:
        if strict:
            raise AnalysisError(f"visual.frames 最多 {MAX_FRAMES} 帧，收到 {len(frames_raw)}；请合并平淡步骤")
        frames_raw = frames_raw[:30]

    code_lines = code_source.splitlines() if code_source else None
    current = {p["id"]: _initial_state(p) for p in panels}
    frames = []
    for i, raw in enumerate(frames_raw):
        raw = _dict(raw)
        where = f"frames[{i}]"
        caption = _text(raw.get("caption"))
        if not caption:
            if strict:
                raise AnalysisError(f"visual {where}.caption 不能为空")
            caption = "继续观察状态变化"
        state_raw = _dict(raw.get("state"))
        for pid in state_raw:
            if pid not in by_id:
                ctx.fail(f"{where}.state", f"面板 id {pid!r} 不存在；可用：{ids}")
        resolved = {}
        for pid, panel in by_id.items():
            current[pid] = _apply(ctx, f"{where}.state.{pid}", panel, current[pid], _dict(state_raw.get(pid)))
            resolved[pid] = copy.deepcopy(current[pid])
        tone = _text(raw.get("tone")).lower()
        frame = {
            "caption": caption,
            "badge": _text(raw.get("badge")),
            "tone": tone if tone in TONES else "info",
            "lines": _lines(ctx, f"{where}.line", raw.get("line", raw.get("lines")), code_lines),
            "state": resolved,
        }
        if strict and frames and frames[-1]["caption"] == caption:
            raise AnalysisError(f"visual {where}: 与上一帧 caption 完全相同，请合并或写清楚差异")
        frames.append(frame)

    for panel in panels:
        if panel["kind"] == "graph" and panel["layout"] != "manual":
            note_px = max(
                (_text_px(text) for f in frames for text in f["state"][panel["id"]]["notes"].values()),
                default=0.0,
            )
            if note_px:
                _layout_graph(panel, note_px)

    if strict and code_lines is not None and sum(1 for f in frames if f["lines"]) * 2 < len(frames):
        raise AnalysisError("visual: 超过一半的帧缺少 line，请为每帧标出正在执行的代码片段")
    if strict and frames[-1]["tone"] not in {"success", "fail"}:
        raise AnalysisError("visual: 最后一帧必须给出返回值/最终结果，tone 写 success 或 fail")

    return {
        "version": VERSION,
        "resolved": True,
        "title": _text(value.get("title"), "解题动画"),
        "example": _text(value.get("example")),
        "panels": panels,
        "frames": frames,
        "warnings": ctx.warnings[:20] if strict else [],
    }


def ensure_visual(visual: Any, code_source: str | None = None) -> dict:
    """Upgrade stored analyses (legacy or v2) right before rendering."""
    visual = _dict(visual)
    if visual.get("version") == VERSION and visual.get("resolved"):
        return visual
    return normalize_visual(visual, code_source)


def upgrade_legacy(value: dict) -> dict:
    """Convert the v1 single-type visual into an equivalent v2 scene."""
    vtype = _text(value.get("type")).lower()
    if vtype not in LEGACY_TYPES:
        raise AnalysisError(f"visual.type 不支持：{vtype!r}；新格式请使用 panels")
    data = _dict(value.get("data"))
    frames_in = _list(value.get("frames"))
    panels: list[dict] = []
    frames: list[dict] = []

    def base(frame: dict) -> dict:
        frame = _dict(frame)
        return {"caption": frame.get("caption"), "badge": frame.get("badge")}

    if vtype == "array":
        values = _list(data.get("values"))
        if not values:
            raise AnalysisError("array.data.values 不能为空")
        panels.append({"id": "arr", "kind": "array", "values": values[:MAX_ARRAY],
                       "labels": _list(data.get("labels"))})
        has_map = any(isinstance(_dict(f).get("map"), dict) for f in frames_in)
        if has_map:
            panels.append({"id": "map", "kind": "map", "label": "状态"})
        for f in frames_in:
            f = _dict(f)
            out = base(f)
            out["state"] = {"arr": {
                "pointers": _dict(f.get("pointers")),
                "window": f.get("window") if isinstance(f.get("window"), list) else None,
                "active": _list(f.get("active")),
                "done": _list(f.get("done")),
            }}
            if has_map:
                out["state"]["map"] = {"items": _dict(f.get("map"))}
            frames.append(out)
    elif vtype == "grid":
        panels.append({"id": "grid", "kind": "grid", "grid": _list(data.get("grid"))[:MAX_GRID],
                       "row_labels": data.get("row_labels"), "col_labels": data.get("col_labels")})
        for f in frames_in:
            f = _dict(f)
            out = base(f)
            out["state"] = {"grid": {k: _list(f.get(k)) for k in ("active", "visited", "path")}}
            frames.append(out)
    elif vtype == "linked_list":
        panels.append({"id": "list", "kind": "linked_list", "nodes": _list(data.get("nodes"))[:MAX_LIST]})
        for f in frames_in:
            f = _dict(f)
            out = base(f)
            out["state"] = {"list": {
                "pointers": _dict(f.get("pointers")),
                "active": _list(f.get("active")),
                "done": _list(f.get("done")),
                "good": _list(f.get("reversed")),
            }}
            frames.append(out)
    elif vtype in {"graph", "tree"}:
        panels.append({"id": "g", "kind": "tree" if vtype == "tree" else "graph",
                       "nodes": data.get("nodes"), "edges": data.get("edges"),
                       "directed": data.get("directed", False)})
        for f in frames_in:
            f = _dict(f)
            out = base(f)
            out["state"] = {"g": {
                "active": _list(f.get("active_nodes")),
                "visited": _list(f.get("visited")),
                "active_edges": _list(f.get("active_edges")),
            }}
            frames.append(out)
    elif vtype == "table":
        panels.append({"id": "dp", "kind": "table", "headers": data.get("headers"),
                       "rows": _list(data.get("rows"))[:MAX_TABLE_ROWS]})
        for f in frames_in:
            f = _dict(f)
            out = base(f)
            out["state"] = {"dp": {"active": _list(f.get("active_cells")), "done": _list(f.get("done_cells"))}}
            frames.append(out)
    elif vtype == "flow":
        panels.append({"id": "flow", "kind": "cards", "items": _list(data.get("stages"))[:MAX_CARDS]})
        for f in frames_in:
            f = _dict(f)
            out = base(f)
            idx = _int(f.get("active_stage"))
            out["state"] = {"flow": {"active": [idx] if idx is not None else []}}
            frames.append(out)
    else:  # stack
        inputs = _list(data.get("input"))
        if inputs:
            panels.append({"id": "input", "kind": "array", "label": "输入", "values": inputs[:MAX_ARRAY]})
        panels.append({"id": "stack", "kind": "stack", "label": "stack"})
        has_queue = any(_list(_dict(f).get("queue")) for f in frames_in)
        if has_queue:
            panels.append({"id": "queue", "kind": "queue", "label": "queue"})
        for f in frames_in:
            f = _dict(f)
            out = base(f)
            stack = _list(f.get("stack"))
            active = _text(f.get("active"))
            top = [len(stack) - 1] if stack and active and _cell(stack[-1]) == active else []
            out["state"] = {"stack": {"items": stack, "active": top}}
            if has_queue:
                out["state"]["queue"] = {"items": _list(f.get("queue"))}
            frames.append(out)
    return {"title": value.get("title"), "panels": panels, "frames": frames}

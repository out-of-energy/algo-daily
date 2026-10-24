"""Email composition and SMTP delivery."""

from __future__ import annotations

import html
import re
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any

from .config import Settings
from .visual import ensure_visual

DIFFICULTY_ZH = {"Easy": "简单", "Medium": "中等", "Hard": "困难"}
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


class EmailError(RuntimeError):
    pass


MONO = "Menlo,Consolas,monospace"
TONE_COLOR = {"decision": "#f4d06f", "success": "#2a8f7f", "fail": "#ef6b6b", "info": "#3b4b5e"}


def _box(value: Any, state: str = "", width: int = 38, height: int = 40, size: int = 15) -> str:
    bg, color, border, opacity = "#172334", "#e8eef7", "#3b4b5e", "1"
    if state == "window":
        bg, border = "#1e3a5f", "#3b82f6"
    elif state == "done":
        color, opacity = "#7a8da3", "0.55"
    elif state == "good":
        bg, color, border = "#14342f", "#b9f5e8", "#2a8f7f"
    elif state == "dep":
        bg, border = "#1e3a5f", "#3b82f6"
    elif state == "active":
        bg, color, border = "#f4d06f", "#17212b", "#f4d06f"
    elif state == "bad":
        bg, color, border = "#5b2323", "#ffd9d9", "#ef6b6b"
    return (
        f'<div style="min-width:{width}px;height:{height}px;line-height:{height}px;padding:0 4px;'
        f'border:1px solid {border};border-radius:7px;background:{bg};color:{color};opacity:{opacity};'
        f'font:{size}px {MONO};text-align:center">{e(value)}</div>'
    )


def _pick(index: Any, st: dict, order: tuple[str, ...]) -> str:
    for key in order:
        if key == "window":
            w = st.get("window")
            if isinstance(w, list) and len(w) == 2 and w[0] <= index <= w[1]:
                return key
            continue
        if index in (st.get(key) or []):
            return key
    return ""


def _static_array(panel: dict, st: dict) -> str:
    values = (st.get("values") or [])[:16]
    pointer_by_index: dict[int, list[str]] = {}
    for name, idx in (st.get("pointers") or {}).items():
        if isinstance(idx, int):
            pointer_by_index.setdefault(idx, []).append(str(name))
    cells = []
    for i, value in enumerate(values):
        state = _pick(i, st, ("active", "bad", "good", "done", "window"))
        cells.append(
            f'<td align="center" valign="bottom" style="padding:0 3px">'
            f'<div style="height:15px;color:#f4d06f;font:11px {MONO}">{e(" ".join(pointer_by_index.get(i, [])))}</div>'
            f'{_box(value, state)}'
            f'<div style="height:15px;color:#7e91a7;font:10px {MONO}">{i}</div></td>'
        )
    return f'<table role="presentation" cellpadding="0" cellspacing="0" align="center"><tr>{"".join(cells)}</tr></table>'


def _static_matrix(panel: dict, st: dict, is_grid: bool) -> str:
    matrix = (st.get("grid") if is_grid else st.get("rows")) or []
    head = ""
    headers = [] if is_grid else (panel.get("headers") or [])
    if headers:
        head = "<tr>" + "".join(
            f'<td style="padding:2px 4px;color:#7f91a7;font:11px {MONO};text-align:center">{e(h)}</td>'
            for h in headers[:10]
        ) + "</tr>"
    order = ("active", "bad", "good", "path", "visited", "deps", "done")
    sets = {k: {tuple(x) for x in (st.get(k) or [])} for k in order}
    body = []
    for r, row in enumerate(matrix[:10]):
        cells = []
        for c, value in enumerate(row[:10]):
            state = next((k for k in order if (r, c) in sets[k]), "")
            state = {"path": "good", "visited": "good", "deps": "dep"}.get(state, state)
            if not is_grid and c == 0 and not state:
                cells.append(f'<td style="padding:2px 6px;color:#7f91a7;font:11px {MONO}">{e(value)}</td>')
            else:
                cells.append(f'<td style="padding:2px">{_box(value, state, 30, 30, 12)}</td>')
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f'<table role="presentation" cellpadding="0" cellspacing="0" align="center">{head}{"".join(body)}</table>'


def _static_linked(panel: dict, st: dict) -> str:
    nodes = (st.get("nodes") or [])[:10]
    nxt = st.get("next") or []
    cells = []
    for i, value in enumerate(nodes):
        state = _pick(i, st, ("active", "bad", "good", "done"))
        cells.append(f'<td style="padding:0 2px">{_box(value, state)}</td>')
        j = nxt[i] if i < len(nxt) else None
        arrow = "→" if j == i + 1 else ("←" if j == i - 1 else ("∅" if j is None else f"→{j}"))
        cells.append(f'<td style="padding:0 3px;color:#7f91a7;font:13px {MONO}">{e(arrow)}</td>')
    pointers = " · ".join(
        f"{name}={'None' if idx is None else nodes[idx] if idx < len(nodes) else idx}"
        for name, idx in (st.get("pointers") or {}).items()
    )
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" align="center"><tr>{"".join(cells)}</tr></table>'
        + (f'<div style="margin-top:6px;color:#f4d06f;font:11px {MONO};text-align:center">{e(pointers)}</div>' if pointers else "")
    )


def _static_graph(panel: dict, st: dict) -> str:
    pills = []
    labels = st.get("labels") or {}
    notes = st.get("notes") or {}
    for node in (panel.get("nodes") or [])[:15]:
        nid = node["id"]
        state = _pick(nid, st, ("active", "bad", "good", "visited", "done"))
        bg, color = {
            "active": ("#f4d06f", "#17212b"), "bad": ("#5b2323", "#ffd9d9"),
            "good": ("#14342f", "#b9f5e8"), "visited": ("#14342f", "#dbe6f4"),
        }.get(state, ("#172334", "#dbe6f4"))
        note = f" · {notes[nid]}" if nid in notes else ""
        pills.append(
            f'<span style="display:inline-block;margin:3px;padding:5px 10px;border-radius:999px;'
            f'background:{bg};color:{color};font:11px {MONO}">{e(labels.get(nid, node.get("label")))}{e(note)}</span>'
        )
    return f'<div style="text-align:center">{"".join(pills)}</div>'


def _static_map(panel: dict, st: dict) -> str:
    items = st.get("items") or {}
    active = set(st.get("active") or [])
    bad = set(st.get("bad") or [])
    if not items:
        return f'<div style="color:#7f91a7;font:12px {MONO};text-align:center">空</div>'
    chips = []
    for key, value in list(items.items())[:16]:
        border = "#ef6b6b" if key in bad else ("#f4d06f" if key in active else "#34455a")
        chips.append(
            f'<span style="display:inline-block;margin:3px;padding:5px 10px;border:1px solid {border};'
            f'border-radius:8px;background:#132032;color:#9fb0c4;font:12px {MONO}">{e(key)} '
            f'<b style="color:#f4d06f">{e(value)}</b></span>'
        )
    return f'<div style="text-align:center">{"".join(chips)}</div>'


def _static_seq(panel: dict, st: dict, is_queue: bool) -> str:
    items = st.get("items") or []
    if not items:
        return f'<div style="color:#7f91a7;font:12px {MONO};text-align:center">空</div>'
    cells = "".join(
        f'<td style="padding:0 2px">{_box(v, _pick(i, st, ("active", "bad")), 34, 30, 12)}</td>'
        for i, v in enumerate(items[:14])
    )
    hint = "队头 → 队尾" if is_queue else "栈底 → 栈顶"
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" align="center"><tr>{cells}</tr></table>'
        f'<div style="margin-top:4px;color:#5f7187;font:10px {MONO};text-align:center">{hint}</div>'
    )


def _static_cards(panel: dict, st: dict) -> str:
    rows = []
    for i, card in enumerate((panel.get("items") or [])[:6]):
        state = _pick(i, st, ("active", "bad", "good", "done"))
        border = {"active": "#f4d06f", "bad": "#ef6b6b", "good": "#2a8f7f"}.get(state, "#34455a")
        mark = {"good": " ✓", "bad": " ✗"}.get(state, "")
        rows.append(
            f'<tr><td style="padding:3px 0"><div style="padding:8px 12px;border:1px solid {border};'
            f'border-radius:7px;background:#132032;color:#dbe6f4;font:12px -apple-system,sans-serif">'
            f'<b style="color:#f4d06f">{i + 1}. {e(card.get("title"))}{mark}</b><br>'
            f'<span style="color:#9fb0c4">{e(card.get("detail"))}</span></div></td></tr>'
        )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(rows)}</table>'


def static_visual(analysis: dict) -> str:
    """One representative frame (the first key decision, else the result) as email-safe HTML."""
    try:
        visual = ensure_visual(analysis.get("visual"), (analysis.get("code") or {}).get("source"))
    except Exception:  # noqa: BLE001 - email must never fail because of the visual
        return ""
    frames = visual.get("frames") or []
    if not frames:
        return ""
    index = next((i for i, f in enumerate(frames) if f.get("tone") == "decision"), len(frames) - 1)
    frame = frames[index]
    blocks = []
    for panel in visual.get("panels") or []:
        st = (frame.get("state") or {}).get(panel["id"]) or {}
        kind = panel["kind"]
        if kind == "array":
            inner = _static_array(panel, st)
        elif kind in {"grid", "table"}:
            inner = _static_matrix(panel, st, kind == "grid")
        elif kind == "linked_list":
            inner = _static_linked(panel, st)
        elif kind == "graph":
            inner = _static_graph(panel, st)
        elif kind == "map":
            inner = _static_map(panel, st)
        elif kind in {"stack", "queue"}:
            inner = _static_seq(panel, st, kind == "queue")
        else:
            inner = _static_cards(panel, st)
        label = panel.get("label") or kind
        blocks.append(
            f'<div style="padding:8px 4px 10px"><div style="margin-bottom:6px;color:#7f91a7;'
            f'font:10px {MONO};letter-spacing:.06em">{e(label)}</div>{inner}</div>'
        )
    tone = TONE_COLOR.get(frame.get("tone") or "info", "#3b4b5e")
    badge = frame.get("badge") or ""
    badge_html = (
        f'<span style="float:right;color:{tone};font:10px {MONO}">{e(badge)}</span>' if badge else ""
    )
    return (
        '<div style="border:1px solid #263241;border-radius:12px;background:#0f1722;padding:12px 10px 10px">'
        f'<div style="padding:0 6px 4px;color:#9fb0c4;font:10px {MONO};letter-spacing:.08em">'
        f'{badge_html}关键一步 · 第 {index + 1}/{len(frames)} 帧</div>'
        f'{"".join(blocks)}'
        f'<div style="margin:6px 8px 2px;padding:4px 10px;border-left:3px solid {tone};color:#c4d0de;'
        f'font:12px -apple-system,sans-serif;line-height:1.7">{e(frame.get("caption"))}</div>'
        "</div>"
    )


def build_email_html(analysis: dict, problem: dict, day: date, public_url: str = "", demo: bool = False) -> str:
    topic = analysis["problem"].get("topic_label", "")
    difficulty = DIFFICULTY_ZH.get(problem.get("difficulty"), problem.get("difficulty", ""))
    date_text = f"{day.year}年{day.month}月{day.day}日 · {WEEKDAYS[day.weekday()]}"
    hook = analysis.get("hook") or ""
    plain = analysis["real_meaning"].get("plain") or ""
    analogy = analysis["real_meaning"].get("analogy") or ""
    approach = analysis.get("approach") or {}
    complexity = approach.get("complexity") or {}
    title = problem.get("title_zh") or problem.get("title") or ""
    official = problem.get("official_url") or "#"
    cta = (
        f'<a href="{e(public_url)}" style="display:inline-block;padding:12px 20px;border-radius:9px;'
        f'background:#17212b;color:#fff;text-decoration:none;font-size:14px;font-weight:700">'
        f'打开完整动画页 →</a>'
        if public_url else
        '<div style="padding:12px 14px;border-radius:9px;background:#f0eadc;color:#5f6875;'
        'font-size:12px;line-height:1.7">完整可交互页面在附件 HTML 里，用浏览器打开即可播放动画。</div>'
    )
    demo_html = (
        '<div style="margin:0 0 16px;padding:8px 12px;border:1px dashed #c7bda9;border-radius:7px;'
        'color:#7a6b53;font-size:11px">示例预览 · 配置 AI 后会换成每日随机题</div>'
        if demo else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · 算法日报</title></head>
<body style="margin:0;padding:0;background:#f4f1e8;color:#17212b;
font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">每天一道，今天看 {e(title)}。{e(hook)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f1e8">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#fffdf8;
border:1px solid #ded6c7;border-radius:16px;overflow:hidden">
  <tr><td style="padding:30px 30px 20px;background:#17212b;color:#f8f5ed">
    <div style="color:#f4d06f;font:11px Menlo,monospace;letter-spacing:.12em">ALGORITHM DAILY / {day.strftime('%Y.%m.%d')}</div>
    <div style="margin-top:14px;color:#9fb0c4;font-size:12px">{e(date_text)} · {e(topic)} · {e(difficulty)}</div>
    <h1 style="margin:8px 0 0;font-size:31px;line-height:1.25;letter-spacing:-.03em">{e(title)}</h1>
    <div style="margin-top:8px;color:#8595a9;font-size:12px">{e(problem.get('title'))}</div>
  </td></tr>
  <tr><td style="padding:26px 30px 6px">
    {demo_html}
    <div style="padding-left:14px;border-left:3px solid #f4d06f;font-size:18px;line-height:1.6;font-weight:650">{e(hook)}</div>
    <p style="margin:18px 0 0;font-size:14px;line-height:1.85;color:#3f4a57">{e(plain)}</p>
    {f'<p style="margin:10px 0 0;font-size:12px;line-height:1.7;color:#7a8492"><b>生活类比：</b>{e(analogy)}</p>' if analogy else ''}
  </td></tr>
  <tr><td style="padding:18px 30px 0">{static_visual(analysis)}</td></tr>
  <tr><td style="padding:20px 30px 4px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="50%" style="padding:10px 12px;background:#f7f2e7;border-radius:8px 0 0 8px">
        <div style="color:#7a8492;font-size:10px;letter-spacing:.08em">TIME</div>
        <div style="font:600 18px Menlo,monospace">{e(complexity.get('time'))}</div></td>
      <td width="50%" style="padding:10px 12px;background:#f7f2e7;border-radius:0 8px 8px 0">
        <div style="color:#7a8492;font-size:10px;letter-spacing:.08em">SPACE</div>
        <div style="font:600 18px Menlo,monospace">{e(complexity.get('space'))}</div></td>
    </tr></table>
    <p style="margin:16px 0 0;font-size:13px;line-height:1.8;color:#3f4a57">
      <b>{e(approach.get('name'))}</b>：{e(approach.get('one_line'))}
    </p>
    <div style="margin:18px 0 4px">{cta}</div>
  </td></tr>
  <tr><td style="padding:20px 30px 26px">
    <div style="padding-top:14px;border-top:1px solid #e5ddcf;color:#8a8173;font-size:11px;line-height:1.8">
      每天一道，不求刷完，只求真的看懂一道。<br>
      题目来源：<a href="{e(official)}" style="color:#5f6875">LeetCode</a> · 个人学习用途，不公开分发题面。<br>
      AI 生成内容可能有误，请以原题与本地测试为准。
    </div>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def build_plain_text(analysis: dict, problem: dict, day: date, public_url: str = "") -> str:
    approach = analysis.get("approach") or {}
    complexity = approach.get("complexity") or {}
    lines = [
        f"算法日报 · {day.isoformat()}",
        f"{(problem.get('title_zh') or problem.get('title'))} ({problem.get('difficulty')})",
        "",
        analysis.get("hook") or "",
        "",
        analysis["real_meaning"].get("plain") or "",
        "",
        f"思路：{approach.get('name')} — {approach.get('one_line')}",
        f"复杂度：时间 {complexity.get('time')} / 空间 {complexity.get('space')}",
    ]
    if public_url:
        lines.append("")
        lines.append(f"完整动画页：{public_url}")
    else:
        lines.append("")
        lines.append("完整可交互 HTML 见邮件附件。")
    lines.append("")
    lines.append(f"原题：{problem.get('official_url')}")
    return "\n".join(lines)


class Emailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _subject(self, problem: dict, day: date, analysis: dict) -> str:
        prefix = self.settings.app.get("email", {}).get("subject_prefix", "算法日报")
        title = problem.get("title_zh") or problem.get("title")
        topic = analysis["problem"].get("topic_label", "")
        return f"[{prefix}] {day.strftime('%m-%d')} {title} · {topic}"

    def compose(
        self,
        analysis: dict,
        problem: dict,
        page_html: str,
        day: date,
        public_url: str = "",
        demo: bool = False,
        to_override: list[str] | None = None,
        from_override: str = "",
    ) -> EmailMessage:
        recipients = to_override or self.settings.mail_to
        sender = from_override or self.settings.mail_from
        if not recipients:
            raise EmailError("MAIL_TO 未配置")
        if not sender:
            raise EmailError("MAIL_FROM / SMTP_USER 未配置")
        msg = EmailMessage()
        msg["Subject"] = self._subject(problem, day, analysis)
        msg["From"] = formataddr((self.settings.app_name, sender))
        msg["To"] = ", ".join(recipients)
        msg["Message-ID"] = make_msgid(domain="algo-daily.local")
        msg["X-Mailer"] = "algo-daily/0.1"
        msg.set_content(build_plain_text(analysis, problem, day, public_url))
        msg.add_alternative(
            build_email_html(analysis, problem, day, public_url, demo),
            subtype="html",
        )
        if self.settings.app.get("email", {}).get("attach_html", True):
            filename = f"{day.isoformat()}-{problem.get('slug')}.html"
            msg.add_attachment(
                page_html.encode("utf-8"),
                maintype="text",
                subtype="html",
                filename=filename,
            )
        return msg

    def send(
        self,
        analysis: dict,
        problem: dict,
        page_html: str,
        day: date,
        dry_run: bool | None = None,
        public_url: str = "",
        demo: bool = False,
    ) -> dict:
        dry = self.settings.mail_dry_run if dry_run is None else dry_run
        recipients = self.settings.mail_to
        if not recipients and not dry:
            raise EmailError("MAIL_TO 未配置；先复制 .env.example 为 .env 并填写邮箱")
        if not recipients:
            recipients = ["demo@localhost"]
        sender = self.settings.mail_from or "demo@localhost"
        msg = self.compose(
            analysis, problem, page_html, day, public_url, demo,
            to_override=recipients,
            from_override=sender,
        )
        if dry:
            outbox = self.settings.root / "data" / "daily" / "outbox"
            outbox.mkdir(parents=True, exist_ok=True)
            path = outbox / f"{day.isoformat()}{'-demo' if demo else ''}.eml"
            path.write_bytes(bytes(msg))
            return {
                "status": "dry-run",
                "path": str(path),
                "subject": msg["Subject"],
                "to": recipients,
            }

        missing = self.settings.missing_for_send()
        if missing:
            raise EmailError("邮件配置缺失：" + ", ".join(missing))
        context = ssl.create_default_context()
        try:
            if self.settings.smtp_security == "ssl":
                server = smtplib.SMTP_SSL(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=30,
                    context=context,
                )
            else:
                server = smtplib.SMTP(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=30,
                )
                if self.settings.smtp_security == "starttls":
                    server.starttls(context=context)
            with server:
                if self.settings.smtp_user:
                    server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            raise EmailError(f"SMTP 发送失败：{exc}") from exc
        return {
            "status": "sent",
            "path": "",
            "subject": msg["Subject"],
            "to": recipients,
        }

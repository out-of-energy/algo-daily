"""Render the daily standalone HTML page and archive."""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path
from typing import Any

from .config import Settings
from .visual import ensure_visual

STATIC = Path(__file__).with_name("static")
DIFFICULTY_ZH = {"Easy": "简单", "Medium": "中等", "Hard": "困难"}
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def p(value: Any) -> str:
    return f"<p>{esc(value)}</p>"


def bullet_list(items: list[Any]) -> str:
    if not items:
        return ""
    return "<ul class=\"bullet-list\">" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>"


def source_links(analysis: dict) -> str:
    links = []
    for source in analysis.get("sources") or []:
        url = str(source.get("url") or "")
        label = str(source.get("label") or "来源")
        if url.startswith("http://") or url.startswith("https://"):
            links.append(
                f'<a class="pill-link" href="{esc(url)}" target="_blank" rel="noopener">'
                f'{esc(label)} ↗</a>'
            )
    return "".join(links)


def verification_badge(report: Any) -> str:
    if not isinstance(report, dict):
        return ""
    status = report.get("status")
    if status == "passed":
        official = int(report.get("official_with_expected") or 0)
        parts = [f"官方示例 {official} 个"] if official else []
        parts.append(f"边界用例 {int(report.get('tests') or 0)} 个")
        text = "✓ 代码已在沙箱中实际运行：" + " + ".join(parts) + " 全部通过，动画结论与运行结果一致"
        return f'<div class="verify-note ok">{esc(text)}</div>'
    if status == "partial":
        return '<div class="verify-note ok">✓ 代码已实际运行通过；本题允许多个合法答案，结果未逐值比对</div>'
    if status == "skipped":
        reason = report.get("reason") or ""
        return f'<div class="verify-note">未自动验证（{esc(reason)}），请以本地测试为准</div>'
    return ""


def render_page(
    settings: Settings,
    problem: dict,
    analysis: dict,
    day: date,
    demo: bool = False,
) -> str:
    css = (STATIC / "daily.css").read_text()
    js = (STATIC / "daily.js").read_text()
    visual = ensure_visual(analysis.get("visual"), (analysis.get("code") or {}).get("source"))
    visual_json = json.dumps(visual, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    example_html = (
        f'<div class="visual-example"><b>示例</b>{esc(visual["example"])}</div>'
        if visual.get("example") else ""
    )
    verify_html = verification_badge(analysis.get("verification"))
    topic = analysis["problem"].get("topic_label") or settings.topic_label(problem.get("primary_topic"))
    accent = settings.topic_accent(problem.get("primary_topic"))
    difficulty = problem.get("difficulty", "")
    difficulty_zh = DIFFICULTY_ZH.get(difficulty, difficulty)
    ac_rate = problem.get("ac_rate")
    ac_text = f"{ac_rate:.1f}% 通过率" if isinstance(ac_rate, (int, float)) else "通过率未知"
    model = (analysis.get("meta") or {}).get("model", "AI")
    generated_at = (analysis.get("meta") or {}).get("generated_at", "")
    date_text = f"{day.year}年{day.month}月{day.day}日 · {WEEKDAYS[day.weekday()]}"
    day_num = day.timetuple().tm_yday
    hook = analysis.get("hook") or analysis.get("takeaway") or ""
    plain = analysis["real_meaning"]["plain"]
    analogy = analysis["real_meaning"].get("analogy") or ""
    input_output = analysis["real_meaning"].get("input_output") or "把题面里的变量和返回值对应起来。"
    test = analysis["real_meaning"].get("test") or ""
    traps = analysis["real_meaning"].get("traps") or []
    approach = analysis["approach"]
    complexity = approach["complexity"]
    code = analysis["code"]
    checks = analysis.get("checks") or []
    followups = analysis.get("followups") or []
    takeaway = analysis.get("takeaway") or ""
    brand_href = "index.html" if demo else "../index.html"
    demo_banner = (
        '<div class="demo-banner">这是内置示例精读，用于预览页面与动画效果；'
        '配置 AI key 后，每天会换成随机题目和真实生成内容。</div>'
        if demo else ""
    )
    checks_html = "".join(
        f'<details class="check"><summary>{esc(item["q"])}</summary>'
        f'<p>{esc(item["a"])}</p></details>'
        for item in checks
    ) or '<p class="empty-state">没有额外自检题，写完代码后手动跑三组边界用例。</p>'
    followups_html = ""
    if followups:
        followups_html = (
            '<div class="section-head"><span class="section-num">07 / 延伸</span>'
            '<h2>如果题目再变一下</h2></div><div class="followups">'
            + "".join(
                f'<div class="followup"><b>{esc(x.get("title"))}</b>'
                f'<p>{esc(x.get("idea"))}</p></div>'
                for x in followups
            )
            + "</div>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(problem.get('title_zh') or problem.get('title'))} · {esc(settings.app_name)}</title>
<meta name="description" content="{esc(hook)}">
<style>{css}</style>
</head>
<body style="--accent:{esc(accent)}">
  <div class="wrap">
    <header class="topbar">
      <a class="brand" href="{brand_href}"><span class="brand-mark">A</span>{esc(settings.app_name)}</a>
      <span class="topbar-right">DAY {day_num:03d} · {esc(date_text)}</span>
    </header>

    <main>
      <section class="hero" style="border-top:0">
        {demo_banner}
        <div class="eyebrow">
          <span class="dot"></span>
          <span>{esc(date_text)}</span>
          <span>·</span>
          <span class="topic-chip">{esc(topic)}</span>
          <span>·</span>
          <span>题目 {esc(problem.get('frontend_id'))}</span>
        </div>
        <h1>{esc(problem.get('title_zh') or problem.get('title'))}</h1>
        <p class="en-title">{esc(problem.get('title'))}</p>
        <p class="hook">{esc(hook)}</p>
        <div class="meta-row">
          <span class="chip diff">{esc(difficulty_zh)}</span>
          <span class="chip">{esc(ac_text)}</span>
          <span class="chip">代码 <strong>Python 3</strong></span>
          <a class="pill-link" href="{esc(problem.get('official_url'))}" target="_blank" rel="noopener">打开 LeetCode 原题 ↗</a>
        </div>
      </section>

      <section id="meaning">
        <div class="section-head"><span class="section-num">01 / 先说人话</span><h2>这题到底在问什么</h2></div>
        <div class="meaning">
          <span class="meaning-label">PLAIN MEANING</span>
          <p>{esc(plain)}</p>
          {f'<div class="analogy"><strong>生活类比：</strong>{esc(analogy)}</div>' if analogy else ''}
        </div>
        <div class="translation-grid">
          <div class="note-card"><h3>输入 → 输出</h3><p>{esc(input_output)}</p></div>
          <div class="note-card"><h3>它真正考什么</h3><p>{esc(test)}</p></div>
        </div>
      </section>

      <section id="test">
        <div class="section-head"><span class="section-num">02 / 审题</span><h2>先看懂考点，再动手</h2></div>
        <div class="split">
          <div class="panel">
            <h3>最容易误解的地方</h3>
            {bullet_list(traps) if traps else '<p class="empty-state">题面没有额外陷阱。</p>'}
          </div>
          <div class="panel">
            <h3>读完题目先问自己</h3>
            <p>{esc(analysis['real_meaning'].get('test') or '这道题的限制条件是什么？能不能把重复计算变成一次状态更新？')}</p>
          </div>
        </div>
      </section>

      <section id="visual">
        <div class="section-head"><span class="section-num">03 / 看见它</span><h2>{esc(visual.get('title') or '一步步看它怎么跑')}</h2></div>
        <div class="section-sub">按「播放」自动走，也可以按 ← → 自己控制。右侧高亮的是这一帧正在执行的代码。</div>
        {verify_html}
        {example_html}
        <div class="visual-shell" id="visual-shell" data-tone="info">
          <div class="visual-head">
            <h3>STATE MACHINE</h3>
            <span class="visual-badge" id="visual-badge"></span>
          </div>
          <div class="visual-body">
            <div class="visual-stage" id="visual-stage"></div>
            <aside class="visual-code" id="visual-code" aria-label="当前执行的代码" hidden></aside>
          </div>
          <div class="visual-caption" id="visual-caption" aria-live="polite"></div>
          <div class="visual-controls">
            <button type="button" id="visual-prev" aria-label="上一帧">←</button>
            <button type="button" class="play" id="visual-play">播放</button>
            <button type="button" id="visual-next" aria-label="下一帧">→</button>
            <div class="visual-progress"><span id="visual-progress-bar"></span></div>
            <span class="visual-counter" id="visual-counter"></span>
            <button type="button" class="speed" id="visual-speed" aria-label="播放速度">1x</button>
          </div>
        </div>
      </section>

      <section id="approach">
        <div class="section-head"><span class="section-num">04 / 推理</span><h2>{esc(approach.get('name') or '解题思路')}</h2></div>
        <div class="section-sub">{esc(approach.get('one_line') or '')}</div>
        <div class="split">
          <ol class="steps">
            {''.join(f'<li><p>{esc(step)}</p></li>' for step in approach.get('steps', []))}
          </ol>
          <div>
            <div class="why-box">
              <h3>WHY IT WORKS</h3>
              <p>{esc(approach.get('why_it_works') or '')}</p>
            </div>
            <div class="complexity-row">
              <div class="complexity"><span>TIME</span><b>{esc(complexity.get('time'))}</b><small>{esc(complexity.get('time_why'))}</small></div>
              <div class="complexity"><span>SPACE</span><b>{esc(complexity.get('space'))}</b><small>{esc(complexity.get('space_why'))}</small></div>
            </div>
            <div class="panel" style="margin-top:14px">
              <h3>实现时最容易写错</h3>
              {bullet_list(approach.get('pitfalls') or []) or '<p class="empty-state">写完先跑空输入和重复元素。</p>'}
            </div>
          </div>
        </div>
      </section>

      <section id="code">
        <div class="section-head"><span class="section-num">05 / 代码</span><h2>把状态翻译成循环</h2></div>
        <div class="code-shell">
          <div class="code-head"><span>{esc(code.get('language') or 'Python 3')}</span><button class="copy-btn" type="button" data-copy-target="solution-code">复制代码</button></div>
          <pre class="code" id="solution-code"><code>{esc(code.get('source'))}</code></pre>
        </div>
        <p class="code-walkthrough">{esc(code.get('walkthrough') or '')}</p>
      </section>

      <section id="checks">
        <div class="section-head"><span class="section-num">06 / 自检</span><h2>不看答案，先问自己</h2></div>
        <div class="check-grid">{checks_html}</div>
        {followups_html}
      </section>

      <section id="takeaway" style="border-top:0;padding-top:10px">
        <div class="takeaway">
          <small>TAKEAWAY</small>
          <p>{esc(takeaway)}</p>
        </div>
      </section>
    </main>

    <footer class="footer">
      <div class="footer-row">
        <div>{source_links(analysis)}</div>
        <div>生成于 {esc(generated_at[:19].replace('T', ' '))} UTC · 模型 {esc(model)}</div>
      </div>
      <div class="fine">
        本页由 AI 根据 LeetCode 免费题面生成，用于个人学习；代码与复杂度请以原题约束和本地测试为准。
        题面版权归 LeetCode 所有。
      </div>
    </footer>
  </div>
  <script id="visual-data" type="application/json">{visual_json}</script>
  <script>{js}</script>
</body>
</html>
"""


def render_archive(settings: Settings, entries: list[dict]) -> str:
    rows = []
    for entry in sorted(entries, key=lambda x: x.get("date", ""), reverse=True):
        date_str = entry.get("date", "")
        title = entry.get("title_zh") or entry.get("slug") or "未命名"
        topic = entry.get("topic_label") or settings.topic_label(entry.get("topic"))
        diff = DIFFICULTY_ZH.get(entry.get("difficulty"), entry.get("difficulty") or "")
        rows.append(
            f'<a class="entry" href="day/{esc(date_str)}.html">'
            f'<span class="entry-date">{esc(date_str)}</span>'
            f'<span class="entry-title">{esc(title)}</span>'
            f'<span class="entry-meta">{esc(topic)} · {esc(diff)}</span>'
            f'<span class="entry-arrow">↗</span></a>'
        )
    body = "".join(rows) or (
        '<p class="empty">还没有每日记录。运行 '
        '<code>python3 scripts/run_daily.py --demo</code> 生成第一份预览，或 '
        '<a href="demo.html">先看看示例页 →</a></p>'
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(settings.app_name)} · 归档</title>
<style>
  body {{ margin:0; background:#f5f1e8; color:#17212b; font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif; }}
  .wrap {{ width:min(860px,calc(100% - 32px)); margin:0 auto; padding:48px 0 70px; }}
  .brand {{ font-size:12px; letter-spacing:.12em; color:#6b7280; text-transform:uppercase; }}
  h1 {{ margin:14px 0 8px; font-size:clamp(38px,7vw,72px); letter-spacing:-.055em; line-height:1.05; }}
  .sub {{ color:#6b7280; margin:0 0 34px; }}
  .entry {{ display:grid; grid-template-columns:100px 1fr auto 22px; align-items:center; gap:16px; padding:18px 0; border-top:1px solid #ded6c7; text-decoration:none; color:inherit; }}
  .entry:hover .entry-title {{ color:#2563eb; }}
  .entry-date {{ font-family:ui-monospace,monospace; font-size:12px; color:#6b7280; }}
  .entry-title {{ font-weight:700; font-size:18px; }}
  .entry-meta {{ font-size:12px; color:#6b7280; }}
  .entry-arrow {{ color:#6b7280; }}
  .empty {{ color:#6b7280; padding:26px 0; border-top:1px solid #ded6c7; }}
  code {{ background:#e9e2d4; padding:2px 5px; border-radius:4px; }}
  @media(max-width:600px) {{ .entry {{ grid-template-columns:1fr 22px; gap:6px; }} .entry-date,.entry-meta {{ grid-column:1; }} .entry-title {{ grid-column:1; }} .entry-arrow {{ grid-row:1/4; }} }}
</style></head>
<body><div class="wrap">
  <div class="brand">{esc(settings.app_name)} / ARCHIVE</div>
  <h1>每天一道，<br>把思路留下来。</h1>
  <p class="sub">最近生成的可交互解题页。</p>
  {body}
</div></body></html>"""

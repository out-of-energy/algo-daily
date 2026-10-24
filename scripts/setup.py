#!/usr/bin/env python3
"""Interactive, local-only .env setup. Secrets are read with getpass."""

from __future__ import annotations

import getpass
import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"


def read_existing() -> dict[str, str]:
    data: dict[str, str] = {}
    if not ENV.exists():
        return data
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data


def ask(label: str, default: str = "", secret: bool = False) -> str:
    shown = "" if not default else ("（留空保留现有值）" if secret else f" [{default}]")
    prompt = f"{label}{shown}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip()
    if not value and default:
        return default
    return value


def infer_smtp(email: str) -> tuple[str, int, str]:
    domain = email.split("@")[-1].lower()
    presets = {
        "qq.com": ("smtp.qq.com", 465, "ssl"),
        "foxmail.com": ("smtp.qq.com", 465, "ssl"),
        "gmail.com": ("smtp.gmail.com", 465, "ssl"),
        "163.com": ("smtp.163.com", 465, "ssl"),
        "126.com": ("smtp.126.com", 465, "ssl"),
        "outlook.com": ("smtp-mail.outlook.com", 587, "starttls"),
        "hotmail.com": ("smtp-mail.outlook.com", 587, "starttls"),
    }
    return presets.get(domain, ("", 465, "ssl"))


def main() -> int:
    existing = read_existing()
    print("=== 算法日报本地配置 ===")
    print("只写入项目根目录的 .env（权限 600），不要把 .env 提交到仓库。\n")

    ai_key = ask(
        "DeepSeek / OpenAI-compatible API Key（已有文件可留空，稍后填 AI_API_KEY_FILE）",
        existing.get("AI_API_KEY", ""),
        secret=True,
    )
    ai_base = ask("AI Base URL", existing.get("AI_BASE_URL", "https://api.deepseek.com"))
    ai_model = ask("AI 模型", existing.get("AI_MODEL", "deepseek-v4-flash"))

    mail_to = ask("接收日报的邮箱 MAIL_TO", existing.get("MAIL_TO", ""))
    smtp_user = ask("发件邮箱 SMTP_USER（通常和收件邮箱相同）", existing.get("SMTP_USER", mail_to))
    host_default, port_default, security_default = infer_smtp(smtp_user or mail_to)
    smtp_host = ask("SMTP 服务器", existing.get("SMTP_HOST", host_default))
    port = ask("SMTP 端口", existing.get("SMTP_PORT", str(port_default)))
    security = ask("SMTP 加密 ssl/starttls/none", existing.get("SMTP_SECURITY", security_default))
    smtp_pass = ask(
        "SMTP 授权码 / App Password（不是网页登录密码）",
        existing.get("SMTP_PASS", ""),
        secret=True,
    )
    mail_from = ask("发件地址 MAIL_FROM", existing.get("MAIL_FROM", smtp_user or mail_to))
    public_base = ask(
        "公网页面地址 PUBLIC_BASE_URL（可留空，留空则邮件带 HTML 附件）",
        existing.get("PUBLIC_BASE_URL", ""),
    )

    lines = [
        "# 由 scripts/setup.py 生成；不要提交此文件",
        "AI_API_KEY=" + ai_key,
        "AI_BASE_URL=" + ai_base,
        "AI_MODEL=" + ai_model,
        "",
        "SMTP_HOST=" + smtp_host,
        "SMTP_PORT=" + port,
        "SMTP_SECURITY=" + security,
        "SMTP_USER=" + smtp_user,
        "SMTP_PASS=" + smtp_pass,
        "MAIL_FROM=" + mail_from,
        "MAIL_TO=" + mail_to,
        "",
        "PUBLIC_BASE_URL=" + public_base,
        "MAIL_DRY_RUN=1",
    ]
    ENV.write_text("\n".join(lines) + "\n")
    os.chmod(ENV, stat.S_IRUSR | stat.S_IWUSR)
    print(f"\n已写入 {ENV}（权限 600）。")
    print("默认 MAIL_DRY_RUN=1：先试跑，确认 .eml 正常后改成 0，或给 run_daily.py 加 --send。")
    print("\n下一步：")
    print("  python3 scripts/doctor.py")
    print("  python3 scripts/run_daily.py --demo")
    print("  python3 scripts/run_daily.py --dry-run")
    print("  python3 scripts/schedule.py install --time 08:00")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

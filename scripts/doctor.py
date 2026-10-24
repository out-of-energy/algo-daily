#!/usr/bin/env python3
"""Preflight checklist: no secrets are printed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_app.config import Settings  # noqa: E402
from bootstrap_data import dataset_status  # noqa: E402


def mask_email(value: str) -> str:
    if "@" not in value:
        return value[:2] + "***" if value else "(empty)"
    local, domain = value.split("@", 1)
    return (local[:2] + "***@" + domain) if local else "***@" + domain


def main() -> int:
    settings = Settings()
    checks = []
    data_ready, data_detail = dataset_status()
    checks.append(("题库 SQLite", data_ready, data_detail if data_ready else data_detail + "，运行 make data"))
    checks.append(("AI key / model", settings.ai_configured, f"{settings.ai_model}（{settings.ai_key_source}）"))
    checks.append(("SMTP host", bool(settings.smtp_host), settings.smtp_host or "未配置"))
    checks.append(("SMTP 密码来源", bool(settings.smtp_password), settings.smtp_password_source))
    checks.append(("发件人", bool(settings.mail_from), mask_email(settings.mail_from) if settings.mail_from else "未配置"))
    checks.append(("收件人", bool(settings.mail_to), f"{len(settings.mail_to)} 个"))
    checks.append(("邮件模式", True, "dry-run（只写 .eml）" if settings.mail_dry_run else "真实发送"))
    checks.append(("公网页面", bool(settings.public_base_url), settings.public_base_url or "未配置（邮件用附件）"))
    for label, ok, detail in checks:
        mark = "OK " if ok else "!! "
        print(f"{mark}{label:<18} {detail}")
    print()
    if not settings.ai_configured:
        print("下一步：复制 .env.example 为 .env，填写 AI_API_KEY。")
    if not settings.smtp_configured:
        print("下一步：填写 SMTP_HOST / SMTP_USER / SMTP_PASS / MAIL_TO，并把 MAIL_DRY_RUN 改为 0。")
    print("可以先运行：python3 scripts/run_daily.py --demo")
    print("定时任务：python3 scripts/schedule.py install --time 08:00")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

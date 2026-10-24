#!/usr/bin/env python3
"""Reuse the word-learning project's credential files without copying secrets.

The word app (`love`) keeps its launchd environment in
`~/Library/LaunchAgents/dev.love.daily.plist`; the plist contains paths to
0600 secret files, not the secrets themselves. This script reads that plist
(read-only), and can write an `.env` for 算法日报 that references the same
files. It never reads or prints the key/password contents.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLIST = Path.home() / "Library" / "LaunchAgents" / "dev.love.daily.plist"
ENV_PATH = ROOT / ".env"


def mask_email(value: str) -> str:
    if "@" not in value:
        return value[:2] + "***" if value else "(empty)"
    local, domain = value.split("@", 1)
    return (local[:2] + "***@" + domain) if local else "***@" + domain


def file_info(path: str) -> str:
    if not path:
        return "未配置"
    p = Path(path).expanduser()
    if not p.exists():
        return f"{p}（不存在）"
    mode = stat.S_IMODE(p.stat().st_mode)
    return f"{p}（权限 {mode:o}）"


def main() -> int:
    parser = argparse.ArgumentParser(description="从单词项目的 launchd 配置生成算法日报 .env")
    parser.add_argument("--plist", default=str(DEFAULT_PLIST))
    parser.add_argument("--write", action="store_true", help="写入项目根目录 .env")
    parser.add_argument("--force", action="store_true", help="覆盖已有 .env")
    args = parser.parse_args()

    plist_path = Path(args.plist).expanduser()
    if not plist_path.exists():
        print(f"找不到 {plist_path}", file=sys.stderr)
        print("请确认单词项目的 launchd 已安装；或手动填写 .env。", file=sys.stderr)
        return 2

    plist = plistlib.loads(plist_path.read_bytes())
    env = plist.get("EnvironmentVariables") or {}
    user = str(env.get("SMTP_USER", "")).strip()
    to = str(env.get("LOVE_MAIL_TO", "")).strip() or user
    sender = str(env.get("SMTP_FROM", "")).strip() or user
    password_file = str(env.get("SMTP_PASSWORD_FILE", "")).strip()
    ai_file = str(env.get("DEEPSEEK_API_KEY_FILE", "")).strip()
    host = str(env.get("SMTP_HOST", "")).strip() or "smtp.qq.com"
    port = str(env.get("SMTP_PORT", "")).strip() or "465"
    security = "ssl" if port == "465" else "starttls"

    print("找到单词项目的邮件配置：")
    print(f"  SMTP       {host}:{port} / {security}")
    print(f"  发件人      {mask_email(sender)}")
    print(f"  收件人      {mask_email(to)}")
    print(f"  密码文件    {file_info(password_file)}")
    print(f"  AI key 文件 {file_info(ai_file)}")
    print(f"  launchd    {plist_path}")

    if not user or not to:
        print("launchd 里缺少 SMTP_USER 或 LOVE_MAIL_TO，无法复用。", file=sys.stderr)
        return 1
    if not password_file:
        print("launchd 里没有 SMTP_PASSWORD_FILE，无法复用。", file=sys.stderr)
        return 1

    lines = [
        "# 自动生成：复用单词项目 love 的凭据文件；不包含密钥内容",
        f"AI_API_KEY_FILE={Path(ai_file).expanduser() if ai_file else ''}",
        "AI_BASE_URL=https://api.deepseek.com/v1",
        "AI_MODEL=deepseek-v4-flash",
        "",
        f"SMTP_HOST={host}",
        f"SMTP_PORT={port}",
        f"SMTP_SECURITY={security}",
        f"SMTP_USER={user}",
        f"SMTP_PASSWORD_FILE={Path(password_file).expanduser()}",
        f"MAIL_FROM={sender}",
        f"MAIL_TO={to}",
        "PUBLIC_BASE_URL=",
        "",
        "# 先 dry-run；确认后再改成 0",
        "MAIL_DRY_RUN=1",
    ]

    if not args.write:
        print("\n未写入 .env。确认无误后运行：")
        print("  python3 scripts/reuse_love_config.py --write")
        return 0

    if ENV_PATH.exists() and not args.force:
        print(f"\n{ENV_PATH} 已存在；加 --force 才会覆盖。", file=sys.stderr)
        return 1
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)
    print(f"\n已写入 {ENV_PATH}（权限 600），默认 dry-run。")
    print("下一步：")
    print("  python3 scripts/doctor.py")
    print("  python3 scripts/run_daily.py --demo --send")
    print("  python3 scripts/schedule.py install --time 08:00")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

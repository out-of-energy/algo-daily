#!/usr/bin/env python3
"""Install/uninstall a macOS launchd job for the daily run."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.kk.algorithm.daily"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = ROOT / "data" / "daily" / "logs"


def uid() -> str:
    return str(os.getuid())


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def is_loaded() -> bool:
    result = run(["launchctl", "print", f"gui/{uid()}/{LABEL}"])
    return result.returncode == 0


def install(time_str: str) -> int:
    try:
        hour_s, minute_s = time_str.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        print("时间格式必须是 HH:MM，例如 08:00", file=sys.stderr)
        return 2
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if is_loaded():
        run(["launchctl", "bootout", f"gui/{uid()}/{LABEL}"])
    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            str(ROOT / "scripts" / "run_daily.py"),
        ],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "ProcessType": "Background",
        "StandardOutPath": str(LOG_DIR / "launchd.out.log"),
        "StandardErrorPath": str(LOG_DIR / "launchd.err.log"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONUNBUFFERED": "1",
        },
    }
    PLIST.write_bytes(plistlib.dumps(plist))
    result = run(["launchctl", "bootstrap", f"gui/{uid()}", str(PLIST)])
    if result.returncode != 0:
        # Compatibility path for older launchd setups.
        fallback = run(["launchctl", "load", "-w", str(PLIST)])
        if fallback.returncode != 0:
            print("launchd 安装失败：", file=sys.stderr)
            print(result.stderr or result.stdout, file=sys.stderr)
            print(fallback.stderr or fallback.stdout, file=sys.stderr)
            return 1
    print(f"已安装定时任务：{LABEL}，每天 {hour:02d}:{minute:02d}")
    print(f"plist：{PLIST}")
    print(f"日志：{LOG_DIR}")
    print("注意：Mac 必须开机并处于可运行状态；关机期间不会执行。")
    return 0


def uninstall() -> int:
    if is_loaded():
        run(["launchctl", "bootout", f"gui/{uid()}/{LABEL}"])
    if PLIST.exists():
        PLIST.unlink()
        print("已卸载定时任务并删除 plist")
    else:
        print("没有已安装的 plist")
    return 0


def status() -> int:
    loaded = is_loaded()
    print(f"label: {LABEL}")
    print(f"loaded: {'yes' if loaded else 'no'}")
    print(f"plist: {PLIST} ({'exists' if PLIST.exists() else 'missing'})")
    if loaded:
        result = run(["launchctl", "print", f"gui/{uid()}/{LABEL}"])
        for line in result.stdout.splitlines():
            if any(key in line for key in ("state =", "last exit", "runs =", "program =")):
                print(line.strip())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="macOS 每日定时任务")
    sub = parser.add_subparsers(dest="command", required=True)
    p_install = sub.add_parser("install")
    p_install.add_argument("--time", default="08:00", help="HH:MM，默认 08:00")
    sub.add_parser("uninstall")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "install":
        return install(args.time)
    if args.command == "uninstall":
        return uninstall()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())

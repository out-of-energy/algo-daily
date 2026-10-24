#!/usr/bin/env python3
"""Serve the generated site locally."""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        print(f"[site] {fmt % args}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="本地预览每日算法页")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args()
    if not SITE.exists():
        print(f"还没有生成页面：{SITE}；先运行 python3 scripts/run_daily.py --demo")
        return 1
    handler = functools.partial(Handler, directory=str(SITE))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        url = f"http://127.0.0.1:{args.port}/"
        print(f"本地预览：{url}")
        print("按 Ctrl+C 停止")
        if args.open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Configuration loading for the daily algorithm app.

Secrets live in the project `.env`; non-secret defaults live in
`config/app.json`. Environment variables always win over `.env`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    """Tiny .env loader: KEY=VALUE, # comments, optional quotes.

    Existing environment variables are not overwritten.
    """
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _read_secret_file(env_names: tuple[str, ...]) -> tuple[str, str]:
    """Read the first existing secret file named by an env var.

    Returns (value, source). The value is never logged or embedded in a page.
    """
    for name in env_names:
        raw_path = os.environ.get(name, "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        try:
            value = path.read_text().strip()
        except OSError:
            continue
        if value:
            return value, f"file:{path}"
    return "", ""


class Settings:
    def __init__(self) -> None:
        load_dotenv()
        self.root = ROOT
        self.app = json.loads((ROOT / "config" / "app.json").read_text())
        self.topics_cfg = json.loads((ROOT / "config" / "topics.json").read_text())

    @property
    def app_name(self) -> str:
        return self.app.get("app_name", "算法日报")

    @property
    def timezone(self) -> str:
        return self.app.get("timezone", "Asia/Shanghai")

    @property
    def schedule_time(self) -> str:
        return self.app.get("schedule_time", "08:00")

    @property
    def public_base_url(self) -> str:
        return (
            os.environ.get("PUBLIC_BASE_URL", "").strip()
            or self.app.get("public_base_url", "").strip()
        ).rstrip("/")

    @property
    def ai_api_key(self) -> str:
        value = (
            os.environ.get("AI_API_KEY", "").strip()
            or os.environ.get("DEEPSEEK_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
        if value:
            return value
        value, _ = _read_secret_file((
            "AI_API_KEY_FILE",
            "DEEPSEEK_API_KEY_FILE",
            "OPENAI_API_KEY_FILE",
        ))
        return value

    @property
    def ai_key_source(self) -> str:
        if os.environ.get("AI_API_KEY", "").strip():
            return "env:AI_API_KEY"
        if os.environ.get("DEEPSEEK_API_KEY", "").strip():
            return "env:DEEPSEEK_API_KEY"
        if os.environ.get("OPENAI_API_KEY", "").strip():
            return "env:OPENAI_API_KEY"
        _, source = _read_secret_file((
            "AI_API_KEY_FILE", "DEEPSEEK_API_KEY_FILE", "OPENAI_API_KEY_FILE",
        ))
        return source or "missing"

    @property
    def ai_base_url(self) -> str:
        return (
            os.environ.get("AI_BASE_URL", "").strip()
            or self.app.get("ai", {}).get("base_url", "https://api.deepseek.com").strip()
        )

    @property
    def ai_model(self) -> str:
        return (
            os.environ.get("AI_MODEL", "").strip()
            or self.app.get("ai", {}).get("model", "deepseek-v4-flash").strip()
        )

    @property
    def ai_disable_thinking(self) -> bool:
        value = os.environ.get("AI_THINKING", "").strip().lower()
        if value in {"default", "enabled", "on"}:
            return False
        if value in {"disabled", "none", "off"}:
            return True
        return bool(self.app.get("ai", {}).get("disable_thinking", True))

    @property
    def ai_timeout(self) -> int:
        return int(self.app.get("ai", {}).get("timeout_seconds", 180))

    @property
    def ai_max_tokens(self) -> int:
        return int(self.app.get("ai", {}).get("max_tokens", 10000))

    @property
    def ai_temperature(self) -> float:
        return float(self.app.get("ai", {}).get("temperature", 0.35))

    @property
    def smtp_host(self) -> str:
        return os.environ.get("SMTP_HOST", "").strip()

    @property
    def smtp_port(self) -> int:
        return int(os.environ.get("SMTP_PORT", "465").strip() or "465")

    @property
    def smtp_security(self) -> str:
        value = os.environ.get("SMTP_SECURITY", "").strip().lower()
        if value in {"ssl", "starttls", "none"}:
            return value
        return "ssl" if self.smtp_port == 465 else "starttls"

    @property
    def smtp_user(self) -> str:
        return os.environ.get("SMTP_USER", "").strip()

    @property
    def smtp_password(self) -> str:
        value = (
            os.environ.get("SMTP_PASS", "")
            or os.environ.get("SMTP_PASSWORD", "")
        ).strip()
        if value:
            return value
        value, _ = _read_secret_file(("SMTP_PASSWORD_FILE", "SMTP_PASS_FILE"))
        return value

    @property
    def smtp_password_source(self) -> str:
        if os.environ.get("SMTP_PASS", "").strip():
            return "env:SMTP_PASS"
        if os.environ.get("SMTP_PASSWORD", "").strip():
            return "env:SMTP_PASSWORD"
        _, source = _read_secret_file(("SMTP_PASSWORD_FILE", "SMTP_PASS_FILE"))
        return source or "missing"

    @property
    def mail_from(self) -> str:
        return (
            os.environ.get("MAIL_FROM", "").strip()
            or os.environ.get("SMTP_FROM", "").strip()
            or self.smtp_user
        )

    @property
    def mail_to(self) -> list[str]:
        raw = (
            os.environ.get("MAIL_TO", "").strip()
            or os.environ.get("LOVE_MAIL_TO", "").strip()
        )
        return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]

    @property
    def mail_dry_run(self) -> bool:
        explicit = os.environ.get("MAIL_DRY_RUN")
        if explicit is not None:
            return _bool(explicit, True)
        return not self.smtp_configured

    @property
    def smtp_configured(self) -> bool:
        return bool(
            self.smtp_host and self.mail_from and self.mail_to
            and self.smtp_password
        )

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_api_key)

    def topic_label(self, topic_id: str | None) -> str:
        for category in self.topics_cfg["categories"]:
            if category["id"] == topic_id:
                return category["label"]
        return topic_id or "未分类"

    def topic_accent(self, topic_id: str | None) -> str:
        for category in self.topics_cfg["categories"]:
            if category["id"] == topic_id:
                return category.get("accent", "#2563eb")
        return "#2563eb"

    def topic_patterns(self, topic_id: str | None) -> list[str]:
        for category in self.topics_cfg["categories"]:
            if category["id"] == topic_id:
                return category.get("patterns", [])
        return []

    def public_day_url(self, date_str: str) -> str:
        if not self.public_base_url:
            return ""
        return f"{self.public_base_url}/day/{date_str}.html"

    def missing_for_send(self) -> list[str]:
        missing = []
        if not self.smtp_host:
            missing.append("SMTP_HOST")
        if not self.smtp_user:
            missing.append("SMTP_USER")
        if not self.smtp_password:
            missing.append("SMTP_PASS")
        if not self.mail_from:
            missing.append("MAIL_FROM 或 SMTP_USER")
        if not self.mail_to:
            missing.append("MAIL_TO")
        return missing

    def redacted(self) -> dict[str, Any]:
        return {
            "ai_base_url": self.ai_base_url,
            "ai_model": self.ai_model,
            "ai_configured": self.ai_configured,
            "ai_key_source": self.ai_key_source,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_security": self.smtp_security,
            "mail_from": (self.mail_from.split("@")[0][:2] + "***@" + self.mail_from.split("@", 1)[1]) if "@" in self.mail_from else self.mail_from,
            "mail_to_count": len(self.mail_to),
            "smtp_password_source": self.smtp_password_source,
            "mail_dry_run": self.mail_dry_run,
            "public_base_url": self.public_base_url,
            "timezone": self.timezone,
        }

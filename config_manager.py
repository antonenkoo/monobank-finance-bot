"""
config_manager.py — .env configuration + local template storage.
"""

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BOT_VERSION    = "1.5.1"

ENV_PATH       = Path(".env")
TEMPLATES_PATH = Path("templates.json")

# ── Field metadata ─────────────────────────────────────────────────────────────

FIELD_LABELS: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN":        "Telegram Bot Token",
    "TELEGRAM_CHAT_ID":          "Telegram Chat ID",
    "MONOBANK_TOKEN":            "Monobank Personal Token",
    "MONOBANK_ACCOUNT_ID":       "Monobank Account ID",
    "NOTION_API_KEY":            "Notion API Key",
    "NOTION_TRANSACTIONS_DB_ID": "Notion Transactions Database ID",
    "NOTION_CATEGORIES_DB_ID":   "Notion Categories Database ID",
    "NGROK_AUTH_TOKEN":          "Ngrok Auth Token",
    "WEBHOOK_PORT":              "Webhook Port",
    "NOTION_REMAINING_PROP":     "Notion Column Name With Remaining Amount",
    "NOTION_LIMIT_PROP":         "Notion Column Name With Monthly Limit",
    "DEVELOPER_FEEDBACK_URL":    "Developer Feedback URL (ngrok)",
}

FIELD_HINTS: dict[str, str] = {
    "MONOBANK_TOKEN": (
        "Личный токен Monobank.\n"
        "Где взять: monobank.ua → Особисті дані → Токен."
    ),
    "MONOBANK_ACCOUNT_ID": (
        "ID конкретной карты/счёта.\n"
        "Проще выбрать через кнопку «Выбрать аккаунт» ниже."
    ),
    "NOTION_API_KEY": (
        "Internal Integration Secret из Notion.\n"
        "Где взять: notion.so/my-integrations → создай интеграцию → скопируй секрет."
    ),
    "NOTION_TRANSACTIONS_DB_ID": (
        "32 символа из URL базы транзакций в Notion.\n"
        "Убедись, что интеграция подключена к базе (··· → Connections)."
    ),
    "NOTION_CATEGORIES_DB_ID": (
        "32 символа из URL базы категорий в Notion.\n"
        "Убедись, что интеграция подключена к этой базе тоже."
    ),
    "NOTION_REMAINING_PROP": (
        "Название колонки в которой пишеться количество денег оставшееся для каждого типа категории."
    ),
    "NOTION_LIMIT_PROP": (
        "Название колонки с лимитом расходов на месяц для каждой категории.\n"
        "Используется для уведомлений о достижении лимита."
    ),
    "DEVELOPER_FEEDBACK_URL": (
        "Ngrok URL разработчика для отправки фидбека.\n"
        "Пример: https://mark-dev.ngrok-free.app\n"
        "Бот будет делать POST /feedback на этот адрес."
    ),
    "NGROK_AUTH_TOKEN": (
        "Auth Token из ngrok — создаёт публичный URL для webhook.\n"
        "Где взять: dashboard.ngrok.com → Your Authtoken."
    )
}

EDITABLE_FIELDS: list[str] = [
    "MONOBANK_TOKEN",
    "MONOBANK_ACCOUNT_ID",
    "NOTION_API_KEY",
    "NOTION_TRANSACTIONS_DB_ID",
    "NOTION_CATEGORIES_DB_ID",
    "NGROK_AUTH_TOKEN",
    "NOTION_REMAINING_PROP",
    "NOTION_LIMIT_PROP",
    "DEVELOPER_FEEDBACK_URL",
]

REQUIRED_FIELDS: list[str] = [
    "TELEGRAM_BOT_TOKEN",
    "MONOBANK_TOKEN",
    "MONOBANK_ACCOUNT_ID",
    "NOTION_API_KEY",
    "NOTION_TRANSACTIONS_DB_ID",
    "NOTION_CATEGORIES_DB_ID",
]

RESTART_REQUIRED_FIELDS: set[str] = {"MONOBANK_TOKEN", "NGROK_AUTH_TOKEN"}


# ═════════════════════════════════════════════════════════════════════════════
# ConfigManager
# ═════════════════════════════════════════════════════════════════════════════

class ConfigManager:
    def __init__(self, env_path: Path = ENV_PATH) -> None:
        self.env_path = env_path
        self._ensure_file()
        self._reload()

    def _ensure_file(self) -> None:
        if not self.env_path.exists():
            self.env_path.write_text("", encoding="utf-8")

    def _reload(self) -> None:
        load_dotenv(self.env_path, override=True)

    def get(self, key: str, default: str = "") -> str:
        return os.environ.get(key, default)

    def set(self, key: str, value: str) -> None:
        content  = self.env_path.read_text(encoding="utf-8")
        lines    = content.splitlines()
        safe     = f'"{value}"' if (" " in value or "=" in value) else value
        new_line = f"{key}={safe}"
        updated  = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}=") or line.strip() == f"{key}=":
                lines[i] = new_line
                updated  = True
                break
        if not updated:
            lines.append(new_line)
        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._reload()

    def is_configured(self) -> bool:
        return all(self.get(k) for k in REQUIRED_FIELDS)

    def missing_fields(self) -> list[str]:
        return [k for k in REQUIRED_FIELDS if not self.get(k)]

    def get_mode(self) -> str:
        """Return current operating mode: 'silent' (default) or 'pro'."""
        return self.get("BOT_MODE", "silent").strip().lower()

    def set_mode(self, mode: str) -> None:
        """Persist operating mode: 'silent' or 'pro'."""
        self.set("BOT_MODE", mode)

    def get_notes_enabled(self) -> bool:
        """Return True if the card-transaction notes step is enabled (default: on)."""
        return self.get("CARD_NOTES_ENABLED", "on").strip().lower() == "on"

    def set_notes_enabled(self, enabled: bool) -> None:
        self.set("CARD_NOTES_ENABLED", "on" if enabled else "off")

    def get_smart_cats_enabled(self) -> bool:
        """Return True if smart (last-used) category suggestion is enabled (default: on)."""
        return self.get("SMART_CATS_ENABLED", "on").strip().lower() == "on"

    def set_smart_cats_enabled(self, enabled: bool) -> None:
        self.set("SMART_CATS_ENABLED", "on" if enabled else "off")

    def get_webhook_port(self) -> int:
        return int(self.get("WEBHOOK_PORT", "8080"))

    def mask(self, key: str) -> str:
        value = self.get(key)
        if not value:
            return "❌ не задан"
        if key in ("TELEGRAM_CHAT_ID", "WEBHOOK_PORT", "MONOBANK_ACCOUNT_ID"):
            return value
        if len(value) <= 8:
            return "✅ " + "*" * len(value)
        return f"✅ {value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"

    def config_summary(self) -> str:
        lines = ["<b>Текущие настройки:</b>\n"]
        for f in EDITABLE_FIELDS:
            lines.append(f"• {FIELD_LABELS[f]}: {self.mask(f)}")
        mode = "🔔 Про" if self.get_mode() == "pro" else "🔇 Тихий"
        lines.append(f"\n• Режим работы: {mode}")
        smart = "вкл" if self.get_smart_cats_enabled() else "выкл"
        lines.append(f"• Авто-категории: {smart}")
        return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# TemplateManager
# ═════════════════════════════════════════════════════════════════════════════

class TemplateManager:
    """Stores reusable transaction templates in templates.json."""

    def __init__(self, path: Path = TEMPLATES_PATH) -> None:
        self.path = path
        if not self.path.exists():
            self.path.write_text('{"templates": []}', encoding="utf-8")

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_all(self) -> list[dict]:
        return self._load().get("templates", [])

    def get(self, template_id: str) -> Optional[dict]:
        return next((t for t in self.get_all() if t["id"] == template_id), None)

    def add(
        self,
        name:          str,
        amount:        float,
        category_id:   Optional[str],
        category_name: str,
        notes:         str = "",
    ) -> dict:
        tpl = {
            "id":            str(uuid.uuid4())[:8],
            "name":          name,
            "amount":        round(amount, 2),
            "category_id":   category_id,
            "category_name": category_name,
            "notes":         notes,
        }
        data = self._load()
        data.setdefault("templates", []).append(tpl)
        self._save(data)
        logger.info("Template saved: %s (%.2f)", name, amount)
        return tpl

    def update(self, template_id: str, **fields) -> bool:
        """Update one or more fields of an existing template."""
        data      = self._load()
        templates = data.get("templates", [])
        for i, t in enumerate(templates):
            if t["id"] == template_id:
                for k, v in fields.items():
                    templates[i][k] = v
                self._save(data)
                return True
        return False

    def delete(self, template_id: str) -> bool:
        data   = self._load()
        before = len(data.get("templates", []))
        data["templates"] = [t for t in data.get("templates", []) if t["id"] != template_id]
        if len(data["templates"]) == before:
            return False
        self._save(data)
        return True

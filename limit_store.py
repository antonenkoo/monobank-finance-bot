"""
limit_store.py — Persists "already notified" flags for budget limit alerts.

Backed by limit_notifications.json so flags survive bot restarts.
Keys follow the pattern:  "{category_name}:{YYYY-MM}:{level}"
  level = "low25"    → ≤ 25% of monthly budget remaining
  level = "exceeded" → monthly budget exceeded

A separate clear_month() call (not currently used) can reset flags when a
new month starts, but in practice the YYYY-MM in the key makes old entries
harmless — they simply never match the new month's keys.
"""

import json
import logging
from pathlib import Path

LIMIT_STORE_PATH = Path("limit_notifications.json")
logger = logging.getLogger(__name__)


class LimitNotificationStore:

    def __init__(self, path: Path = LIMIT_STORE_PATH) -> None:
        self.path = path
        if not self.path.exists():
            self._write({})

    # ── I/O ───────────────────────────────────────────────────────────────────

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("LimitStore read error: %s", exc)
            return {}

    def _write(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def already_notified(self, key: str) -> bool:
        return key in self._read()

    def mark_notified(self, key: str) -> None:
        data = self._read()
        data[key] = True
        self._write(data)

"""
pending_store.py — JSON-backed store for transactions awaiting category selection.

Transactions that arrive via Monobank webhook are held here until the user
picks a category (or explicitly skips) in Telegram. The file persists across
bot restarts, so old messages stay actionable.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from config_manager import USER_DATA_DIR

logger = logging.getLogger(__name__)

PENDING_PATH = USER_DATA_DIR / "pending_transactions.json"


class PendingTransactionStore:
    """Thread-safe enough for single-process use with Python's GIL."""

    def __init__(self, path: Path = PENDING_PATH) -> None:
        self.path = path
        if not self.path.exists():
            self._write({})

    # ── I/O ───────────────────────────────────────────────────────────────────

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("PendingStore read error: %s", exc)
            return {}

    def _write(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def add(self, item: dict, chat_id: str, formatted_text: str) -> str:
        """
        Store a pending transaction.

        Returns a short 8-char transaction ID used in callback_data.
        """
        txn_id = str(uuid.uuid4())[:8]
        data = self._read()
        data[txn_id] = {
            "item":        item,
            "chat_id":     chat_id,
            "text":        formatted_text,
            "state":       "selecting",   # "selecting" | "awaiting_notes"
            "message_id":  None,          # set after message is sent
            "category_id": None,
            "cat_display": "",
        }
        self._write(data)
        logger.debug("Pending txn stored: %s (%s)", txn_id, item.get("description", "?"))
        return txn_id

    def set_message_id(self, txn_id: str, message_id: int) -> None:
        """Store the Telegram message_id so we can edit it later."""
        data = self._read()
        if txn_id in data:
            data[txn_id]["message_id"] = message_id
            self._write(data)

    def update_for_notes(
        self, txn_id: str, category_id: Optional[str], cat_display: str
    ) -> None:
        """Transition to awaiting_notes state after the user picked a category."""
        data = self._read()
        if txn_id in data:
            data[txn_id]["state"]       = "awaiting_notes"
            data[txn_id]["category_id"] = category_id
            data[txn_id]["cat_display"] = cat_display
            self._write(data)

    def get_awaiting_notes_by_chat(self, chat_id: str) -> Optional[tuple]:
        """Return (txn_id, pending_data) for the first transaction awaiting notes."""
        data = self._read()
        for txn_id, pending in data.items():
            if (pending.get("chat_id") == str(chat_id)
                    and pending.get("state") == "awaiting_notes"):
                return txn_id, pending
        return None

    def get(self, txn_id: str) -> Optional[dict]:
        return self._read().get(txn_id)

    def remove(self, txn_id: str) -> None:
        data = self._read()
        if txn_id in data:
            data.pop(txn_id)
            self._write(data)
            logger.debug("Pending txn removed: %s", txn_id)


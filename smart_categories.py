"""
smart_categories.py — Per-merchant last-used category memory.

Remembers the category a user chose for each merchant description.
Used to offer a ⭐ suggestion the next time the same merchant appears.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SMART_CATS_PATH = Path("smart_categories.json")


class SmartCategoryStore:
    """
    JSON-backed store: merchant description → last chosen category.
    Structure: {"description": {"category_id": "...", "category_name": "..."}}
    """

    def __init__(self, path: Path = SMART_CATS_PATH) -> None:
        self.path = path
        if not self.path.exists():
            self._write({})

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("SmartCategoryStore read error: %s", exc)
            return {}

    def _write(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, description: str) -> Optional[dict]:
        """Return {"category_id": str, "category_name": str} or None."""
        return self._read().get(description)

    def set(self, description: str, category_id: str, category_name: str) -> None:
        """Save the last chosen category for this merchant description."""
        data = self._read()
        data[description] = {
            "category_id":   category_id,
            "category_name": category_name,
        }
        self._write(data)
        logger.debug("SmartCats saved: %r → %s", description, category_name)

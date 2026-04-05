"""
notion_service.py — Notion API integration.

Handles:
  - Fetching categories from the Categories database
  - Creating transaction pages in the Transactions database

All calls are synchronous (use asyncio.to_thread() from async handlers).
"""

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
NOTION_BASE    = "https://api.notion.com/v1"

# Property names in your Notion databases — change if yours differ
PROP_NAME       = "Name"        # Title
PROP_DATE       = "Date"        # Date
PROP_AMOUNT     = "Amount"      # Number
PROP_NOTES      = "Notes"       # Rich text
PROP_CATEGORIES = "Categories"  # Relation → categories DB
PROP_CAT_NAME   = "Name"        # Title in categories DB


class NotionService:

    def __init__(
        self,
        api_key:            str,
        transactions_db_id: str,
        categories_db_id:   str,
    ) -> None:
        self.api_key            = api_key
        self.transactions_db_id = transactions_db_id.replace("-", "")
        self.categories_db_id   = categories_db_id.replace("-", "")

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization":  f"Bearer {self.api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type":   "application/json",
        }

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
        url  = f"{NOTION_BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req  = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            logger.error("Notion %s %s → %d: %s", method, path, exc.code, body_text)
            return None
        except Exception as exc:
            logger.error("Notion request failed: %s", exc)
            return None

    # ── Categories ────────────────────────────────────────────────────────────

    def get_categories(self) -> list[dict]:
        """Return all categories sorted alphabetically. Each: {"id", "name"}"""
        categories: list[dict] = []
        cursor: Optional[str]  = None

        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor

            result = self._request(
                "POST", f"/databases/{self.categories_db_id}/query", body
            )
            if not result:
                break

            for page in result.get("results", []):
                name = self._extract_title(page, PROP_CAT_NAME)
                if name:
                    categories.append({"id": page["id"], "name": name})

            if result.get("has_more") and result.get("next_cursor"):
                cursor = result["next_cursor"]
            else:
                break

        categories.sort(key=lambda c: c["name"].lower())
        logger.info("Loaded %d categories from Notion", len(categories))
        return categories

    # ── Transactions ──────────────────────────────────────────────────────────

    def create_transaction(
        self,
        name:        str,
        amount:      float,
        dt:          datetime,
        category_id: Optional[str] = None,
        notes:       str           = "",
    ) -> bool:
        """
        Create a new page in the Transactions database.

        amount: negative = expense, positive = income (stored as-is in Notion).
        """
        properties: dict = {
            PROP_NAME:   {"title": [{"text": {"content": name[:2000]}}]},
            PROP_DATE:   {"date":  {"start": dt.isoformat()}},
            PROP_AMOUNT: {"number": round(amount, 2)},
        }

        if notes:
            properties[PROP_NOTES] = {
                "rich_text": [{"text": {"content": notes[:2000]}}]
            }

        if category_id:
            properties[PROP_CATEGORIES] = {
                "relation": [{"id": category_id}]
            }

        result = self._request("POST", "/pages", {
            "parent":     {"database_id": self.transactions_db_id},
            "properties": properties,
        })
        if result:
            logger.info(
                "Notion transaction: %s %.2f UAH on %s",
                name, amount, dt.strftime("%d.%m.%Y"),
            )
            return True
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_title(page: dict, prop_name: str) -> str:
        try:
            return "".join(
                t["plain_text"]
                for t in page["properties"][prop_name]["title"]
            ).strip()
        except (KeyError, TypeError):
            return ""

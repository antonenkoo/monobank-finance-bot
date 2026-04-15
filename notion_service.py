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
        remaining_prop:     str = "Remaining",
        limit_prop:         str = "",
    ) -> None:
        self.api_key            = api_key
        self.transactions_db_id = transactions_db_id.replace("-", "")
        self.categories_db_id   = categories_db_id.replace("-", "")
        self.remaining_prop     = remaining_prop or "Remaining"
        self.limit_prop         = limit_prop or ""

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
    
    def _parse_remaining_prop(self, prop: dict) -> float | None:
        """Extract a numeric remaining value from any supported Notion property type."""
        ptype = prop.get("type")

        if ptype == "number":
            return prop.get("number")

        if ptype == "formula":
            formula = prop.get("formula", {})
            ftype = formula.get("type")
            if ftype == "number":
                return formula.get("number")
            if ftype == "string":
                value = formula.get("string")
                if not value:
                    return None
                try:
                    cleaned = (
                        value.replace("₴", "")
                        .replace(" ", "")
                        .replace(",", ".")
                        .strip()
                    )
                    return float(cleaned)
                except ValueError:
                    logger.warning("Could not parse remaining formula string: %r", value)
                    return None

        if ptype == "rollup":
            rollup = prop.get("rollup", {})
            if rollup.get("type") == "number":
                return rollup.get("number")
            logger.warning("Unsupported rollup type for remaining: %s", rollup.get("type"))
            return None

        logger.warning("Unsupported remaining property type: %s", ptype)
        return None

    def get_category_limit(self, category_id: str) -> float | None:
        """Read the monthly limit from limit_prop for a given category page."""
        if not self.limit_prop:
            return None
        result = self._request("GET", f"/pages/{category_id}")
        if not result:
            return None
        prop = result.get("properties", {}).get(self.limit_prop)
        if not prop:
            logger.warning("Property '%s' not found for category %s", self.limit_prop, category_id)
            return None
        return self._parse_remaining_prop(prop)

    def get_category_remaining(self, category_id: str) -> float | None:
        result = self._request("GET", f"/pages/{category_id}")
        if not result:
            return None

        prop = result.get("properties", {}).get(self.remaining_prop)
        if not prop:
            logger.warning("Property '%s' not found for category %s", self.remaining_prop, category_id)
            return None

        logger.info("Remaining property type for %s: %s", category_id, prop.get("type"))
        return self._parse_remaining_prop(prop)

    def get_total_remaining(self) -> float | None:
        """Return sum of remaining amounts across all categories."""
        total = 0.0
        found_any = False
        cursor: Optional[str] = None

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
                prop = page.get("properties", {}).get(self.remaining_prop)
                if prop:
                    val = self._parse_remaining_prop(prop)
                    if val is not None:
                        total += val
                        found_any = True

            if result.get("has_more") and result.get("next_cursor"):
                cursor = result["next_cursor"]
            else:
                break

        return total if found_any else None

    def get_all_category_budgets(self) -> dict[str, float | None]:
        """
        Return {normalized_cat_id: limit_or_None} for every category in one query.
        Requires limit_prop to be configured; returns {} otherwise.
        """
        if not self.limit_prop:
            return {}

        budgets: dict[str, float | None] = {}
        cursor: Optional[str] = None

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
                cat_id = page["id"].replace("-", "")
                prop   = page.get("properties", {}).get(self.limit_prop)
                limit  = self._parse_remaining_prop(prop) if prop else None
                budgets[cat_id] = limit

            if result.get("has_more") and result.get("next_cursor"):
                cursor = result["next_cursor"]
            else:
                break

        return budgets

    def get_total_budget(self) -> float | None:
        """Return the sum of all category limits (requires limit_prop to be configured)."""
        if not self.limit_prop:
            return None

        total = 0.0
        found_any = False
        cursor: Optional[str] = None

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
                prop = page.get("properties", {}).get(self.limit_prop)
                if prop:
                    val = self._parse_remaining_prop(prop)
                    if val is not None:
                        total += val
                        found_any = True

            if result.get("has_more") and result.get("next_cursor"):
                cursor = result["next_cursor"]
            else:
                break

        return total if found_any else None

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

    def get_transactions_by_period(
        self, start: "datetime", end: "datetime"
    ) -> list[dict]:
        """
        Return all transactions in [start, end] (inclusive).
        Fetches all pages from the DB and filters by date in Python to avoid
        Notion filter edge-cases (timezone handling, compound filter errors, etc.).
        Each item: {"name", "amount" (float, neg=expense), "category_id" (str|None), "date" (str|None)}
        """
        from datetime import date as _date

        start_date = start.date()
        end_date   = end.date()

        all_pages: list[dict] = []
        cursor: Optional[str] = None

        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor

            result = self._request(
                "POST", f"/databases/{self.transactions_db_id}/query", body
            )
            if not result:
                logger.warning("get_transactions_by_period: Notion query returned None")
                break

            all_pages.extend(result.get("results", []))

            if result.get("has_more") and result.get("next_cursor"):
                cursor = result["next_cursor"]
            else:
                break

        transactions: list[dict] = []
        for page in all_pages:
            amount_prop = page.get("properties", {}).get(PROP_AMOUNT, {})
            amount      = amount_prop.get("number")

            date_prop = page.get("properties", {}).get(PROP_DATE, {})
            date_str  = (date_prop.get("date") or {}).get("start")

            # Filter by date range in Python.
            # Notion date_str is always "YYYY-MM-DD..." so first 10 chars = the date.
            if date_str:
                try:
                    txn_date = _date.fromisoformat(date_str[:10])
                    if not (start_date <= txn_date <= end_date):
                        continue
                except Exception:
                    continue
            else:
                continue  # skip transactions with no date

            if amount is None:
                continue

            cat_prop = page.get("properties", {}).get(PROP_CATEGORIES, {})
            cat_ids  = [r["id"] for r in cat_prop.get("relation", [])]
            cat_id   = cat_ids[0] if cat_ids else None

            name = self._extract_title(page, PROP_NAME)

            transactions.append({
                "name":        name,
                "amount":      amount,
                "category_id": cat_id,
                "date":        date_str,
            })

        logger.info(
            "Loaded %d/%d transactions in range %s–%s",
            len(transactions), len(all_pages), start_date, end_date,
        )
        return transactions

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

"""
monobank_service.py — FastAPI webhook server + ngrok tunnel.

Responsibilities:
  - Expose GET /webhook  (Monobank connectivity check)
  - Expose POST /webhook (incoming transaction events)
  - Expose GET /trigger  (quick-trigger saved templates)
  - Proxy POST /feedback and POST /feedback/voice → feedback-bot on localhost:FEEDBACK_PORT
  - Own and manage the ngrok tunnel (start, stop, restart)
  - Register webhook URL with Monobank on startup

Run in a daemon thread via run_webhook_server().
"""

import asyncio
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

import ngrok
import uvicorn
from fastapi import FastAPI, Request, Response

logger = logging.getLogger(__name__)

# ── Shared queues (consumed by PTB jobs in bot_handlers.py) ───────────────────
webhook_queue: queue.Queue = queue.Queue()  # Monobank StatementItem dicts
trigger_queue: queue.Queue = queue.Queue()  # template dicts from /trigger endpoint
api_txn_queue: queue.Queue = queue.Queue()  # transactions saved via POST /transactions

# ── Feedback storage ───────────────────────────────────────────────────────────
from config_manager import USER_DATA_DIR
FEEDBACKS_FILE = USER_DATA_DIR / "feedbacks.json"


def _load_feedbacks() -> list[dict]:
    try:
        if FEEDBACKS_FILE.exists():
            return json.loads(FEEDBACKS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("feedbacks.json load error: %s", exc)
    return []


def _save_feedbacks(data: list[dict]) -> None:
    try:
        FEEDBACKS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.error("feedbacks.json save error: %s", exc)

# ── Monobank API ──────────────────────────────────────────────────────────────
MONO_API_BASE = "https://api.monobank.ua"

app = FastAPI(docs_url=None, redoc_url=None)

# Set at startup so request handlers can filter by account
_account_id: str = ""
_mono_token: str = ""
_feedback_port: int = 8765  # feedback-bot local port

# Track the running server instance and ngrok listener for graceful restart
_server_instance: Optional[uvicorn.Server] = None
_ngrok_listener = None  # ngrok Listener object


# ── FastAPI routes ─────────────────────────────────────────────────────────────

@app.get("/webhook")
async def webhook_ping() -> Response:
    """Monobank sends a GET to verify the endpoint is alive."""
    return Response(status_code=200)


@app.get("/trigger")
async def trigger_template(name: str = "", id: str = "") -> Response:
    """
    Quick-trigger a saved template by name or ID without opening Telegram.

    GET /trigger?name=Автобус
    GET /trigger?id=a1b2c3d4
    GET /trigger              → returns list of available templates

    The PTB job (process_trigger_queue) picks it up and saves to Notion.
    """
    from config_manager import TemplateManager
    tpl_mgr   = TemplateManager()
    templates = tpl_mgr.get_all()

    # List mode — no params provided
    if not name and not id:
        items = [
            {"id": t["id"], "name": t["name"], "amount": t["amount"],
             "category": t.get("category_name", "—")}
            for t in templates
        ]
        return Response(
            content=json.dumps({"templates": items}, ensure_ascii=False),
            status_code=200,
            media_type="application/json; charset=utf-8",
        )

    # Find template
    tpl: dict | None = None
    if id:
        tpl = next((t for t in templates if t["id"] == id), None)
    elif name:
        name_lower = name.lower()
        tpl = next((t for t in templates if t["name"].lower() == name_lower), None)

    if not tpl:
        available = [t["name"] for t in templates]
        return Response(
            content=json.dumps(
                {"error": f"Template not found", "available": available},
                ensure_ascii=False,
            ),
            status_code=404,
            media_type="application/json; charset=utf-8",
        )

    trigger_queue.put(tpl)
    logger.info("Trigger queued: %s (%.2f UAH)", tpl["name"], abs(tpl["amount"]))
    return Response(
        content=json.dumps(
            {"status": "ok", "template": tpl["name"], "amount": tpl["amount"],
             "category": tpl.get("category_name", "—")},
            ensure_ascii=False,
        ),
        status_code=200,
        media_type="application/json; charset=utf-8",
    )


# ── Notion API routes ─────────────────────────────────────────────────────────

def _make_notion():
    """Build a NotionService from env vars. Returns None if not configured."""
    from notion_service import NotionService
    api_key = os.getenv("NOTION_API_KEY", "")
    txn_db  = os.getenv("NOTION_TRANSACTIONS_DB_ID", "")
    cat_db  = os.getenv("NOTION_CATEGORIES_DB_ID", "")
    if not (api_key and txn_db and cat_db):
        return None
    return NotionService(
        api_key=api_key,
        transactions_db_id=txn_db,
        categories_db_id=cat_db,
        remaining_prop=os.getenv("NOTION_REMAINING_PROP", "Remaining"),
        limit_prop=os.getenv("NOTION_LIMIT_PROP", ""),
    )


# Simple in-memory cache for categories (avoids a Notion round-trip on every call)
_categories_cache: list[dict] = []
_categories_cache_ts: float = 0.0
_CATEGORIES_TTL = 300  # seconds


@app.get("/categories")
async def api_categories() -> Response:
    """
    Return all Notion categories sorted alphabetically.

    GET /categories
    → {"categories": [{"id": "...", "name": "..."}, ...]}

    Result is cached for 5 minutes to avoid slow Notion API round-trips.
    """
    global _categories_cache, _categories_cache_ts

    if _categories_cache and time.monotonic() - _categories_cache_ts < _CATEGORIES_TTL:
        return Response(
            content=json.dumps({"categories": _categories_cache}, ensure_ascii=False),
            status_code=200,
            media_type="application/json; charset=utf-8",
        )

    ns = _make_notion()
    if not ns:
        return Response(
            content=json.dumps({"error": "Notion not configured"}, ensure_ascii=False),
            status_code=503,
            media_type="application/json; charset=utf-8",
        )
    try:
        cats = await asyncio.to_thread(ns.get_categories)
        _categories_cache    = cats
        _categories_cache_ts = time.monotonic()
    except Exception as exc:
        logger.error("GET /categories error: %s", exc)
        return Response(
            content=json.dumps({"error": str(exc)}, ensure_ascii=False),
            status_code=500,
            media_type="application/json; charset=utf-8",
        )
    return Response(
        content=json.dumps({"categories": cats}, ensure_ascii=False),
        status_code=200,
        media_type="application/json; charset=utf-8",
    )


@app.post("/transactions")
async def api_create_transaction(request: Request) -> Response:
    """
    Save a transaction directly to Notion.

    POST /transactions
    Content-Type: text/plain  (raw JSON string in the body)

    Body: {"name":"Silpo","amount":150.50,"category_id":"abc123...","notes":"groceries"}

    Required: name, amount
    Optional: category_id, notes, date (ISO-8601, defaults to now)

    amount > 0  →  expense
    amount < 0  →  income
    """
    try:
        raw  = await request.body()
        data = json.loads(raw)
    except Exception:
        return Response(
            content=json.dumps({"error": "Body must be a valid JSON string"}, ensure_ascii=False),
            status_code=400,
            media_type="application/json; charset=utf-8",
        )

    name   = data.get("name")
    amount = data.get("amount")
    if not name or amount is None:
        return Response(
            content=json.dumps({"error": "name and amount are required"}, ensure_ascii=False),
            status_code=400,
            media_type="application/json; charset=utf-8",
        )

    category_id = data.get("category_id") or None
    notes       = data.get("notes", "")

    import datetime as _dt
    date_str = data.get("date")
    if date_str:
        try:
            dt = _dt.datetime.fromisoformat(date_str)
        except ValueError:
            return Response(
                content=json.dumps(
                    {"error": "Invalid date — use ISO-8601, e.g. 2026-04-25T14:00:00"},
                    ensure_ascii=False,
                ),
                status_code=400,
                media_type="application/json; charset=utf-8",
            )
    else:
        dt = _dt.datetime.now()

    ns = _make_notion()
    if not ns:
        return Response(
            content=json.dumps({"error": "Notion not configured"}, ensure_ascii=False),
            status_code=503,
            media_type="application/json; charset=utf-8",
        )

    try:
        ok = await asyncio.to_thread(
            ns.create_transaction, name, float(amount), dt, category_id, notes,
        )
    except Exception as exc:
        logger.error("POST /transactions error: %s", exc)
        return Response(
            content=json.dumps({"error": str(exc)}, ensure_ascii=False),
            status_code=500,
            media_type="application/json; charset=utf-8",
        )

    if ok:
        logger.info("API transaction saved: %s %.2f", name, amount)
        api_txn_queue.put({
            "name":        name,
            "amount":      float(amount),
            "category_id": category_id,
            "notes":       notes,
            "date":        dt.isoformat(),
        })
        return Response(
            content=json.dumps({"status": "ok", "name": name, "amount": amount},
                               ensure_ascii=False),
            status_code=200,
            media_type="application/json; charset=utf-8",
        )
    return Response(
        content=json.dumps({"error": "Notion returned an error — check server logs"},
                           ensure_ascii=False),
        status_code=500,
        media_type="application/json; charset=utf-8",
    )


# ── Feedback proxy → feedback-bot on localhost:_feedback_port ──────────────────

async def _proxy_to_feedback(request: Request, path: str) -> Response:
    url  = f"http://localhost:{_feedback_port}/{path}"
    body = await request.body()
    req  = urllib.request.Request(
        url,
        data=body or None,
        headers={k: v for k, v in request.headers.items()
                 if k.lower() in ("content-type", "content-length")},
        method=request.method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return Response(content=r.read(), status_code=r.status,
                            media_type=r.headers.get("Content-Type", "application/json"))
    except urllib.error.HTTPError as e:
        return Response(content=e.read(), status_code=e.code, media_type="application/json")
    except Exception as exc:
        logger.error("feedback proxy error: %s", exc)
        return Response(content=json.dumps({"error": str(exc)}),
                        status_code=502, media_type="application/json")


@app.post("/feedback")
async def feedback_proxy(request: Request) -> Response:
    return await _proxy_to_feedback(request, "feedback")


@app.post("/feedback/voice")
async def feedback_voice_proxy(request: Request) -> Response:
    return await _proxy_to_feedback(request, "feedback/voice")


@app.post("/feedback")
async def receive_feedback(request: Request) -> Response:
    """
    Receive feedback from a remote bot instance.
    Saves to feedbacks.json and puts into feedback_notification_queue
    so the PTB job can notify the admin via Telegram.
    """
    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400, content=b"bad json")

    entry = {
        **payload,
        "id":     str(uuid.uuid4())[:8],
        "status": "new",
    }

    # Persist
    feedbacks = _load_feedbacks()
    feedbacks.insert(0, entry)   # newest first
    _save_feedbacks(feedbacks)

    logger.info(
        "Feedback saved: %s from @%s  id=%s",
        entry.get("type"), entry.get("from_username"), entry["id"],
    )
    return Response(
        content=json.dumps({"status": "ok", "id": entry["id"]}, ensure_ascii=False),
        status_code=200,
        media_type="application/json; charset=utf-8",
    )


@app.post("/webhook")
async def webhook_event(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400)

    event_type = body.get("type")
    if event_type != "StatementItem":
        return Response(status_code=200)

    data    = body.get("data", {})
    account = data.get("account", "")

    if _account_id and account != _account_id:
        logger.warning(
            "Webhook: ignoring transaction for account %s (configured: %s)",
            account, _account_id,
        )
        return Response(status_code=200)

    item = data.get("statementItem", {})
    webhook_queue.put(item)
    logger.info("Webhook: queued '%s' %.2f UAH", item.get("description", "?"), abs(item.get("amount", 0) / 100))

    return Response(status_code=200)


# ── Monobank API helpers ───────────────────────────────────────────────────────

def get_accounts(mono_token: str) -> list[dict]:
    """Fetch client info and return list of accounts."""
    url = f"{MONO_API_BASE}/personal/client-info"
    req = urllib.request.Request(url, headers={"X-Token": mono_token})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("accounts", [])
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        logger.error("Monobank client-info → %d: %s", exc.code, body)
        return []
    except Exception as exc:
        logger.error("Monobank client-info failed: %s", exc)
        return []


def register_webhook(mono_token: str, webhook_url: str) -> bool:
    """Register (or update) the webhook URL with Monobank."""
    url  = f"{MONO_API_BASE}/personal/webhook"
    body = json.dumps({"webHookUrl": webhook_url}).encode()
    req  = urllib.request.Request(
        url, data=body,
        headers={"X-Token": mono_token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        logger.info("Monobank webhook registered: %s", webhook_url)
        return True
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        logger.error("Monobank webhook register → %d: %s", exc.code, body_text)
        return False
    except Exception as exc:
        logger.error("Monobank webhook register failed: %s", exc)
        return False


# ── Transaction formatter ──────────────────────────────────────────────────────

MCC_LABELS: dict[int, str] = {
    5411: "Супермаркет",
    5812: "Ресторан / кафе",
    5814: "Фастфуд",
    5541: "АЗС",
    5912: "Аптека",
    4111: "Транспорт",
    4121: "Такси",
    5311: "Торговый центр",
    5651: "Одежда",
    5732: "Электроника",
    6011: "Банкомат",
    6012: "Финансы",
    4814: "Связь",
    5999: "Разное",
}


def format_transaction_message(item: dict) -> str:
    """Build a detailed Russian Telegram message from a StatementItem dict."""
    import datetime

    amount_uah  = item.get("amount", 0) / 100
    balance_uah = item.get("balance", 0) / 100
    description = item.get("description", "—")
    mcc         = item.get("mcc", 0)
    ts          = item.get("time", 0)
    currency    = item.get("currencyCode", 980)

    dt_str = (
        datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")
        if ts else "—"
    )

    sign         = "➖" if amount_uah < 0 else "➕"
    amount_disp  = f"{abs(amount_uah):.2f}"
    balance_disp = f"{balance_uah:.2f}"
    mcc_label    = MCC_LABELS.get(mcc, f"MCC {mcc}" if mcc else "—")
    currency_str = "UAH" if currency == 980 else f"код {currency}"

    op_amount = item.get("operationAmount", item.get("amount", 0)) / 100

    lines = [
        f"{sign} <b>{description}</b>",
        f"💰 Сумма: <b>{amount_disp} {currency_str}</b>",
    ]
    if abs(op_amount) != abs(amount_uah):
        lines.append(f"   (операция: {abs(op_amount):.2f})")
    lines += [
        f"🏦 Остаток: {balance_disp} UAH",
        # f"🏷 Категория: {mcc_label}", // пока не актуально, возможно в будущем переиспользую
        f"🕐 Время: {dt_str}",
    ]

    comment = item.get("comment")
    if comment:
        lines.append(f"💬 Комментарий: {comment}")

    return "\n".join(lines)


# ── Server startup / stop / restart ───────────────────────────────────────────

def _run_server_thread(
    port: int,
    ngrok_token: Optional[str],
    mono_token: str,
    ngrok_domain: str = "",
) -> None:
    """Entry point for the background daemon thread."""
    global _server_instance

    async def _start() -> None:
        global _server_instance, _ngrok_listener
        public_url: Optional[str] = None

        if ngrok_token:
            # Close any existing listener before opening a new one to avoid
            # ERR_NGROK_108 (1-session limit) on bot-triggered restarts.
            if _ngrok_listener is not None:
                try:
                    await _ngrok_listener.close()
                    logger.info("Ngrok: previous listener closed")
                except Exception as exc:
                    logger.warning("Ngrok: could not close previous listener: %s", exc)
                _ngrok_listener = None

            try:
                kwargs: dict = {"authtoken": ngrok_token, "proto": "http"}
                if ngrok_domain:
                    kwargs["domain"] = ngrok_domain
                listener = await ngrok.forward(port, **kwargs)
                _ngrok_listener = listener
                public_url = listener.url()
                logger.info("Ngrok tunnel: %s → localhost:%d", public_url, port)
                try:
                    url_file = os.path.join(os.path.expanduser("~"), ".tunnel-current-url")
                    with open(url_file, "w") as f:
                        f.write(public_url)
                except Exception as exc:
                    logger.warning("Could not write tunnel URL to file: %s", exc)
            except Exception as exc:
                logger.error("Ngrok failed: %s", exc)
        else:
            logger.warning("NGROK_AUTH_TOKEN не задан — туннель не создан")

        if public_url and mono_token:
            register_webhook(mono_token, public_url.rstrip("/") + "/webhook")

        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
        server = uvicorn.Server(config)
        _server_instance = server
        await server.serve()
        _server_instance = None

    asyncio.run(_start())


def stop_webhook_server() -> None:
    """Signal the running uvicorn server to shut down."""
    global _server_instance
    if _server_instance:
        _server_instance.should_exit = True


async def shutdown_ngrok() -> None:
    """Close the ngrok listener so the reserved domain is free for the next run."""
    global _ngrok_listener
    if _ngrok_listener is not None:
        try:
            await _ngrok_listener.close()
            logger.info("Ngrok listener closed")
        except Exception as exc:
            logger.warning("Ngrok close failed: %s", exc)
        _ngrok_listener = None


def run_webhook_server(
    port: int,
    ngrok_token: str,
    mono_token: str,
    account_id: str,
    ngrok_domain: str = "",
    feedback_port: int = 8765,
) -> threading.Thread:
    """Start the webhook server in a daemon thread. Returns the started Thread."""
    global _account_id, _mono_token, _feedback_port
    _account_id    = account_id
    _mono_token    = mono_token
    _feedback_port = feedback_port

    t = threading.Thread(
        target=_run_server_thread,
        args=(port, ngrok_token or None, mono_token, ngrok_domain),
        daemon=True,
        name="webhook-server",
    )
    t.start()
    logger.info("Webhook server запущен (порт %d, feedback→%d)", port, feedback_port)
    return t


def restart_webhook_server(
    port: int,
    ngrok_token: str,
    mono_token: str,
    account_id: str,
    ngrok_domain: str = "",
    feedback_port: int = 8765,
) -> threading.Thread:
    """Gracefully stop the current server and start a new one."""
    stop_webhook_server()
    time.sleep(1.5)  # allow the old server to shut down
    return run_webhook_server(port, ngrok_token, mono_token, account_id, ngrok_domain, feedback_port)

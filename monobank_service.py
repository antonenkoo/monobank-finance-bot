"""
monobank_service.py — FastAPI webhook server + ngrok tunnel.

Responsibilities:
  - Expose GET /webhook  (Monobank connectivity check)
  - Expose POST /webhook (incoming transaction events)
  - Start ngrok tunnel and register webhook URL with Monobank
  - Push raw transaction data into webhook_queue for the PTB job to consume
  - Support restart of the webhook server when tokens change

Run in a daemon thread via run_webhook_server().
"""

import asyncio
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

import ngrok
import uvicorn
from fastapi import FastAPI, Request, Response

logger = logging.getLogger(__name__)

# ── Shared queue (consumed by PTB job in bot_handlers.py) ─────────────────────
webhook_queue: queue.Queue = queue.Queue()

# ── Monobank API ──────────────────────────────────────────────────────────────
MONO_API_BASE = "https://api.monobank.ua"

app = FastAPI(docs_url=None, redoc_url=None)

# Set at startup so request handlers can filter by account
_account_id: str = ""
_mono_token: str = ""

# Track the running server instance for graceful restart
_server_instance: Optional[uvicorn.Server] = None


# ── FastAPI routes ─────────────────────────────────────────────────────────────

@app.get("/webhook")
async def webhook_ping() -> Response:
    """Monobank sends a GET to verify the endpoint is alive."""
    return Response(status_code=200)


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
        logger.debug("Ignoring transaction for account %s (not configured)", account)
        return Response(status_code=200)

    item = data.get("statementItem", {})
    webhook_queue.put(item)
    logger.debug("Transaction queued: %s", item.get("description", "?"))

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
        f"🏷 Категория: {mcc_label}",
        f"🕐 Время: {dt_str}",
    ]

    comment = item.get("comment")
    if comment:
        lines.append(f"💬 Комментарий: {comment}")

    return "\n".join(lines)


# ── Server startup / stop / restart ───────────────────────────────────────────

def _run_server_thread(port: int, ngrok_token: Optional[str], mono_token: str) -> None:
    """Entry point for the background daemon thread."""
    global _server_instance

    async def _start() -> None:
        global _server_instance
        public_url: Optional[str] = None

        if ngrok_token:
            try:
                listener   = await ngrok.forward(port, authtoken=ngrok_token, proto="http")
                public_url = listener.url()
                logger.info("Ngrok tunnel: %s → localhost:%d", public_url, port)
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


def run_webhook_server(
    port: int,
    ngrok_token: str,
    mono_token: str,
    account_id: str,
) -> threading.Thread:
    """Start the webhook server in a daemon thread. Returns the started Thread."""
    global _account_id, _mono_token
    _account_id = account_id
    _mono_token = mono_token

    t = threading.Thread(
        target=_run_server_thread,
        args=(port, ngrok_token or None, mono_token),
        daemon=True,
        name="webhook-server",
    )
    t.start()
    logger.info("Webhook server запущен (порт %d)", port)
    return t


def restart_webhook_server(
    port: int,
    ngrok_token: str,
    mono_token: str,
    account_id: str,
) -> threading.Thread:
    """Gracefully stop the current server and start a new one."""
    stop_webhook_server()
    time.sleep(1.5)  # allow the old server to shut down
    return run_webhook_server(port, ngrok_token, mono_token, account_id)

"""
main.py — Entry point for Monobank Finance Bot.
"""

# load_dotenv before all other imports — bot_handlers reads os.getenv() at module level.
# .env lives in user_data/ so git reset --hard never clobbers it.
import shutil
from pathlib import Path as _Path

_USER_DATA_DIR = _Path(__file__).parent / "user_data"
_USER_DATA_DIR.mkdir(exist_ok=True)

from dotenv import load_dotenv
load_dotenv(_USER_DATA_DIR / ".env")

import asyncio
import logging
import os
import subprocess
import sys
import time

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot_handlers import (
    cancel_handler,
    cmd_report,
    cmd_stats,
    handle_card_notes_text,
    handle_category_callback,
    handle_income_note_callback,
    handle_notes_skip_callback,
    handle_report_callback,
    handle_skip_txn_callback,
    make_add_handler,
    make_feedback_handler,
    make_settings_handler,
    make_templates_handler,
    process_api_txn_queue,
    process_trigger_queue,
    process_webhook_queue,
    _refresh_stats_cache,
    send_monthly_report,
    send_startup_message,
)
from config_manager import BOT_VERSION, ConfigManager, TemplateManager
from monobank_service import run_webhook_server, shutdown_ngrok, stop_webhook_server


_restart_requested = False

_MIGRATE_FILES = [
    ".env",
    "feedbacks.json",
    "pending_transactions.json",
    "smart_categories.json",
    "limit_notifications.json",
    "release_shown.txt",
]

def _migrate_user_data() -> None:
    """One-time migration: move legacy root-level data files into user_data/."""
    root = _Path(__file__).parent
    for name in _MIGRATE_FILES:
        src = root / name
        dst = _USER_DATA_DIR / name
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
            print(f"[migration] Moved {name} → user_data/{name}")


def setup_logging(debug: bool) -> None:
    root_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        level=root_level,
        stream=sys.stdout,
    )
    # Suppress noisy third-party loggers, especially APScheduler "executed successfully" spam
    for name in (
        "httpx", "telegram", "uvicorn", "uvicorn.access", "uvicorn.error",
        "fastapi", "ngrok",
        "apscheduler",
        "apscheduler.scheduler",
        "apscheduler.schedulers",
        "apscheduler.schedulers.base",
        "apscheduler.schedulers.asyncio",
        "apscheduler.schedulers.background",
        "apscheduler.executors",
        "apscheduler.executors.default",
        "apscheduler.jobstores",
        "apscheduler.jobstores.default",
    ):
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        lg.propagate = False


async def cmd_restart(update, context) -> None:
    """/restart — graceful shutdown, then main() spawns a fresh instance."""
    global _restart_requested
    from bot_handlers import _auth
    if not _auth(update, context):
        return
    await update.message.reply_text("🔄 Перезапускаю бота…")
    _restart_requested = True
    context.application.stop_running()


async def cmd_update(update, context) -> None:
    from bot_handlers import _auth
    if not _auth(update, context):
        return

    msg = await update.message.reply_text("⏳ Выполняю git pull…")
    project_dir = os.path.dirname(os.path.abspath(__file__))

    await asyncio.to_thread(
        lambda: subprocess.run(
            ["git", "config", "--global", "--add",
             "safe.directory", project_dir],
            cwd=project_dir, capture_output=True, text=True,
        )
    )

    result = await asyncio.to_thread(
        lambda: subprocess.run(
            ["git", "pull"],
            cwd=project_dir, capture_output=True, text=True,
        )
    )

    output = (result.stdout + result.stderr).strip() or "(no output)"
    if result.returncode != 0:
        await msg.edit_text(
            f"❌ git pull завершился с ошибкой:\n<pre>{output}</pre>",
            parse_mode="HTML",
        )
        return

    await msg.edit_text(
        f"✅ git pull:\n<pre>{output}</pre>\n\n📦 Устанавливаю зависимости…",
        parse_mode="HTML",
    )

    _pip_candidates = [
        os.path.join(project_dir, "venv", "Scripts", "pip.exe"),
        os.path.join(project_dir, "venv", "bin", "pip"),
    ]
    _pip_exe = next((c for c in _pip_candidates if os.path.exists(c)), None)
    _pip_cmd = (
        [_pip_exe, "install", "-r", "requirements.txt", "--quiet", "--no-warn-script-location"]
        if _pip_exe
        else [sys.executable, "-m", "pip", "install", "-r", "requirements.txt",
              "--quiet", "--no-warn-script-location"]
    )
    pip_result = await asyncio.to_thread(
        lambda: subprocess.run(
            _pip_cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
    )
    pip_output = (pip_result.stdout + pip_result.stderr).strip() or "(no output)"
    if pip_result.returncode != 0:
        await msg.edit_text(
            f"❌ pip install завершился с ошибкой:\n<pre>{pip_output}</pre>",
            parse_mode="HTML",
        )
        return

    await msg.edit_text(
        f"✅ git pull:\n<pre>{output}</pre>\n\n✅ Зависимости установлены\n\n🔄 Перезапускаю…",
        parse_mode="HTML",
    )
    global _restart_requested
    _restart_requested = True
    context.application.stop_running()


async def _init_min_year(ctx) -> None:
    """Fetch oldest Notion record year → store in bot_data['min_year']."""
    from notion_service import NotionService
    cfg     = ctx.bot_data["config"]
    api_key = cfg.get("NOTION_API_KEY")
    txn_db  = cfg.get("NOTION_TRANSACTIONS_DB_ID")
    cat_db  = cfg.get("NOTION_CATEGORIES_DB_ID")
    if not (api_key and txn_db and cat_db):
        return
    try:
        ns = NotionService(api_key, txn_db, cat_db)
        yr = await asyncio.to_thread(ns.get_oldest_transaction_year)
        if yr:
            ctx.bot_data["min_year"] = yr
            logging.getLogger(__name__).info("min_year set to %d", yr)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not fetch min_year: %s", exc)


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start",           "Главное меню"),
        BotCommand("config",          "Настройки"),
        BotCommand("add",             "Добавить транзакцию"),
        BotCommand("create_template", "Создать шаблон"),
        BotCommand("stats",           "Статистика за месяц"),
        BotCommand("feedback",        "Отправить фидбек разработчику"),
        BotCommand("cancel",          "Отмена текущего действия"),
        BotCommand("report",          "Сгенерировать PDF-отчёт за месяц"),
        BotCommand("restart",         "Перезапустить бота"),
        BotCommand("update",          "git pull + перезапуск"),
    ])
    chat_id = app.bot_data["config"].get("TELEGRAM_CHAT_ID")
    if chat_id:
        await send_startup_message(app.bot, chat_id)

    # Pre-warm stats cache on startup so first /stats is instant
    app.job_queue.run_once(_refresh_stats_cache, when=5, name="stats_warmup")
    # Fetch oldest transaction year for /report year range picker
    app.job_queue.run_once(_init_min_year, when=8, name="init_min_year")


async def _post_shutdown(app: Application) -> None:
    """Close ngrok + webhook server on bot stop so port 8080 and the ngrok
    domain are freed before the next instance tries to claim them."""
    await shutdown_ngrok()
    stop_webhook_server()


def _start_webhook(cfg: ConfigManager, bot_data: dict) -> None:
    mono_token = cfg.get("MONOBANK_TOKEN")
    if not mono_token:
        logging.getLogger(__name__).warning(
            "MONOBANK_TOKEN не задан — webhook не запущен."
        )
        bot_data["webhook_started"] = False
        return

    thread = run_webhook_server(
        port=cfg.get_webhook_port(),
        ngrok_token=cfg.get("NGROK_AUTH_TOKEN"),
        mono_token=mono_token,
        account_id=cfg.get("MONOBANK_ACCOUNT_ID"),
        ngrok_domain=cfg.get("NGROK_DOMAIN", ""),
        feedback_port=int(cfg.get("FEEDBACK_PORT", "8765")),
    )
    bot_data["webhook_started"] = True
    bot_data["webhook_thread"]  = thread


def main() -> None:
    _migrate_user_data()
    cfg = ConfigManager()
    setup_logging(False)
    logger = logging.getLogger(__name__)

    token = cfg.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не задан. Добавь в .env и перезапусти.")
        sys.exit(1)
         
    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["config"] = cfg
    app.bot_data["templates"] = TemplateManager()

    _start_webhook(cfg, app.bot_data)

    # Card-notes text handler runs BEFORE ConversationHandlers (group -1)
    # so it can intercept the user's note input without breaking other flows.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_card_notes_text),
        group=-1,
    )

    # Handlers
    app.add_handler(make_settings_handler())
    app.add_handler(make_add_handler())
    app.add_handler(make_templates_handler())
    app.add_handler(make_feedback_handler())
    app.add_handler(CommandHandler("cancel",    cancel_handler))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("report",    cmd_report))
    app.add_handler(CommandHandler("restart",   cmd_restart))
    app.add_handler(CommandHandler("update",    cmd_update))
    app.add_handler(MessageHandler(filters.Regex(r"^📊 Статистика$"), cmd_stats))
    app.add_handler(CallbackQueryHandler(handle_category_callback,   pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(handle_skip_txn_callback,   pattern=r"^skip_txn:"))
    app.add_handler(CallbackQueryHandler(handle_notes_skip_callback, pattern=r"^notes_skip:"))
    app.add_handler(CallbackQueryHandler(handle_income_note_callback, pattern=r"^inc_note:"))
    app.add_handler(CallbackQueryHandler(handle_report_callback,     pattern=r"^rpt:"))

    # Periodic queue drains
    app.job_queue.run_repeating(
        process_webhook_queue,
        interval=1.0,
        first=2.0,
        name="webhook_drain",
    )
    app.job_queue.run_repeating(
        process_trigger_queue,
        interval=1.0,
        first=2.0,
        name="trigger_drain",
    )
    app.job_queue.run_repeating(
        process_api_txn_queue,
        interval=1.0,
        first=2.0,
        name="api_txn_drain",
    )
    # Monthly report — checked daily at 09:00 local time; sends on 1st of month
    import datetime as _dt
    app.job_queue.run_daily(
        send_monthly_report,
        time=_dt.time(9, 0, 0),
        name="monthly_report",
    )
    # Startup banner
    status   = "✅ настроен" if cfg.is_configured() else "⚠️ требует настройки (/config)"
    mode_tag = "🔔 Про" if cfg.get_mode() == "pro" else "🔇 Тихий"
    print(f"\n{'─' * 50}")
    print(f" Monobank Finance Bot v{BOT_VERSION}")
    print(f" Статус: {status}")
    print(f" Режим: {mode_tag}")
    print(f"{'─' * 50}\n")

    # Python 3.10+ requires an explicit event loop before PTB's run_polling
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(drop_pending_updates=True)

    # If /restart or /update triggered a graceful stop, spawn a fresh process.
    # By this point PTB has fully shut down (polling released, _post_shutdown
    # closed ngrok + signalled uvicorn), so the child won't collide.
    if _restart_requested:
        time.sleep(3.0)  # extra buffer for socket release + ngrok cloud cleanup
        subprocess.Popen(
            [sys.executable] + sys.argv,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            close_fds=True,
        )


if __name__ == "__main__":
    main()

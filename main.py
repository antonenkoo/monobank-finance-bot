"""
main.py — Entry point for Monobank Finance Bot.
"""

import asyncio
import logging
import sys

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, CommandHandler, JobQueue

from bot_handlers import (
    cancel_handler,
    debug_handler,
    make_add_handler,
    make_settings_handler,
    make_templates_handler,
    process_webhook_queue,
)
from config_manager import ConfigManager, TemplateManager
from monobank_service import run_webhook_server


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
        "apscheduler.schedulers.background",
        "apscheduler.executors",
        "apscheduler.executors.default",
        "apscheduler.jobstores",
        "apscheduler.jobstores.default",
    ):
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        lg.propagate = False


async def _register_commands(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start",           "Главное меню"),
        BotCommand("config",          "Настройки"),
        BotCommand("add",             "Добавить транзакцию"),
        BotCommand("create_template", "Создать шаблон"),
        BotCommand("debug",           "Вкл/выкл режим отладки"),
        BotCommand("cancel",          "Отмена текущего действия"),
    ])


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
    )
    bot_data["webhook_started"] = True
    bot_data["webhook_thread"]  = thread


def main() -> None:
    cfg = ConfigManager()
    setup_logging(cfg.is_debug())

    logger = logging.getLogger(__name__)

    token = cfg.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не задан. Добавь в .env и перезапусти.")
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(_register_commands)
        .build()
    )
    app.bot_data["config"]    = cfg
    app.bot_data["templates"] = TemplateManager()

    _start_webhook(cfg, app.bot_data)

    # ── Handlers (order matters — more specific first) ────────────────────────
    app.add_handler(make_settings_handler())
    app.add_handler(make_add_handler())
    app.add_handler(make_templates_handler())
    app.add_handler(CommandHandler("debug",  debug_handler))
    app.add_handler(CommandHandler("cancel", cancel_handler))

    # ── Periodic webhook queue drain ──────────────────────────────────────────
    app.job_queue.run_repeating(
        process_webhook_queue,
        interval=1.0,
        first=2.0,
        name="webhook_drain",
    )

    # ── Startup banner ────────────────────────────────────────────────────────
    status    = "✅ настроен" if cfg.is_configured() else "⚠️  требует настройки (/config)"
    debug_tag = " [ОТЛАДКА]" if cfg.is_debug() else ""
    print(f"\n{'─' * 50}")
    print(f"  Monobank Finance Bot запущен{debug_tag}")
    print(f"  Статус: {status}")
    print(f"{'─' * 50}\n")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

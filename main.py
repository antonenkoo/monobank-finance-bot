"""
main.py — Entry point for Monobank Finance Bot.
"""

import asyncio
import logging
import sys

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler

from bot_handlers import (
    cancel_handler,
    handle_category_callback,
    make_add_handler,
    make_settings_handler,
    make_templates_handler,
    process_webhook_queue,
    send_startup_message,
)
from config_manager import BOT_VERSION, ConfigManager, TemplateManager
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


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start",           "Главное меню"),
        BotCommand("config",          "Настройки"),
        BotCommand("add",             "Добавить транзакцию"),
        BotCommand("create_template", "Создать шаблон"),
        BotCommand("cancel",          "Отмена текущего действия"),
    ])
    chat_id = app.bot_data["config"].get("TELEGRAM_CHAT_ID")
    if chat_id:
        await send_startup_message(app.bot, chat_id)


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
        .build()
    )
    app.bot_data["config"] = cfg
    app.bot_data["templates"] = TemplateManager()

    _start_webhook(cfg, app.bot_data)

    # Handlers
    app.add_handler(make_settings_handler())
    app.add_handler(make_add_handler())
    app.add_handler(make_templates_handler())
    app.add_handler(CommandHandler("cancel", cancel_handler))
    app.add_handler(CallbackQueryHandler(handle_category_callback, pattern=r"^cat:"))

    # Periodic webhook queue drain
    app.job_queue.run_repeating(
        process_webhook_queue,
        interval=1.0,
        first=2.0,
        name="webhook_drain",
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

if __name__ == "__main__":
    main()

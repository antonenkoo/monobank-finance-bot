"""
bot_handlers.py — All navigation via ReplyKeyboard (bottom buttons).

Main menu:  ➕ Добавить  |  📋 Шаблоны
            ⚙️ Настройки

Amount display convention (user-facing only):
  • expense internally = negative → shown as positive  e.g. "150 ₴"
  • income  internally = positive → shown as negative  e.g. "-200 ₴"
  Notion always receives the real sign.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config_manager import (
    BOT_VERSION,
    EDITABLE_FIELDS,
    FIELD_HINTS,
    FIELD_LABELS,
    RESTART_REQUIRED_FIELDS,
    ConfigManager,
    TemplateManager,
)
from monobank_service import (
    feedback_notification_queue,
    format_transaction_message,
    get_accounts,
    restart_webhook_server,
    trigger_queue,
    webhook_queue,
)
from notion_service import NotionService
from pending_store import PendingTransactionStore
from smart_categories import SmartCategoryStore

logger = logging.getLogger(__name__)

# ── Persistent stores ──────────────────────────────────────────────────────────
_pending_store = PendingTransactionStore()
_smart_cats    = SmartCategoryStore()

# ── Startup messages ───────────────────────────────────────────────────────────
_STARTUP_MESSAGES = [
    "🚀 <b>Бот запущен!</b>\nWebhook слушает. Notion записывает. Кошелёк плачет.",
    "💸 <b>Я вернулся.</b>\nТвои финансовые грехи снова под моим контролем. Начнём.",
    "🤖 <b>Онлайн.</b>\nЛовлю транзакции, несу в Notion, пью чай. Жизнь удалась.",
    "🌝 <b>Тихо вышел из тени.</b>\nWebhook подключён. Каждая трата записана с невозмутимым спокойствием.",
    "🎯 <b>Финансовый снайпер на позиции.</b>\nМоно дёрнулся — я уже записал. Notion доволен.",
    "💎💸 <b>YOLO FINANCE BOT ACTIVATED SIS</b> 💸💎\nWebhook is bussin fr fr ngl. Notion secured. We vibin. 🔥",
    "🦾 <b>Бот в строю.</b>\nВсе системы запущены. Траты защищены от амнезии. Пока.",
    "😈 <b>Запущен.</b>\nСледить за расходами — моя работа. Осуждать — моё хобби.",
    "🔇 <b>Тихий запуск.</b>\nНикто ничего не заметил. Транзакции тоже. Ладно, шучу — Notion увидел всё.",
    "🥵 <b>Горячий рестарт!</b>\nWebhook задышал, Notion открыт. Поехали транжирить.",
    "🕵️ <b>Слежка возобновлена.</b>\nКаждая гривна под наблюдением. Это не угроза — это фича.",
    "💼 <b>Рабочее место занято.</b>\nТвои траты сами себя не запишут. Почти.",
    "🫀 <b>Живой.</b>\nWebhook бьётся. Notion дышит. Кошелёк стонет.",
    "🎯 <b>На связи.</b>\nТраты — фиксирую. Осуждать — по настроению, но обычно да.",
    "🫠 <b>Всё хорошо.</b>\nWebhook работает. Notion пишет. Бот не спит. Всё хорошо.",
    "🎪 <b>Шоу продолжается.</b>\nКаждая транзакция — новый эпизод. Ты главный герой. Бюджет — злодей.",
    "😏 <b>Снова вместе.</b>\nСоскучился по твоим транзакциям. Не притворяйся что ты нет.",
    "👀 <b>Наблюдаю за каждым твоим движением.</b>\nФинансовым. Исключительно финансовым.",
    "🥵 <b>Готов принять всё что придёт.</b>\nТранзакции. Webhook открыт, Notion ждёт.",
    "💦 <b>Webhook готов.</b>\nNotion разогрет. Бот возбуждён... к работе. Поехали.",
    "🍑 <b>Горячий старт.</b>\nWebhook поднят. Я всегда готов — в отличие от некоторых бюджетов.",
]


_RELEASE_NOTES = """\
🚀 <b>v{version}</b>
──────────────

✨ <b>Новое</b>
• 📝 <b>Обратная связь</b> — кнопка «Фидбек» в главном меню. Отправь баг или идею разработчику прямо из бота

🔧 <b>Исправлено</b>
• Мелкие улучшения стабильности\
"""

_RELEASE_SHOWN_FILE = Path("release_shown.txt")


def _get_shown_version() -> str:
    try:
        return _RELEASE_SHOWN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _mark_release_shown(version: str) -> None:
    try:
        _RELEASE_SHOWN_FILE.write_text(version, encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write release_shown.txt: %s", exc)


async def send_startup_message(bot: Bot, chat_id: str) -> None:
    """Send a random friendly startup message; show release notes once per version."""
    shown_ver = _get_shown_version()

    if shown_ver != BOT_VERSION:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=_RELEASE_NOTES.format(version=BOT_VERSION),
                parse_mode=ParseMode.HTML,
                reply_markup=MAIN_KB,
            )
            _mark_release_shown(BOT_VERSION)
        except Exception as exc:
            logger.error("Failed to send release notes: %s", exc)
        return

    try:
        text = random.choice(_STARTUP_MESSAGES) + f"\n\n<i>v{BOT_VERSION}</i>"
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KB,
        )
    except Exception as exc:
        logger.error("Failed to send startup message: %s", exc)



# ═════════════════════════════════════════════════════════════════════════════
# Static ReplyKeyboards
# ═════════════════════════════════════════════════════════════════════════════

def _kb(*rows: list[str], one_time: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(list(rows), resize_keyboard=True, one_time_keyboard=one_time)


MAIN_KB = _kb(["➕ Добавить", "📋 Шаблоны"], ["📊 Статистика", "⚙️ Настройки"], ["📝 Фидбек"])

def _settings_kb(mode: str, notes: bool = True, smart_cats: bool = True) -> ReplyKeyboardMarkup:
    """Settings menu keyboard with mode-toggle, notes-toggle and smart-cats-toggle buttons."""
    mode_toggle   = "🔔 Про режим"            if mode       == "silent" else "🔇 Тихий режим"
    notes_toggle  = "💬 Заметки: вкл"         if notes      else "💬 Заметки: выкл"
    smart_toggle  = "🧠 Авто-категории: вкл"  if smart_cats else "🧠 Авто-категории: выкл"
    return _kb(["⚙️ Конфигурация"], [mode_toggle], [notes_toggle], [smart_toggle], ["📋 Версия"], ["◀️ Назад"])

SIGN_KB = _kb(["➖ Расход", "➕ Доход"], ["◀️ Назад"])

TIME_KB = _kb(["🕐 Сейчас"], ["📅 Указать дату и время"], ["◀️ Назад"])

NOTES_KB = _kb(["⏭ Пропустить"], ["◀️ Назад"])

SAVE_TPL_KB = _kb(["✅ Да, сохранить как шаблон"], ["❌ Нет, не сохранять"])

TPL_DETAIL_KB = _kb(["✅ Использовать"], ["✏️ Редактировать", "🗑 Удалить"], ["◀️ К шаблонам"])

TPL_EDIT_KB = _kb(["📝 Название", "💰 Сумма"], ["🏷 Категория", "💬 Заметка"], ["◀️ Назад"])

TPL_EDIT_SIGN_KB = _kb(["➖ Расход", "➕ Доход"], ["◀️ Назад"])

TPL_EDIT_NOTES_KB = _kb(["🗑 Очистить заметку"], ["◀️ Назад"])

DELETE_CONFIRM_KB = _kb(["⚠️ Да, удалить"], ["◀️ Отмена"])

USE_TIME_KB = _kb(["🕐 Сейчас"], ["📅 Указать дату и время"], ["◀️ Назад"])

CANCEL_KB = _kb(["◀️ Отменить"])
BACK_KB   = _kb(["◀️ Назад"])


# ── Dynamic keyboards ──────────────────────────────────────────────────────────

def _config_fields_kb() -> ReplyKeyboardMarkup:
    rows = [[FIELD_LABELS[f]] for f in EDITABLE_FIELDS]
    rows.append(["📋 Выбрать аккаунт Monobank"])
    rows.append(["◀️ Назад к настройкам"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _accounts_kb(labels: list[str]) -> ReplyKeyboardMarkup:
    rows = [[lbl] for lbl in labels]
    rows.append(["◀️ Назад"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _categories_kb(cats: list[dict]) -> ReplyKeyboardMarkup:
    rows: list[list[str]] = []
    for i in range(0, len(cats), 2):
        row = [cats[i]["name"]]
        if i + 1 < len(cats):
            row.append(cats[i + 1]["name"])
        rows.append(row)
    rows.append(["⬜ Без категории"])
    rows.append(["◀️ Назад"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)



def _templates_kb(templates: list[dict]) -> ReplyKeyboardMarkup:
    rows = [[t["name"]] for t in templates]
    rows.append(["◀️ Назад"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# ═════════════════════════════════════════════════════════════════════════════
# Conversation states
# ═════════════════════════════════════════════════════════════════════════════

# Settings / Config
SETTINGS_MENU      = 0
CONF_MENU          = 1
CONF_WAIT_VALUE    = 2
CONF_MONO_ACCOUNTS = 3
SETTINGS_VERSION   = 4

# Add / Create-template wizard
ADD_DESC         = 10
ADD_AMOUNT       = 11
ADD_SIGN         = 12
ADD_TIME_CHOICE  = 13
ADD_CUSTOM_TIME  = 14
ADD_NOTES        = 15
ADD_CATEGORY     = 16
ADD_SAVE_CONFIRM = 17
ADD_TPL_NAME     = 18

# Templates browser
TPL_MENU          = 20
TPL_DETAIL        = 21
TPL_USE_TIME      = 22
TPL_CUSTOM_TIME   = 23
TPL_EDIT_MENU     = 24
TPL_EDIT_NAME     = 25
TPL_EDIT_AMOUNT   = 26
TPL_EDIT_SIGN     = 27
TPL_EDIT_CATEGORY = 28
TPL_EDIT_NOTES    = 29
TPL_DELETE_CONFIRM = 30

# Prevent main-menu buttons from leaking into text-input states
_MAIN_BTNS = {"➕ Добавить", "📋 Шаблоны", "⚙️ Настройки", "📊 Статистика", "📝 Фидбек"}
_NOT_MAIN  = ~filters.Regex(r"^(➕ Добавить|📋 Шаблоны|⚙️ Настройки|📊 Статистика|📝 Фидбек)$")
_TXT       = filters.TEXT & ~filters.COMMAND & _NOT_MAIN

# Russian month names for stats output
_MONTHS_RU = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


# ═════════════════════════════════════════════════════════════════════════════
# Core helpers
# ═════════════════════════════════════════════════════════════════════════════

def _cfg(ctx: ContextTypes.DEFAULT_TYPE) -> ConfigManager:
    return ctx.bot_data["config"]

def _tpl(ctx: ContextTypes.DEFAULT_TYPE) -> TemplateManager:
    return ctx.bot_data["templates"]

def _notion(ctx: ContextTypes.DEFAULT_TYPE) -> Optional[NotionService]:
    cfg = _cfg(ctx)
    if not (cfg.get("NOTION_API_KEY") and cfg.get("NOTION_TRANSACTIONS_DB_ID")
            and cfg.get("NOTION_CATEGORIES_DB_ID")):
        return None
    return NotionService(
        api_key=cfg.get("NOTION_API_KEY"),
        transactions_db_id=cfg.get("NOTION_TRANSACTIONS_DB_ID"),
        categories_db_id=cfg.get("NOTION_CATEGORIES_DB_ID"),
        remaining_prop=cfg.get("NOTION_REMAINING_PROP", "Remaining"),
        limit_prop=cfg.get("NOTION_LIMIT_PROP", ""),
    )

def _auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = _cfg(ctx).get("TELEGRAM_CHAT_ID")
    return not chat_id or str(update.effective_chat.id) == chat_id

def _try_restart_webhook(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg  = _cfg(ctx)
    mono = cfg.get("MONOBANK_TOKEN")
    if not mono:
        return
    t = restart_webhook_server(
        port=cfg.get_webhook_port(),
        ngrok_token=cfg.get("NGROK_AUTH_TOKEN"),
        mono_token=mono,
        account_id=cfg.get("MONOBANK_ACCOUNT_ID"),
        ngrok_domain=cfg.get("NGROK_DOMAIN", ""),
    )
    ctx.bot_data["webhook_started"] = True
    ctx.bot_data["webhook_thread"]  = t


# ── Amount display ─────────────────────────────────────────────────────────────

def _disp(amount: float) -> str:
    """expense (neg internally) → shown as positive; income (pos internally) → shown as negative."""
    return f"{-amount:,.2f} ₴"

def _disp_with_type(amount: float) -> str:
    t = "расход" if amount < 0 else "доход"
    return f"{_disp(amount)} ({t})"


# ── Main menu text ─────────────────────────────────────────────────────────────

def _menu_text(cfg: ConfigManager) -> str:
    mode = cfg.get_mode()
    mode_label = "🔔 Про" if mode == "pro" else "🔇 Тихий"
    return (
        "🏠 <b>Главное меню</b>\n\n"
        "Используй кнопки внизу экрана.\n\n"
        f"Режим: {mode_label}"
    )

async def _main_menu(msg: Message, cfg: ConfigManager) -> None:
    await msg.reply_text(_menu_text(cfg), parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)


# ── Category loader ────────────────────────────────────────────────────────────

async def _load_cats(ctx: ContextTypes.DEFAULT_TYPE) -> tuple[list[dict], dict[str, str]]:
    if "cat_map" not in ctx.user_data:
        notion = _notion(ctx)
        if notion:
            cats = await asyncio.to_thread(notion.get_categories)
        else:
            cats = []
        ctx.user_data["cats"]    = cats
        ctx.user_data["cat_map"] = {c["name"]: c["id"] for c in cats}
    return ctx.user_data["cats"], ctx.user_data["cat_map"]


# ═════════════════════════════════════════════════════════════════════════════
# /start  and  /debug  (standalone — not inside any conversation)
# ═════════════════════════════════════════════════════════════════════════════

async def start_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = _cfg(ctx)
    uid = str(update.effective_chat.id)
    if not cfg.get("TELEGRAM_CHAT_ID"):
        cfg.set("TELEGRAM_CHAT_ID", uid)
    if not _auth(update, ctx):
        return ConversationHandler.END

    if not cfg.is_configured():
        await update.message.reply_text(
            "👋 <b>Добро пожаловать в Monobank Finance Bot!</b>\n\n"
            "Для начала настрой бота — нажми <b>⚙️ Настройки</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KB,
        )
        return ConversationHandler.END

    await _main_menu(update.message, cfg)
    return ConversationHandler.END




# ═════════════════════════════════════════════════════════════════════════════
# Settings / Config conversation
# ═════════════════════════════════════════════════════════════════════════════

def _cfg_status(cfg: ConfigManager) -> str:
    lines = ["⚙️ <b>Конфигурация бота</b>\n"]
    for f in EDITABLE_FIELDS:
        lines.append(f"<b>{FIELD_LABELS[f]}:</b> {cfg.mask(f)}")
    missing = [f for f in cfg.missing_fields() if f in EDITABLE_FIELDS]
    if missing:
        lines.append("\n⚠️ Не заданы: " + ", ".join(FIELD_LABELS[f] for f in missing))
    else:
        lines.append("\n✅ Все поля заданы")
    return "\n".join(lines)


# ── Version changelog ─────────────────────────────────────────────────────────

_CHANGELOG: dict[str, str] = {
    "v1.0": (
        "🏁 <b>v1.0 — Начало</b>\n\n"
        "• Webhook от Monobank — каждая трата сразу в боте\n"
        "• Сохранение транзакций в Notion (название, сумма, дата, категория)\n"
        "• Ручное добавление расходов и доходов через /add\n"
        "• Настройка токенов прямо в боте через /config\n"
        "• Выбор аккаунта Monobank из списка"
    ),
    "v1.1": (
        "📋 <b>v1.1 — Шаблоны и режимы</b>\n\n"
        "• Шаблоны — сохраняй и запускай повторяющиеся транзакции в один клик\n"
        "• Режим 🔔 Про — каждая трата → сообщение с выбором категории\n"
        "• Режим 🔇 Тихий — Monobank сохраняется автоматически без уведомлений\n"
        "• Остаток по категории после каждого сохранения\n"
        "• Стартовое сообщение при запуске бота\n"
        "⚙️ Новое в конфиге: <code>NOTION_REMAINING_PROP</code>"
    ),
    "v1.2": (
        "💰 <b>v1.2 — Общий бюджет</b>\n\n"
        "• Общий остаток по всему бюджету (сумма по всем категориям)\n"
        "• После каждой транзакции показывается и остаток по категории, и общий\n"
        "• <code>NOTION_REMAINING_PROP</code> стал настраиваемым — можно указать свою колонку"
    ),
    "v1.2.1": (
        "💬 <b>v1.2.1 — Заметки и отмена</b>\n\n"
        "• Заметки к транзакциям Monobank — бот спрашивает комментарий после выбора категории\n"
        "• Кнопка ❌ Не сохранять — отменить сохранение прямо из сообщения (Про режим)\n"
        "• Переключатель заметок в ⚙️ Настройки (можно выключить)\n"
        "• Стартовое сообщение теперь показывает главное меню\n"
        "• Расширен набор стартовых фраз — 21 штука"
    ),
    "v1.4.0": (
        "🚀 <b>v1.4.0 — Статистика и авто-категории</b>\n\n"
        "• 🧠 Авто-категории — бот запоминает твой выбор и подсказывает категорию по описанию платежа\n"
        "• 📊 Статистика /stats — расходы и доходы текущего месяца с визуальными прогресс-барами\n"
        "• 🚨 Уведомления о лимите — предупреждает когда расходы по категории близки к лимиту\n"
        "• ⚡ HTTP-триггеры — шаблоны можно запускать внешним запросом (с ярлыка на телефоне)\n"
        "• Подсказка даты при вводе вручную теперь показывает текущее время\n"
        "• Убрали глючащий прогресс-бар при ручном добавлении\n"
        "⚙️ Новое в конфиге: <code>NOTION_LIMIT_PROP</code> — колонка с месячным лимитом по категории"
    ),
    "v1.4.1": (
        "📝 <b>v1.4.1 — Обратная связь</b>\n\n"
        "• 📝 Фидбек — кнопка в главном меню для отправки багов и идей разработчику\n"
        "• Сообщение остаётся в чате, пользователь получает подтверждение\n"
        "• Фидбек приходит на ngrok разработчика и сохраняется локально\n"
        "⚙️ Новое в конфиге: <code>DEVELOPER_FEEDBACK_URL</code> — ngrok URL разработчика"
    ),
}

_VERSIONS_ORDERED = ["v1.4.1", "v1.4.0", "v1.2.1", "v1.2", "v1.1", "v1.0"]

_VERSION_KB = _kb(
    ["v1.4.1", "v1.4.0"],
    ["v1.2.1", "v1.2"],
    ["v1.1",   "v1.0"],
    ["◀️ Назад к настройкам"],
)


async def settings_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    cfg = _cfg(ctx)
    await update.message.reply_text(
        "⚙️ <b>Настройки</b>", parse_mode=ParseMode.HTML,
        reply_markup=_settings_kb(cfg.get_mode(), cfg.get_notes_enabled(), cfg.get_smart_cats_enabled())
    )
    return SETTINGS_MENU


async def settings_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t   = update.message.text.strip()
    cfg = _cfg(ctx)

    def _kb_now() -> ReplyKeyboardMarkup:
        return _settings_kb(cfg.get_mode(), cfg.get_notes_enabled(), cfg.get_smart_cats_enabled())

    if t == "◀️ Назад":
        await _main_menu(update.message, cfg)
        return ConversationHandler.END

    if t == "⚙️ Конфигурация":
        await update.message.reply_text(
            _cfg_status(cfg), parse_mode=ParseMode.HTML, reply_markup=_config_fields_kb()
        )
        return CONF_MENU

    if t == "🔔 Про режим":
        cfg.set_mode("pro")
        await update.message.reply_text(
            "🔔 <b>Про режим включён!</b>\n\n"
            "Каждая транзакция Monobank → сообщение в чат с выбором категории.",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_now(),
        )
        return SETTINGS_MENU

    if t == "🔇 Тихий режим":
        cfg.set_mode("silent")
        await update.message.reply_text(
            "🔇 <b>Тихий режим включён!</b>\n\n"
            "Транзакции Monobank сохраняются в Notion автоматически, без уведомлений.",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_now(),
        )
        return SETTINGS_MENU

    if t == "💬 Заметки: вкл":
        cfg.set_notes_enabled(False)
        await update.message.reply_text(
            "💬 <b>Заметки отключены.</b>\n\n"
            "Транзакции по карте сохраняются сразу после выбора категории.",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_now(),
        )
        return SETTINGS_MENU

    if t == "💬 Заметки: выкл":
        cfg.set_notes_enabled(True)
        await update.message.reply_text(
            "💬 <b>Заметки включены.</b>\n\n"
            "После выбора категории бот попросит добавить заметку к транзакции.",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_now(),
        )
        return SETTINGS_MENU

    if t == "🧠 Авто-категории: вкл":
        cfg.set_smart_cats_enabled(False)
        await update.message.reply_text(
            "🧠 <b>Авто-категории отключены.</b>\n\n"
            "Подсказка по истории больше не показывается.",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_now(),
        )
        return SETTINGS_MENU

    if t == "🧠 Авто-категории: выкл":
        cfg.set_smart_cats_enabled(True)
        await update.message.reply_text(
            "🧠 <b>Авто-категории включены!</b>\n\n"
            "Бот предлагает категорию на основе предыдущих транзакций от этого же магазина.",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_now(),
        )
        return SETTINGS_MENU

    if t == "📋 Версия":
        await update.message.reply_text(
            f"📋 <b>История версий</b>\n\nТекущая: <b>v{BOT_VERSION}</b>\n\nВыбери версию:",
            parse_mode=ParseMode.HTML,
            reply_markup=_VERSION_KB,
        )
        return SETTINGS_VERSION

    await update.message.reply_text("Выбери пункт:", reply_markup=_kb_now())
    return SETTINGS_MENU


async def settings_version(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад к настройкам":
        cfg = _cfg(ctx)
        await update.message.reply_text(
            "⚙️ <b>Настройки</b>", parse_mode=ParseMode.HTML,
            reply_markup=_settings_kb(cfg.get_mode(), cfg.get_notes_enabled(), cfg.get_smart_cats_enabled()),
        )
        return SETTINGS_MENU

    if t in _CHANGELOG:
        await update.message.reply_text(
            _CHANGELOG[t],
            parse_mode=ParseMode.HTML,
            reply_markup=_VERSION_KB,
        )
        return SETTINGS_VERSION

    return SETTINGS_VERSION


async def conf_menu_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t   = update.message.text.strip()
    cfg = _cfg(ctx)

    if t == "◀️ Назад к настройкам":
        await update.message.reply_text("⚙️ <b>Настройки</b>", parse_mode=ParseMode.HTML,
                                         reply_markup=_settings_kb(cfg.get_mode(), cfg.get_notes_enabled(), cfg.get_smart_cats_enabled()))
        return SETTINGS_MENU

    if t == "📋 Выбрать аккаунт Monobank":
        mono = cfg.get("MONOBANK_TOKEN")
        if not mono:
            await update.message.reply_text(
                "⚠️ Сначала задай Monobank Personal Token.", reply_markup=_config_fields_kb()
            )
            return CONF_MENU

        await update.message.reply_text("⏳ Загружаю список карт из Monobank…")
        accounts = await asyncio.to_thread(get_accounts, mono)

        if not accounts:
            await update.message.reply_text(
                "❌ Не удалось получить карты.\n\n"
                "• Проверь MONOBANK_TOKEN\n"
                "• API Monobank: не чаще 1 раза в 60 с",
                reply_markup=_config_fields_kb(),
            )
            return CONF_MENU

        acc_map: dict[str, str] = {}
        for acc in accounts:
            pans    = acc.get("maskedPan", [])
            pan     = pans[0] if pans else "—"
            atype   = acc.get("type", "?")
            balance = acc.get("balance", 0) / 100
            mark    = " ✓" if acc["id"] == cfg.get("MONOBANK_ACCOUNT_ID") else ""
            label   = f"{pan} ({atype}) {balance:,.0f}₴{mark}"
            acc_map[label] = acc["id"]

        ctx.user_data["acc_map"] = acc_map
        await update.message.reply_text(
            "Выбери карту (✓ — текущая):", reply_markup=_accounts_kb(list(acc_map))
        )
        return CONF_MONO_ACCOUNTS

    # Field edit?
    field = next((f for f in EDITABLE_FIELDS if FIELD_LABELS[f] == t), None)
    if field:
        ctx.user_data["editing_field"] = field
        hint    = FIELD_HINTS.get(field, "")
        current = cfg.mask(field)
        text    = f"✏️ <b>{FIELD_LABELS[field]}</b>\n\nТекущее: {current}"
        if hint:
            text += f"\n\n💡 <i>{hint}</i>"
        text += "\n\nВведи новое значение:"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                         reply_markup=BACK_KB)
        return CONF_WAIT_VALUE

    await update.message.reply_text("Выбери поле из списка:", reply_markup=_config_fields_kb())
    return CONF_MENU


async def conf_wait_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        await update.message.reply_text(
            _cfg_status(_cfg(ctx)), parse_mode=ParseMode.HTML, reply_markup=_config_fields_kb()
        )
        return CONF_MENU

    field = ctx.user_data.get("editing_field")
    if not field:
        await update.message.reply_text("❌ Ошибка. Используй /config снова.")
        return ConversationHandler.END

    cfg   = _cfg(ctx)
    label = FIELD_LABELS.get(field, field)
    cfg.set(field, t)

    note = ""
    if field in RESTART_REQUIRED_FIELDS:
        await asyncio.to_thread(_try_restart_webhook, ctx)
        note = "\n🔄 Webhook-сервер перезапущен."

    await update.message.reply_text(f"✅ <b>{label}</b> сохранён.{note}",
                                     parse_mode=ParseMode.HTML)

    if cfg.is_configured():
        await update.message.reply_text(
            "🎉 <b>Все поля заданы!</b>\n\n" + cfg.config_summary() + "\n\n" + _menu_text(cfg),
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KB,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        _cfg_status(cfg), parse_mode=ParseMode.HTML, reply_markup=_config_fields_kb()
    )
    return CONF_MENU


async def conf_pick_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t   = update.message.text.strip()
    cfg = _cfg(ctx)

    if t == "◀️ Назад":
        await update.message.reply_text(
            _cfg_status(cfg), parse_mode=ParseMode.HTML, reply_markup=_config_fields_kb()
        )
        return CONF_MENU

    acc_id = ctx.user_data.get("acc_map", {}).get(t)
    if not acc_id:
        await update.message.reply_text(
            "Выбери карту из списка:", reply_markup=_accounts_kb(list(ctx.user_data.get("acc_map", {})))
        )
        return CONF_MONO_ACCOUNTS

    cfg.set("MONOBANK_ACCOUNT_ID", acc_id)
    await update.message.reply_text(f"✅ Аккаунт сохранён.", reply_markup=_config_fields_kb())

    if cfg.is_configured():
        await update.message.reply_text(
            "🎉 <b>Все поля заданы!</b>\n\n" + cfg.config_summary() + "\n\n" + _menu_text(cfg),
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KB,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        _cfg_status(cfg), parse_mode=ParseMode.HTML, reply_markup=_config_fields_kb()
    )
    return CONF_MENU


async def settings_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Отменено.")
    await _main_menu(update.message, _cfg(ctx))
    return ConversationHandler.END


def make_settings_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start",  start_handler),
            CommandHandler("config", settings_entry),
            MessageHandler(filters.Regex(r"^⚙️ Настройки$"), settings_entry),
        ],
        states={
            SETTINGS_MENU: [
                MessageHandler(_TXT, settings_choice),
            ],
            CONF_MENU: [
                MessageHandler(_TXT, conf_menu_choice),
            ],
            CONF_WAIT_VALUE: [
                MessageHandler(_TXT, conf_wait_value),
            ],
            CONF_MONO_ACCOUNTS: [
                MessageHandler(_TXT, conf_pick_account),
            ],
            SETTINGS_VERSION: [
                MessageHandler(_TXT, settings_version),
            ],
        },
        fallbacks=[CommandHandler("cancel", settings_cancel)],
        per_user=True, per_chat=True, per_message=False,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Add / Create-template wizard
# ═════════════════════════════════════════════════════════════════════════════

# ── Step show helpers ──────────────────────────────────────────────────────────

async def _show_desc(msg: Message, mode: str) -> int:
    steps = "5" if mode == "add" else "4"
    title = "Добавление транзакции" if mode == "add" else "Создание шаблона"
    await msg.reply_text(
        f"💵 <b>{title}</b>\n\n"
        f"Шаг 1/{steps}: Введи {'описание' if mode == 'add' else 'название'}.\n"
        "<i>Например: Продукты, Кофе, Такси</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=CANCEL_KB,
    )
    return ADD_DESC

async def _show_amount(msg: Message, mode: str, current: Optional[float] = None) -> int:
    steps = "5" if mode == "add" else "4"
    hint  = f"\n(текущее: {current:.2f})" if current else ""
    await msg.reply_text(
        f"Шаг 2/{steps}: Введи сумму{hint}\n\n"
        "Введи положительное число. Расход/доход выберешь на следующем шаге.",
        reply_markup=BACK_KB,
    )
    return ADD_AMOUNT

async def _show_sign(msg: Message, mode: str, amount_abs: float) -> int:
    await msg.reply_text(
        f"💰 <b>{amount_abs:.2f} ₴</b> — это расход или доход?",
        parse_mode=ParseMode.HTML,
        reply_markup=SIGN_KB,
    )
    return ADD_SIGN

async def _show_time(msg: Message) -> int:
    await msg.reply_text("Шаг 3/5: Выбери время транзакции:", reply_markup=TIME_KB)
    return ADD_TIME_CHOICE

async def _show_notes(msg: Message, mode: str) -> int: # no need this step
    step  = "4" if mode == "add" else "3"
    steps = "5" if mode == "add" else "4"
    await msg.reply_text(
        f"Шаг {step}/{steps}: Добавь заметку (необязательно).\n"
        "Введи текст или нажми «⏭ Пропустить»:",
        reply_markup=NOTES_KB,
    )
    return ADD_NOTES

async def _show_category(msg: Message, ctx: ContextTypes.DEFAULT_TYPE, mode: str) -> int:
    cats, _ = await _load_cats(ctx)
    step    = "5" if mode == "add" else "4"
    if not cats:
        await msg.reply_text(
            f"Шаг {step}: Категории не найдены в Notion.\n"
            "Продолжим без категории:",
            reply_markup=_kb(["⬜ Без категории"], ["◀️ Назад"]),
        )
    else:
        await msg.reply_text(
            f"Шаг {step}: Выбери категорию:",
            reply_markup=_categories_kb(cats),
        )
    return ADD_CATEGORY


# ── Entry points ───────────────────────────────────────────────────────────────

async def add_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["mode"] = "add"
    asyncio.create_task(_load_cats(ctx))  # preload while user types description
    return await _show_desc(update.message, "add")


async def create_template_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["mode"] = "template"
    asyncio.create_task(_load_cats(ctx))  # preload while user types name
    return await _show_desc(update.message, "template")


# ── Step handlers ──────────────────────────────────────────────────────────────

async def add_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Отменить":
        ctx.user_data.clear()
        await _main_menu(update.message, _cfg(ctx))
        return ConversationHandler.END

    ctx.user_data["add_desc"] = t
    return await _show_amount(update.message, mode)


async def add_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Назад":
        return await _show_desc(update.message, mode)

    raw = t.replace(",", ".")
    try:
        amt = abs(float(raw))
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Введи число, например <code>150</code>:",
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_KB,
        )
        return ADD_AMOUNT

    ctx.user_data["add_amount_abs"] = amt
    return await _show_sign(update.message, mode, amt)


async def add_sign(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")
    amt  = ctx.user_data.get("add_amount_abs", 0.0)

    if t == "◀️ Назад":
        return await _show_amount(update.message, mode, current=amt)

    if t == "➖ Расход":
        ctx.user_data["add_amount"]   = -amt
        ctx.user_data["add_sign_str"] = "расход"
    elif t == "➕ Доход":
        ctx.user_data["add_amount"]   = amt
        ctx.user_data["add_sign_str"] = "доход"
    else:
        await update.message.reply_text("Выбери кнопку на клавиатуре:", reply_markup=SIGN_KB)
        return ADD_SIGN

    if mode == "template":
        # Templates don't need a date/time
        return await _show_category(update.message, ctx, mode)

    return await _show_time(update.message)


async def add_time_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Назад":
        return await _show_sign(update.message, mode, ctx.user_data.get("add_amount_abs", 0))

    if t == "🕐 Сейчас":
        ctx.user_data["add_dt"] = datetime.now(tz=timezone.utc)
        return await _show_category(update.message, ctx, mode)

    if t == "📅 Указать дату и время":
        _now_example = datetime.now(tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
        await update.message.reply_text(
            "Введи дату и время в формате:\n"
            f"<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\nПример: <code>{_now_example}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_KB,
        )
        return ADD_CUSTOM_TIME

    await update.message.reply_text("Выбери кнопку:", reply_markup=TIME_KB)
    return ADD_TIME_CHOICE


async def add_custom_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Назад":
        return await _show_time(update.message)

    try:
        ctx.user_data["add_dt"] = datetime.strptime(t, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        await update.message.reply_text(
            f"❌ Неверный формат. Пример: <code>{datetime.now(tz=timezone.utc).strftime('%d.%m.%Y %H:%M')}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_KB,
        )
        return ADD_CUSTOM_TIME

    return await _show_category(update.message, ctx, mode)


async def add_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Назад":
        if mode == "template":
            return await _show_sign(update.message, mode, ctx.user_data.get("add_amount_abs", 0))
        return await _show_time(update.message)

    ctx.user_data["add_notes"] = "" if t == "⏭ Пропустить" else t
    return await _show_category(update.message, ctx, mode)


async def add_category_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle category selection from the ReplyKeyboard in the manual add/template flow."""
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Назад":
        if mode == "template":
            return await _show_sign(update.message, mode, ctx.user_data.get("add_amount_abs", 0))
        return await _show_time(update.message)

    cats, cat_map = await _load_cats(ctx)

    if t == "⬜ Без категории":
        category_id   = None
        category_name = "—"
    else:
        category_id = cat_map.get(t)
        if not category_id:
            await update.message.reply_text(
                "Выбери категорию из списка:", reply_markup=_categories_kb(cats)
            )
            return ADD_CATEGORY
        category_name = t

    ctx.user_data["add_cat_id"]   = category_id
    ctx.user_data["add_cat_name"] = category_name

    if mode == "template":
        return await _finalize_template(update.message, ctx)
    else:
        return await _finalize_add(update.message, ctx)


# ── Finalize helpers ───────────────────────────────────────────────────────────

async def _finalize_template(msg: Message, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    desc  = ctx.user_data["add_desc"]
    amt   = ctx.user_data["add_amount"]
    notes = ctx.user_data.get("add_notes", "")
    cid   = ctx.user_data["add_cat_id"]
    cname = ctx.user_data["add_cat_name"]

    _tpl(ctx).add(desc, amt, cid, cname, notes)

    await msg.reply_text(
        f"📋 <b>Шаблон сохранён!</b>\n\n"
        f"<b>{desc}</b>\n"
        f"💰 {_disp_with_type(amt)}\n"
        f"🏷 {cname}"
        + (f"\n💬 {notes}" if notes else ""),
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KB,
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def _finalize_add(msg: Message, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    desc   = ctx.user_data["add_desc"]
    amt    = ctx.user_data["add_amount"]
    dt     = ctx.user_data.get("add_dt", datetime.now(tz=timezone.utc))
    notes  = ctx.user_data.get("add_notes", "")
    cid    = ctx.user_data["add_cat_id"]
    cname  = ctx.user_data["add_cat_name"]
    notion = _notion(ctx)

    cname_display = cname if cname != "—" else "Без категории"

    success        = False
    remaining_text = ""

    if notion:
        await msg.reply_text(
            f"<b>{desc}</b>\n"
            f"💰 {_disp_with_type(amt)}\n"
            f"🏷 {cname_display}\n\n"
            "⏳ Сохраняю…",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        success = await asyncio.to_thread(notion.create_transaction, desc, -amt, dt, cid, notes)

    if success and cid:
        try:
            cat_rem, total_rem = await asyncio.wait_for(
                asyncio.gather(
                    asyncio.to_thread(notion.get_category_remaining, cid),
                    asyncio.to_thread(notion.get_total_remaining),
                ),
                timeout=8,
            )
            if cat_rem is not None:
                remaining_text = f"\n💼 На месяц осталось по категории: {cat_rem:,.2f} ₴"
            if total_rem is not None:
                remaining_text += f"\n💰 Всего в бюджете осталось: {total_rem:,.2f} ₴"
        except Exception as e:
            logger.exception("Failed to get category remaining: %s", e)

    if success:
        await msg.reply_text(
            f"✅ <b>Транзакция сохранена в Notion!</b>\n\n"
            f"<b>{desc}</b>\n"
            f"💰 {_disp_with_type(amt)}\n"
            f"📅 {dt.strftime('%d.%m.%Y %H:%M')}\n"
            f"🏷 {cname_display}"
            + (f"\n💬 {notes}" if notes else "")
            + remaining_text,
            parse_mode=ParseMode.HTML,
            reply_markup=SAVE_TPL_KB,
        )
        return ADD_SAVE_CONFIRM
    else:
        await msg.reply_text(
            "⚠️ <b>Не удалось сохранить в Notion.</b>\n"
            "Проверь настройки и права интеграции.",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KB,
        )
        ctx.user_data.clear()
        return ConversationHandler.END

# ── Save-as-template offer ─────────────────────────────────────────────────────

async def add_save_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "✅ Да, сохранить как шаблон":
        desc = ctx.user_data.get("add_desc", "")
        await update.message.reply_text(
            "Введи название шаблона или нажми кнопку, чтобы использовать описание транзакции:",
            reply_markup=_kb([f"💾 Использовать «{desc}»"], ["◀️ Отмена"]),
        )
        return ADD_TPL_NAME

    # "Нет" or anything else
    await _main_menu(update.message, _cfg(ctx))
    ctx.user_data.clear()
    return ConversationHandler.END


async def add_tpl_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Отмена":
        await _main_menu(update.message, _cfg(ctx))
        ctx.user_data.clear()
        return ConversationHandler.END

    desc  = ctx.user_data.get("add_desc", "")
    name  = desc if t.startswith("💾 Использовать") else t
    amt   = ctx.user_data["add_amount"]
    notes = ctx.user_data.get("add_notes", "")
    cid   = ctx.user_data["add_cat_id"]
    cname = ctx.user_data["add_cat_name"]

    _tpl(ctx).add(name, amt, cid, cname, notes)

    await update.message.reply_text(
        f"📋 Шаблон <b>«{name}»</b> сохранён!",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KB,
    )
    ctx.user_data.clear()
    return ConversationHandler.END


# ── Cancel ─────────────────────────────────────────────────────────────────────

async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("❌ Отменено.")
    await _main_menu(update.message, _cfg(ctx))
    return ConversationHandler.END


def make_add_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("add",             add_entry),
            CommandHandler("create_template", create_template_entry),
            MessageHandler(filters.Regex(r"^➕ Добавить$"), add_entry),
        ],
        states={
            ADD_DESC:         [MessageHandler(_TXT, add_desc)],
            ADD_AMOUNT:       [MessageHandler(_TXT, add_amount)],
            ADD_SIGN:         [MessageHandler(_TXT, add_sign)],
            ADD_TIME_CHOICE:  [MessageHandler(_TXT, add_time_choice)],
            ADD_CUSTOM_TIME:  [MessageHandler(_TXT, add_custom_time)],
          # ADD_NOTES:        [MessageHandler(_TXT, add_notes)],
            ADD_CATEGORY:     [MessageHandler(_TXT, add_category_text)],
            ADD_SAVE_CONFIRM: [MessageHandler(_TXT, add_save_confirm)],
            ADD_TPL_NAME:     [MessageHandler(_TXT, add_tpl_name)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
        per_user=True, per_chat=True, per_message=False,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Templates browser
# ═════════════════════════════════════════════════════════════════════════════

def _tpl_info_text(tpl: dict) -> str:
    amt   = tpl["amount"]
    notes = tpl.get("notes", "")
    lines = [
        f"📋 <b>{tpl['name']}</b>\n",
        f"💰 {_disp_with_type(amt)}",
        f"🏷 {tpl.get('category_name', '—')}",
    ]
    if notes:
        lines.append(f"💬 {notes}")
    return "\n".join(lines)


async def _show_tpl_list(msg: Message, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    templates = _tpl(ctx).get_all()
    # Rebuild the name→id map fresh
    ctx.user_data["tpl_map"] = {t["name"]: t["id"] for t in templates}

    if not templates:
        await msg.reply_text(
            "📋 <b>Шаблоны</b>\n\nШаблонов пока нет.\n\n"
            "Создай через /create_template или нажми ➕ Добавить и в конце выбери сохранение как шаблон.",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb(["◀️ Назад"]),
        )
    else:
        await msg.reply_text(
            "📋 <b>Шаблоны</b>\n\nВыбери шаблон:",
            parse_mode=ParseMode.HTML,
            reply_markup=_templates_kb(templates),
        )
    return TPL_MENU


async def _show_tpl_detail(msg: Message, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    tpl_id = ctx.user_data.get("tpl_id")
    tpl    = _tpl(ctx).get(tpl_id)
    if not tpl:
        await msg.reply_text("Шаблон не найден.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    await msg.reply_text(
        _tpl_info_text(tpl) + "\n\nЧто сделать?",
        parse_mode=ParseMode.HTML,
        reply_markup=TPL_DETAIL_KB,
    )
    return TPL_DETAIL


async def _show_tpl_edit_menu(msg: Message, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    tpl_id = ctx.user_data.get("tpl_id")
    tpl    = _tpl(ctx).get(tpl_id)
    if not tpl:
        await msg.reply_text("Шаблон не найден.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    await msg.reply_text(
        f"✏️ <b>Редактирование шаблона</b>\n\n{_tpl_info_text(tpl)}\n\nЧто изменить?",
        parse_mode=ParseMode.HTML,
        reply_markup=TPL_EDIT_KB,
    )
    return TPL_EDIT_MENU


# ── Entry ──────────────────────────────────────────────────────────────────────

async def templates_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    ctx.user_data.clear()
    return await _show_tpl_list(update.message, ctx)


# ── TPL_MENU ───────────────────────────────────────────────────────────────────

async def tpl_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        await _main_menu(update.message, _cfg(ctx))
        return ConversationHandler.END

    tpl_id = ctx.user_data.get("tpl_map", {}).get(t)
    if not tpl_id:
        await update.message.reply_text("Выбери шаблон из списка:")
        return TPL_MENU

    ctx.user_data["tpl_id"] = tpl_id
    return await _show_tpl_detail(update.message, ctx)


# ── TPL_DETAIL ─────────────────────────────────────────────────────────────────

async def tpl_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ К шаблонам":
        return await _show_tpl_list(update.message, ctx)

    if t == "✅ Использовать":
        await update.message.reply_text("Выбери время транзакции:", reply_markup=USE_TIME_KB)
        return TPL_USE_TIME

    if t == "✏️ Редактировать":
        return await _show_tpl_edit_menu(update.message, ctx)

    if t == "🗑 Удалить":
        tpl = _tpl(ctx).get(ctx.user_data.get("tpl_id", ""))
        name = tpl["name"] if tpl else "шаблон"
        await update.message.reply_text(
            f"⚠️ Удалить шаблон <b>«{name}»</b>?\nЭто действие нельзя отменить.",
            parse_mode=ParseMode.HTML,
            reply_markup=DELETE_CONFIRM_KB,
        )
        return TPL_DELETE_CONFIRM

    await update.message.reply_text("Выбери действие:", reply_markup=TPL_DETAIL_KB)
    return TPL_DETAIL


# ── TPL_USE_TIME ───────────────────────────────────────────────────────────────

async def tpl_use_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        return await _show_tpl_detail(update.message, ctx)

    if t == "🕐 Сейчас":
        return await _apply_template(update.message, ctx, datetime.now(tz=timezone.utc))

    if t == "📅 Указать дату и время":
        _now_example = datetime.now(tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
        await update.message.reply_text(
            f"Введи дату и время:\n<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\nПример: <code>{_now_example}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_KB,
        )
        return TPL_CUSTOM_TIME

    await update.message.reply_text("Выбери время:", reply_markup=USE_TIME_KB)
    return TPL_USE_TIME


async def tpl_custom_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        await update.message.reply_text("Выбери время:", reply_markup=USE_TIME_KB)
        return TPL_USE_TIME

    try:
        dt = datetime.strptime(t, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        await update.message.reply_text(
            f"❌ Неверный формат. Пример: <code>{datetime.now(tz=timezone.utc).strftime('%d.%m.%Y %H:%M')}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_KB,
        )
        return TPL_CUSTOM_TIME

    return await _apply_template(update.message, ctx, dt)


async def _apply_template(msg: Message, ctx: ContextTypes.DEFAULT_TYPE, dt: datetime) -> int:
    tpl = _tpl(ctx).get(ctx.user_data.get("tpl_id", ""))
    if not tpl:
        await msg.reply_text("Шаблон не найден.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    notion = _notion(ctx)
    success = False
    remaining_text = ""

    if notion:
        success = await asyncio.to_thread(
            notion.create_transaction,
            tpl["name"], -tpl["amount"], dt,
            tpl.get("category_id"), tpl.get("notes", ""),
        )

        if success and tpl.get("category_id"):
            await asyncio.sleep(0.7)
            cat_rem, total_rem = await asyncio.gather(
                asyncio.to_thread(notion.get_category_remaining, tpl["category_id"]),
                asyncio.to_thread(notion.get_total_remaining),
            )
            if cat_rem is not None:
                remaining_text = f"\n💼 На месяц осталось по категории: {cat_rem:,.2f} ₴"
            if total_rem is not None:
                remaining_text += f"\n💰 Всего в бюджете осталось: {total_rem:,.2f} ₴"

    if success:
        await msg.reply_text(
            f"✅ <b>Транзакция сохранена!</b>\n\n"
            f"<b>{tpl['name']}</b>\n"
            f"💰 {_disp_with_type(tpl['amount'])}\n"
            f"📅 {dt.strftime('%d.%m.%Y %H:%M')}\n"
            f"🏷 {tpl.get('category_name', '—')}"
            + remaining_text,
            parse_mode=ParseMode.HTML,
        )
    else:
        await msg.reply_text(
            "⚠️ <b>Не удалось сохранить в Notion.</b>",
            parse_mode=ParseMode.HTML,
        )

    return await _show_tpl_list(msg, ctx)


# ── TPL_DELETE_CONFIRM ─────────────────────────────────────────────────────────

async def tpl_delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Отмена":
        return await _show_tpl_detail(update.message, ctx)

    if t == "⚠️ Да, удалить":
        tpl_id = ctx.user_data.get("tpl_id", "")
        tpl    = _tpl(ctx).get(tpl_id)
        name   = tpl["name"] if tpl else "шаблон"
        _tpl(ctx).delete(tpl_id)
        await update.message.reply_text(f"🗑 Шаблон <b>«{name}»</b> удалён.",
                                         parse_mode=ParseMode.HTML)
        return await _show_tpl_list(update.message, ctx)

    await update.message.reply_text("Выбери действие:", reply_markup=DELETE_CONFIRM_KB)
    return TPL_DELETE_CONFIRM


# ── TPL_EDIT_MENU ──────────────────────────────────────────────────────────────

async def tpl_edit_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        return await _show_tpl_detail(update.message, ctx)

    if t == "📝 Название":
        tpl  = _tpl(ctx).get(ctx.user_data.get("tpl_id", ""))
        name = tpl["name"] if tpl else ""
        await update.message.reply_text(
            f"Текущее название: <b>{name}</b>\n\nВведи новое название:",
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_KB,
        )
        return TPL_EDIT_NAME

    if t == "💰 Сумма":
        tpl = _tpl(ctx).get(ctx.user_data.get("tpl_id", ""))
        cur = f"  (текущая: {_disp_with_type(tpl['amount'])})" if tpl else ""
        await update.message.reply_text(
            f"Введи новую сумму{cur}\n\nВведи положительное число:",
            reply_markup=BACK_KB,
        )
        return TPL_EDIT_AMOUNT

    if t == "🏷 Категория":
        cats, _ = await _load_cats(ctx)
        if not cats:
            await update.message.reply_text(
                "Категории не найдены в Notion.",
                reply_markup=_kb(["⬜ Без категории"], ["◀️ Назад"]),
            )
        else:
            await update.message.reply_text(
                "Выбери новую категорию:", reply_markup=_categories_kb(cats)
            )
        return TPL_EDIT_CATEGORY

    if t == "💬 Заметка":
        tpl   = _tpl(ctx).get(ctx.user_data.get("tpl_id", ""))
        notes = tpl.get("notes", "") if tpl else ""
        cur   = f"Текущая заметка: <i>{notes}</i>\n\n" if notes else "Заметки нет.\n\n"
        await update.message.reply_text(
            cur + "Введи новую заметку или очисти:",
            parse_mode=ParseMode.HTML,
            reply_markup=TPL_EDIT_NOTES_KB,
        )
        return TPL_EDIT_NOTES

    await update.message.reply_text("Выбери что изменить:", reply_markup=TPL_EDIT_KB)
    return TPL_EDIT_MENU


# ── TPL_EDIT_NAME ──────────────────────────────────────────────────────────────

async def tpl_edit_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        return await _show_tpl_edit_menu(update.message, ctx)

    tpl_id = ctx.user_data.get("tpl_id", "")
    _tpl(ctx).update(tpl_id, name=t)
    await update.message.reply_text(f"✅ Название изменено на <b>«{t}»</b>.",
                                     parse_mode=ParseMode.HTML)
    return await _show_tpl_detail(update.message, ctx)


# ── TPL_EDIT_AMOUNT ────────────────────────────────────────────────────────────

async def tpl_edit_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        return await _show_tpl_edit_menu(update.message, ctx)

    raw = t.replace(",", ".")
    try:
        amt = abs(float(raw))
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Введи число:", reply_markup=BACK_KB
        )
        return TPL_EDIT_AMOUNT

    ctx.user_data["edit_amount_abs"] = amt
    await update.message.reply_text(
        f"💰 <b>{amt:.2f} ₴</b> — расход или доход?",
        parse_mode=ParseMode.HTML,
        reply_markup=TPL_EDIT_SIGN_KB,
    )
    return TPL_EDIT_SIGN


# ── TPL_EDIT_SIGN ──────────────────────────────────────────────────────────────

async def tpl_edit_sign(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t   = update.message.text.strip()
    amt = ctx.user_data.get("edit_amount_abs", 0.0)

    if t == "◀️ Назад":
        tpl = _tpl(ctx).get(ctx.user_data.get("tpl_id", ""))
        cur = f"  (текущая: {_disp_with_type(tpl['amount'])})" if tpl else ""
        await update.message.reply_text(
            f"Введи новую сумму{cur}:", reply_markup=BACK_KB
        )
        return TPL_EDIT_AMOUNT

    if t == "➖ Расход":
        new_amt = -amt
    elif t == "➕ Доход":
        new_amt = amt
    else:
        await update.message.reply_text("Выбери кнопку:", reply_markup=TPL_EDIT_SIGN_KB)
        return TPL_EDIT_SIGN

    _tpl(ctx).update(ctx.user_data.get("tpl_id", ""), amount=round(new_amt, 2))
    await update.message.reply_text(f"✅ Сумма изменена: {_disp_with_type(new_amt)}")
    return await _show_tpl_detail(update.message, ctx)


# ── TPL_EDIT_CATEGORY ──────────────────────────────────────────────────────────

async def tpl_edit_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        return await _show_tpl_edit_menu(update.message, ctx)

    tpl_id = ctx.user_data.get("tpl_id", "")
    if t == "⬜ Без категории":
        _tpl(ctx).update(tpl_id, category_id=None, category_name="—")
        await update.message.reply_text("✅ Категория очищена.")
        return await _show_tpl_detail(update.message, ctx)

    _, cat_map = await _load_cats(ctx)
    cid        = cat_map.get(t)
    if not cid:
        cats, _ = await _load_cats(ctx)
        await update.message.reply_text(
            "Выбери из списка:", reply_markup=_categories_kb(cats) if cats
            else _kb(["⬜ Без категории"], ["◀️ Назад"])
        )
        return TPL_EDIT_CATEGORY

    _tpl(ctx).update(tpl_id, category_id=cid, category_name=t)
    await update.message.reply_text(f"✅ Категория изменена: <b>{t}</b>.",
                                     parse_mode=ParseMode.HTML)
    return await _show_tpl_detail(update.message, ctx)


# ── TPL_EDIT_NOTES ─────────────────────────────────────────────────────────────

async def tpl_edit_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        return await _show_tpl_edit_menu(update.message, ctx)

    tpl_id = ctx.user_data.get("tpl_id", "")
    if t == "🗑 Очистить заметку":
        _tpl(ctx).update(tpl_id, notes="")
        await update.message.reply_text("✅ Заметка очищена.")
    else:
        _tpl(ctx).update(tpl_id, notes=t)
        await update.message.reply_text(f"✅ Заметка сохранена: <i>{t}</i>",
                                         parse_mode=ParseMode.HTML)

    return await _show_tpl_detail(update.message, ctx)


# ── Cancel ─────────────────────────────────────────────────────────────────────

async def tpl_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("❌ Отменено.")
    await _main_menu(update.message, _cfg(ctx))
    return ConversationHandler.END


def make_templates_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^📋 Шаблоны$"), templates_entry),
        ],
        states={
            TPL_MENU:          [MessageHandler(_TXT, tpl_menu)],
            TPL_DETAIL:        [MessageHandler(_TXT, tpl_detail)],
            TPL_USE_TIME:      [MessageHandler(_TXT, tpl_use_time)],
            TPL_CUSTOM_TIME:   [MessageHandler(_TXT, tpl_custom_time)],
            TPL_DELETE_CONFIRM:[MessageHandler(_TXT, tpl_delete_confirm)],
            TPL_EDIT_MENU:     [MessageHandler(_TXT, tpl_edit_menu)],
            TPL_EDIT_NAME:     [MessageHandler(_TXT, tpl_edit_name)],
            TPL_EDIT_AMOUNT:   [MessageHandler(_TXT, tpl_edit_amount)],
            TPL_EDIT_SIGN:     [MessageHandler(_TXT, tpl_edit_sign)],
            TPL_EDIT_CATEGORY: [MessageHandler(_TXT, tpl_edit_category)],
            TPL_EDIT_NOTES:    [MessageHandler(_TXT, tpl_edit_notes)],
        },
        fallbacks=[CommandHandler("cancel", tpl_cancel)],
        per_user=True, per_chat=True, per_message=False,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Webhook queue processor
# ═════════════════════════════════════════════════════════════════════════════

def _build_category_inline_kb(
    txn_id: str,
    cats: list[dict],
    suggested: Optional[dict] = None,
) -> InlineKeyboardMarkup:
    """Build inline keyboard with category buttons + optional ⭐ suggestion + No Category + Cancel."""
    buttons: list[list[InlineKeyboardButton]] = []

    # Smart suggestion row at the top
    if suggested:
        sugg_id_short = suggested["category_id"].replace("-", "")
        buttons.append([InlineKeyboardButton(
            f"⭐ {suggested['category_name']}",
            callback_data=f"cat:{txn_id}:{sugg_id_short}",
        )])

    row: list[InlineKeyboardButton] = []
    for cat in cats:
        # Strip hyphens from Notion page IDs so callback_data stays ≤ 64 bytes
        cat_id_short = cat["id"].replace("-", "")
        row.append(InlineKeyboardButton(
            cat["name"],
            callback_data=f"cat:{txn_id}:{cat_id_short}",
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton("⬜ Без категории", callback_data=f"cat:{txn_id}:NONE"),
    ])
    buttons.append([
        InlineKeyboardButton("❌ Не сохранять", callback_data=f"skip_txn:{txn_id}"),
    ])
    return InlineKeyboardMarkup(buttons)


async def _fetch_cats(ctx: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Return categories for the Pro-mode inline keyboard.

    Loads from Notion and caches in bot_data for 5 minutes.
    On failure keeps the previous cached value (never poisons cache with empty list).
    """
    import time as _time
    now = _time.monotonic()
    age = now - ctx.bot_data.get("cats_cache_ts", 0)

    if age > 300 or "cats_cache" not in ctx.bot_data:
        notion = _notion(ctx)
        if not notion:
            logger.error("_fetch_cats: Notion не настроен (проверь NOTION_API_KEY / DB IDs в /config)")
        else:
            fetched = await asyncio.to_thread(notion.get_categories)
            if fetched:
                ctx.bot_data["cats_cache"]    = fetched
                ctx.bot_data["cats_cache_ts"] = now
                ctx.bot_data["cats_id_map"]   = {
                    c["id"].replace("-", ""): c["id"] for c in fetched
                }
                logger.info("_fetch_cats: загружено %d категорий из Notion", len(fetched))
            else:
                logger.warning("_fetch_cats: Notion вернул пустой список категорий")

    return ctx.bot_data.get("cats_cache", [])


async def process_webhook_queue(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg     = _cfg(ctx)
    chat_id = cfg.get("TELEGRAM_CHAT_ID")

    if not chat_id:
        # Drain silently — no chat configured yet
        while not webhook_queue.empty():
            try:
                webhook_queue.get_nowait()
            except Exception:
                break
        return

    mode = cfg.get_mode()

    while not webhook_queue.empty():
        try:
            item = webhook_queue.get_nowait()
        except Exception:
            break

        if mode == "silent":
            # ── Silent mode: save straight to Notion, no Telegram notification ──
            notion = _notion(ctx)
            if notion:
                desc   = item.get("description", "Транзакция")
                amount = item.get("amount", 0) / 100
                dt     = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)
                saved  = await asyncio.to_thread(
                    notion.create_transaction, desc, -amount, dt, None
                )
                if saved:
                    logger.info("Silent: saved '%s' %.2f UAH to Notion", desc, amount)
                else:
                    logger.warning("Silent: failed to save '%s' to Notion", desc)
            else:
                logger.warning("Silent: Notion not configured — transaction dropped")

        else:
            # ── Pro mode: send message + inline category buttons ──────────────
            # Notion save happens ONLY when user selects a category (handle_category_callback).
            cats = await _fetch_cats(ctx)
            logger.info("Pro: building keyboard with %d categories", len(cats))

            # Smart category suggestion
            suggested: Optional[dict] = None
            if _cfg(ctx).get_smart_cats_enabled():
                desc_key = item.get("description", "")
                mem = _smart_cats.get(desc_key)
                if mem:
                    # Verify the category still exists in current list
                    mem_id_short = mem["category_id"].replace("-", "")
                    if any(c["id"].replace("-", "") == mem_id_short for c in cats):
                        suggested = mem

            text     = format_transaction_message(item)
            txn_id   = _pending_store.add(item, chat_id, text)
            keyboard = _build_category_inline_kb(txn_id, cats, suggested=suggested)

            bot: Bot = ctx.bot
            try:
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
                _pending_store.set_message_id(txn_id, sent.message_id)
                logger.info(
                    "Pro: sent '%s' %.2f UAH — waiting for category selection",
                    item.get("description", "?"),
                    abs(item.get("amount", 0) / 100),
                )
            except Exception as exc:
                logger.error("Pro: failed to send webhook message: %s", exc)
                _pending_store.remove(txn_id)


# ═════════════════════════════════════════════════════════════════════════════
# Category callback handler (inline buttons on transaction messages)
# ═════════════════════════════════════════════════════════════════════════════

async def handle_category_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """User picked a category → transition to notes step."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, txn_id, cat_choice = parts

    pending = _pending_store.get(txn_id)
    if not pending:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # Resolve category
    category_id: Optional[str] = None
    cat_display = "без категории"

    if cat_choice != "NONE":
        raw = cat_choice
        if len(raw) == 32:
            cat_choice_uuid = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
        else:
            cat_choice_uuid = raw
        category_id = cat_choice_uuid
        cats: list[dict] = ctx.bot_data.get("cats_cache", [])
        match = next((c for c in cats if c["id"].replace("-", "") == raw), None)
        cat_display = match["name"] if match else "категория"

    # Store chosen category
    _pending_store.update_for_notes(txn_id, category_id, cat_display)
    updated_pending = _pending_store.get(txn_id) or pending

    # If notes step is disabled → save immediately, no extra prompt
    if not _cfg(ctx).get_notes_enabled():
        await _save_card_txn(query=query, ctx=ctx, txn_id=txn_id,
                             pending=updated_pending, notes="")
        return

    # Notes step enabled → ask for a note
    original_text = pending.get("text", "")
    cat_label = f"🏷 {cat_display}" if cat_display != "без категории" else "⬜ Без категории"

    notes_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Сохранить без заметки", callback_data=f"notes_skip:{txn_id}")],
        [InlineKeyboardButton("❌ Не сохранять", callback_data=f"skip_txn:{txn_id}")],
    ])
    try:
        await query.edit_message_text(
            original_text + f"\n\n{cat_label}\n\n💬 <i>Добавь заметку или пропусти:</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=notes_kb,
        )
    except Exception as exc:
        logger.error("Failed to edit message for notes prompt: %s", exc)


async def handle_skip_txn_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """User explicitly chose NOT to save the transaction."""
    query = update.callback_query
    await query.answer()

    txn_id  = query.data.split(":", 1)[1]
    pending = _pending_store.get(txn_id)
    _pending_store.remove(txn_id)

    original_text = (pending or {}).get("text", "")
    try:
        await query.edit_message_text(
            original_text + "\n\n❌ <b>Не сохранено</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception as exc:
        logger.error("Failed to edit message after skip: %s", exc)


async def handle_notes_skip_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """User chose to save without a note."""
    query = update.callback_query
    await query.answer()

    txn_id  = query.data.split(":", 1)[1]
    pending = _pending_store.get(txn_id)
    if not pending:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    await _save_card_txn(query=query, ctx=ctx, txn_id=txn_id, pending=pending, notes="")


async def handle_card_notes_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Group -1 handler: intercepts text messages when a card transaction is
    awaiting a user note.  Does nothing (lets message fall to group 0) if no
    such pending transaction exists for this chat.
    """
    from telegram.ext import ApplicationHandlerStop

    chat_id = str(update.effective_chat.id)
    result  = _pending_store.get_awaiting_notes_by_chat(chat_id)
    if not result:
        return  # nothing pending — let ConversationHandler see this message

    txn_id, pending = result
    notes = update.message.text.strip()

    # Try to delete the user's note message to keep the chat tidy
    try:
        await update.message.delete()
    except Exception:
        pass

    await _save_card_txn(query=None, ctx=ctx, txn_id=txn_id, pending=pending, notes=notes,
                         bot=ctx.bot, chat_id=chat_id)
    raise ApplicationHandlerStop  # prevent ConversationHandler from seeing this


async def _check_limit_notification(
    ctx:         ContextTypes.DEFAULT_TYPE,
    cat_rem:     float,
    cat_limit:   float,
    cat_display: str,
    chat_id:     str,
) -> None:
    """Send a separate limit-warning message if the category is at or over its daily quota."""
    import calendar as _cal
    if cat_limit <= 0:
        return

    now           = datetime.now(tz=timezone.utc)
    days_in_month = _cal.monthrange(now.year, now.month)[1]
    days_left     = days_in_month - now.day + 1
    proportional  = cat_limit * (days_left / days_in_month)

    if cat_rem <= 0:
        level = "exceeded"
    elif cat_rem <= proportional:
        level = "low"
    else:
        return  # all good

    # Dedup: one notification per (category, date, level) per bot session
    dedup_key = f"{cat_display}:{now.date().isoformat()}:{level}"
    notified  = ctx.bot_data.setdefault("limit_notified", {})
    if dedup_key in notified:
        return
    notified[dedup_key] = True

    if level == "exceeded":
        text = (
            f"🚨 <b>Лимит превышен!</b>\n\n"
            f"🏷 <b>{cat_display}</b>\n"
            f"Лимит на месяц: {cat_limit:,.2f} ₴\n"
            f"Перерасход: {abs(cat_rem):,.2f} ₴"
        )
    else:
        text = (
            f"⚠️ <b>Лимит на сегодня исчерпан</b>\n\n"
            f"🏷 <b>{cat_display}</b>\n"
            f"Пропорция на {now.day}-е число: {proportional:,.2f} ₴\n"
            f"Остаток: {cat_rem:,.2f} ₴"
        )

    try:
        await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.error("Failed to send limit notification: %s", exc)


async def _save_card_txn(
    query,                          # CallbackQuery | None
    ctx:    ContextTypes.DEFAULT_TYPE,
    txn_id: str,
    pending: dict,
    notes:  str,
    bot=None,                       # required when query is None
    chat_id: str = "",
) -> None:
    """Save a card transaction to Notion and edit the original message with the result."""
    item             = pending["item"]
    category_id      = pending.get("category_id")
    cat_display      = pending.get("cat_display", "без категории")
    original_text    = pending.get("text", "")
    message_id       = pending.get("message_id")
    pending_chat_id  = pending.get("chat_id", chat_id)

    desc   = item.get("description", "Транзакция")
    amount = item.get("amount", 0) / 100
    dt     = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)

    cat_label  = f"🏷 {cat_display}" if cat_display != "без категории" else "⬜ Без категории"
    note_label = f"\n💬 {notes}" if notes else ""

    # Capture the target message reference once, so both edits use the same coords.
    if query and query.message:
        _edit_chat = str(query.message.chat_id)
        _edit_mid  = query.message.message_id
    elif message_id and pending_chat_id:
        _edit_chat = pending_chat_id
        _edit_mid  = message_id
    else:
        _edit_chat = ""
        _edit_mid  = None

    notion = _notion(ctx)
    saved  = False

    if notion:
        saved = await asyncio.to_thread(
            notion.create_transaction, desc, -amount, dt, category_id, notes
        )

    # Remember category for future smart suggestions
    if saved and category_id and cat_display != "без категории":
        _smart_cats.set(desc, category_id, cat_display)

    _pending_store.remove(txn_id)

    status_line = "✅ <b>Сохранено в Notion</b>" if saved else "⚠️ <b>Ошибка сохранения в Notion</b>"

    cat_rem   = None
    total_rem = None
    cat_limit = None

    if saved and category_id and notion:
        try:
            await asyncio.sleep(0.7)
            limit_prop = _cfg(ctx).get("NOTION_LIMIT_PROP", "").strip()
            gather_coros = [
                asyncio.to_thread(notion.get_category_remaining, category_id),
                asyncio.to_thread(notion.get_total_remaining),
            ]
            if limit_prop:
                gather_coros.append(asyncio.to_thread(notion.get_category_limit, category_id))

            results   = await asyncio.wait_for(asyncio.gather(*gather_coros), timeout=8)
            cat_rem   = results[0]
            total_rem = results[1]
            if limit_prop and len(results) > 2:
                cat_limit = results[2]
        except Exception as exc:
            logger.warning("Could not fetch category remaining/limit: %s", exc)

    if cat_rem is not None:
        status_line += f"\n💼 На месяц осталось по категории: {cat_rem:,.2f} ₴"
    if total_rem is not None:
        status_line += f"\n💰 Всего в бюджете осталось: {total_rem:,.2f} ₴"

    # ── Final result edit (fallback to send_message if edit is rejected) ─────────
    result_text = original_text + f"\n\n{cat_label}{note_label}\n\n{status_line}"
    if _edit_mid and _edit_chat:
        try:
            await ctx.bot.edit_message_text(
                chat_id=_edit_chat, message_id=_edit_mid,
                text=result_text, parse_mode=ParseMode.HTML, reply_markup=None,
            )
        except Exception as exc:
            logger.warning("Result edit failed (%s), sending new message", exc)
            try:
                await ctx.bot.send_message(
                    chat_id=_edit_chat,
                    text=result_text,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc2:
                logger.error("Failed to send result message: %s", exc2)

    # Limit notification (sent as a separate follow-up message)
    if (saved and category_id and cat_display != "без категории"
            and cat_rem is not None and cat_limit is not None):
        await _check_limit_notification(
            ctx=ctx,
            cat_rem=cat_rem,
            cat_limit=cat_limit,
            cat_display=cat_display,
            chat_id=pending_chat_id,
        )


# ═════════════════════════════════════════════════════════════════════════════
# /stats — monthly analytics
# ═════════════════════════════════════════════════════════════════════════════

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current-month expense/income breakdown with last-month comparison."""
    if not _auth(update, ctx):
        return

    notion = _notion(ctx)
    if not notion:
        await update.message.reply_text(
            "⚠️ Notion не настроен. Зайди в ⚙️ Настройки → ⚙️ Конфигурация."
        )
        return

    await update.message.reply_text("📊 Загружаю статистику…", reply_markup=MAIN_KB)

    import calendar as _cal
    now        = datetime.now(tz=timezone.utc)
    this_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    days_in_month = _cal.monthrange(now.year, now.month)[1]
    day_progress  = now.day / days_in_month

    try:
        cats, this_txns, cat_budgets, total_rem = await asyncio.gather(
            asyncio.to_thread(notion.get_categories),
            asyncio.to_thread(notion.get_transactions_by_period, this_start, now),
            asyncio.to_thread(notion.get_all_category_budgets),
            asyncio.to_thread(notion.get_total_remaining),
        )
    except Exception as exc:
        logger.error("Stats fetch failed: %s", exc)
        await update.message.reply_text("❌ Не удалось загрузить данные из Notion.")
        return

    cat_name_map: dict[str, str] = {c["id"].replace("-", ""): c["name"] for c in cats}

    # Notion convention: positive amount = expense, negative = income
    # (bot stores: create_transaction(desc, -internal_amt) where internal_amt<0 for expense)
    by_cat:    dict[str, float] = {}  # cname → spent
    by_cat_id: dict[str, str]   = {}  # cname → cat_id (for budget lookup)
    total_exp = 0.0
    total_inc = 0.0
    for txn in this_txns:
        amt    = txn.get("amount") or 0.0
        cat_id = (txn.get("category_id") or "").replace("-", "")
        cname  = cat_name_map.get(cat_id) or "Без категории"
        if amt > 0:                        # positive = expense
            by_cat[cname]    = by_cat.get(cname, 0.0) + amt
            by_cat_id[cname] = cat_id
            total_exp       += amt
        elif amt < 0:                      # negative = income
            total_inc       += (-amt)

    def _bar(ratio: float, width: int = 10) -> str:
        ratio  = max(0.0, min(1.0, ratio))
        filled = round(ratio * width)
        return "█" * filled + "░" * (width - filled)

    this_month = _MONTHS_RU[now.month - 1]

    # ── Overall budget block ───────────────────────────────────────────────────
    budget_block = ""
    if total_rem is not None:
        total_budget = total_exp + total_rem
        if total_budget > 0:
            budget_pct   = total_exp / total_budget * 100
            budget_ratio = total_exp / total_budget
            budget_bar   = _bar(budget_ratio)
            budget_block = (
                f"\n💼 <b>Общий бюджет</b>\n"
                f"<code>{budget_bar}</code>  {budget_pct:.0f}%"
                f"  ·  {total_exp:,.0f} / {total_budget:,.0f} ₴"
                f"  (остаток: {total_rem:,.0f} ₴)"
            )

    # ── Per-category lines ─────────────────────────────────────────────────────
    cat_lines: list[str] = []
    for cname in sorted(by_cat, key=lambda c: by_cat[c], reverse=True):
        spent  = by_cat[cname]
        cid    = by_cat_id.get(cname, "")
        limit  = cat_budgets.get(cid) if cid else None

        if limit and limit > 0:
            pct   = spent / limit * 100
            bar   = _bar(spent / limit)
            line  = (
                f"<b>{cname}</b>\n"
                f"<code>{bar}</code>  {pct:.0f}%"
                f"  ·  {spent:,.0f} / {limit:,.0f} ₴"
            )
        else:
            # No limit — show share of total expenses
            pct  = spent / total_exp * 100 if total_exp > 0 else 0.0
            bar  = _bar(spent / total_exp if total_exp > 0 else 0.0)
            line = (
                f"<b>{cname}</b>\n"
                f"<code>{bar}</code>  {pct:.0f}% от расходов"
                f"  ·  {spent:,.0f} ₴"
            )
        cat_lines.append(line)

    # ── Assemble message ───────────────────────────────────────────────────────
    day_bar  = _bar(day_progress)
    day_info = f"<code>{day_bar}</code>  {now.day}/{days_in_month} дней"

    parts = [
        f"📊 <b>Статистика — {this_month} {now.year}</b>",
        "",
        f"📅 {day_info}",
        f"💸 <b>Расходы:</b> {total_exp:,.2f} ₴",
        f"💰 <b>Доходы:</b> {total_inc:,.2f} ₴",
    ]
    if budget_block:
        parts.append(budget_block)
    if cat_lines:
        parts += ["", "📋 <b>По категориям:</b>", ""] + cat_lines
    else:
        parts += ["", "<i>Транзакций за этот месяц не найдено</i>"]

    await update.message.reply_text(
        "\n".join(parts),
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KB,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Feedback conversation (all users can submit, no _auth required)
# ═════════════════════════════════════════════════════════════════════════════

FEEDBACK_TYPE = 100
FEEDBACK_TEXT = 101

# Static developer ngrok URL — used when DEVELOPER_FEEDBACK_URL is not set in .env
_DEVELOPER_FEEDBACK_URL = "https://blousily-uncatechized-tanja.ngrok-free.dev"

_FEEDBACK_TYPE_KB = _kb(["🐛 Баг", "✨ Фича"], ["◀️ Назад"])
_FEEDBACK_BACK_KB = _kb(["◀️ Назад"])


async def feedback_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /feedback command or 📝 Фидбек button."""
    await update.message.reply_html(
        "📝 <b>Обратная связь</b>\n\n"
        "Выбери тип заявки:",
        reply_markup=_FEEDBACK_TYPE_KB,
    )
    return FEEDBACK_TYPE


async def feedback_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        cfg = _cfg(ctx)
        await _main_menu(update.message, cfg)
        return ConversationHandler.END

    if t not in ("🐛 Баг", "✨ Фича"):
        await update.message.reply_text("Выбери тип:", reply_markup=_FEEDBACK_TYPE_KB)
        return FEEDBACK_TYPE

    ctx.user_data["feedback_type"] = "bug" if t == "🐛 Баг" else "feature"
    label = "🐛 баг" if t == "🐛 Баг" else "✨ запрос фичи"

    await update.message.reply_html(
        f"Тип: <b>{label}</b>\n\n"
        "Опиши проблему или идею подробнее.\n"
        "<i>Твоё сообщение останется в чате как есть.</i>",
        reply_markup=_FEEDBACK_BACK_KB,
    )
    return FEEDBACK_TEXT


async def feedback_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        await update.message.reply_html(
            "📝 <b>Обратная связь</b>\n\nВыбери тип заявки:",
            reply_markup=_FEEDBACK_TYPE_KB,
        )
        return FEEDBACK_TYPE

    # Build payload — no sensitive info (no chat_id), only username + user_id
    user     = update.message.from_user
    username = user.username or user.first_name or str(user.id)
    payload  = {
        "type":          ctx.user_data.get("feedback_type", "bug"),
        "text":          t,
        "from_user_id":  user.id,
        "from_username": username,
        "timestamp":     datetime.now(tz=timezone.utc).isoformat(),
        "version":       BOT_VERSION,
    }

    # POST to developer's ngrok (configured URL takes priority, static fallback always available)
    cfg = _cfg(ctx)
    dev_url = cfg.get("DEVELOPER_FEEDBACK_URL", "").strip() or _DEVELOPER_FEEDBACK_URL
    sent = await asyncio.to_thread(_http_post_feedback, dev_url, payload)
    if not sent:
        logger.warning("Feedback POST failed to %s", dev_url)

    type_label = "🐛 Баг" if payload["type"] == "bug" else "✨ Запрос фичи"
    await update.message.reply_html(
        f"✅ <b>Заявка принята!</b>\n\n"
        f"{type_label}\n\n"
        "Спасибо за фидбек — разработчик получит уведомление.",
        reply_markup=MAIN_KB,
    )
    return ConversationHandler.END


async def feedback_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = _cfg(ctx)
    await _main_menu(update.message, cfg)
    return ConversationHandler.END


def _http_post_feedback(url: str, payload: dict) -> bool:
    """Synchronous HTTP POST (runs in thread via asyncio.to_thread)."""
    import json as _json
    import urllib.request as _req
    data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = _req.Request(
        url.rstrip("/") + "/feedback",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _req.urlopen(request, timeout=6) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.debug("_http_post_feedback error: %s", exc)
        return False


def make_feedback_handler() -> ConversationHandler:
    """ConversationHandler for the feedback flow. No auth — open to all users."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("feedback", feedback_start),
            MessageHandler(filters.Regex(r"^📝 Фидбек$"), feedback_start),
        ],
        states={
            FEEDBACK_TYPE: [MessageHandler(_TXT, feedback_type)],
            FEEDBACK_TEXT: [MessageHandler(_TXT, feedback_text)],
        },
        fallbacks=[CommandHandler("cancel", feedback_cancel)],
        per_user=True, per_chat=True, per_message=False,
    )


# ── Feedback notification queue (incoming feedback → Telegram alert to admin) ──

async def process_feedback_queue(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB job: drain feedback_notification_queue → send Telegram alert to admin."""
    cfg     = _cfg(ctx)
    chat_id = cfg.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        return

    while not feedback_notification_queue.empty():
        try:
            entry = feedback_notification_queue.get_nowait()
        except Exception:
            break

        icon     = "🐛" if entry.get("type") == "bug" else "✨"
        username = entry.get("from_username", "?")
        fid      = entry.get("id", "?")
        text_    = entry.get("text", "")
        ver      = entry.get("version", "?")

        msg = (
            f"{icon} <b>Новый фидбек!</b>\n\n"
            f"От: @{username}  (v{ver})\n"
            f"ID: <code>{fid}</code>\n\n"
            f"{text_}"
        )
        try:
            await ctx.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error("Feedback notification failed: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# Template trigger queue (from /trigger HTTP endpoint)
# ═════════════════════════════════════════════════════════════════════════════

async def process_trigger_queue(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB job: drain trigger_queue, save each template transaction to Notion + notify."""
    cfg     = _cfg(ctx)
    chat_id = cfg.get("TELEGRAM_CHAT_ID")

    while not trigger_queue.empty():
        try:
            tpl = trigger_queue.get_nowait()
        except Exception:
            break

        desc       = tpl["name"]
        amount     = tpl["amount"]   # negative = expense (internal convention)
        category_id = tpl.get("category_id")
        cat_name   = tpl.get("category_name", "—")
        notes      = tpl.get("notes", "")
        dt         = datetime.now(tz=timezone.utc)

        notion = _notion(ctx)
        saved  = False
        if notion:
            saved = await asyncio.to_thread(
                notion.create_transaction,
                desc, -amount, dt, category_id, notes,  # -amount: matches template-use convention
            )
            if saved:
                _smart_cats.set(desc, category_id, cat_name) if category_id else None
        else:
            logger.warning("Trigger: Notion not configured — '%s' dropped", desc)

        if chat_id:
            sign     = "➖" if amount < 0 else "➕"
            amt_disp = f"{abs(amount):,.2f} ₴"
            if saved:
                text = (
                    f"⚡ <b>Шаблон применён!</b>\n\n"
                    f"{sign} <b>{desc}</b>\n"
                    f"💰 {amt_disp}\n"
                    f"🏷 {cat_name}"
                    + (f"\n💬 {notes}" if notes else "")
                    + "\n\n✅ Сохранено в Notion"
                )
            else:
                text = (
                    f"⚡ <b>Шаблон: {desc}</b>\n\n"
                    "⚠️ Не удалось сохранить в Notion. Проверь настройки."
                )
            try:
                await ctx.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=ParseMode.HTML
                )
            except Exception as exc:
                logger.error("Trigger: failed to send confirmation: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# Standalone /cancel
# ═════════════════════════════════════════════════════════════════════════════

async def cancel_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Нечего отменять.")

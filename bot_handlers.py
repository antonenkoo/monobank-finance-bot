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
    format_transaction_message,
    get_accounts,
    restart_webhook_server,
    webhook_queue,
)
from notion_service import NotionService
from pending_store import PendingTransactionStore

logger = logging.getLogger(__name__)

# ── Pending transaction store (persists to JSON across restarts) ───────────────
_pending_store = PendingTransactionStore()

# ── Startup messages ───────────────────────────────────────────────────────────
_STARTUP_MESSAGES = [
    "🚀 <b>Monobank Finance Bot запущен!</b>\nСлушаю webhook и жду твои транзакции 👀",
    "💰 <b>Бот снова в строю!</b>\nГотов ловить расходы и нести их в Notion 📊",
    "🤖 <b>Я здесь!</b>\nWebhook Monobank подключён. Деньгам не скрыться! 💸",
    "📡 <b>Сервер запущен.</b>\nMono поглядываю — Notion жду. Поехали! 🏎",
    "🔔 <b>Финансовый страж на посту!</b>\nКаждая гривна будет записана в Notion ✅",
]


async def send_startup_message(bot: Bot, chat_id: str) -> None:
    """Send a random friendly startup message to the owner's chat."""
    try:
        text = random.choice(_STARTUP_MESSAGES) + f"\n\n<i>v{BOT_VERSION}</i>"
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        logger.error("Failed to send startup message: %s", exc)



# ═════════════════════════════════════════════════════════════════════════════
# Static ReplyKeyboards
# ═════════════════════════════════════════════════════════════════════════════

def _kb(*rows: list[str], one_time: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(list(rows), resize_keyboard=True, one_time_keyboard=one_time)


MAIN_KB = _kb(["➕ Добавить", "📋 Шаблоны"], ["⚙️ Настройки"])

def _settings_kb(mode: str) -> ReplyKeyboardMarkup:
    """Settings menu keyboard with mode-toggle button showing the opposite mode."""
    toggle = "🔔 Про режим" if mode == "silent" else "🔇 Тихий режим"
    return _kb(["⚙️ Конфигурация"], [toggle], ["◀️ Назад"])

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


def _add_category_inline_kb(cats: list[dict]) -> InlineKeyboardMarkup:
    """Inline keyboard for category selection in the manual add / template flow."""
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, cat in enumerate(cats):
        cat_id_short = cat["id"].replace("-", "")
        row.append(InlineKeyboardButton(cat["name"], callback_data=f"add_cat:{cat_id_short}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬜ Без категории", callback_data="add_cat:NONE")])
    return InlineKeyboardMarkup(buttons)


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
_MAIN_BTNS = {"➕ Добавить", "📋 Шаблоны", "⚙️ Настройки"}
_NOT_MAIN  = ~filters.Regex(r"^(➕ Добавить|📋 Шаблоны|⚙️ Настройки)$")
_TXT       = filters.TEXT & ~filters.COMMAND & _NOT_MAIN


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


async def settings_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    cfg = _cfg(ctx)
    await update.message.reply_text(
        "⚙️ <b>Настройки</b>", parse_mode=ParseMode.HTML, reply_markup=_settings_kb(cfg.get_mode())
    )
    return SETTINGS_MENU


async def settings_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t   = update.message.text.strip()
    cfg = _cfg(ctx)

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
            reply_markup=_settings_kb("pro"),
        )
        return SETTINGS_MENU

    if t == "🔇 Тихий режим":
        cfg.set_mode("silent")
        await update.message.reply_text(
            "🔇 <b>Тихий режим включён!</b>\n\n"
            "Транзакции Monobank сохраняются в Notion автоматически, без уведомлений.",
            parse_mode=ParseMode.HTML,
            reply_markup=_settings_kb("silent"),
        )
        return SETTINGS_MENU

    await update.message.reply_text("Выбери пункт:", reply_markup=_settings_kb(cfg.get_mode()))
    return SETTINGS_MENU


async def conf_menu_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t   = update.message.text.strip()
    cfg = _cfg(ctx)

    if t == "◀️ Назад к настройкам":
        await update.message.reply_text("⚙️ <b>Настройки</b>", parse_mode=ParseMode.HTML,
                                         reply_markup=_settings_kb(cfg.get_mode()))
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
    steps   = step
    if not cats:
        await msg.reply_text(
            f"Шаг {step}/{steps}: Категории не найдены в Notion.\n"
            "Сохраним без категории:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬜ Без категории", callback_data="add_cat:NONE"),
            ]]),
        )
    else:
        await msg.reply_text(
            f"Шаг {step}/{steps}: Выбери категорию:",
            reply_markup=_add_category_inline_kb(cats),
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
        await update.message.reply_text(
            "Введи дату и время в формате:\n"
            "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\nПример: <code>03.04.2025 14:30</code>",
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
            "❌ Неверный формат. Пример: <code>03.04.2025 14:30</code>",
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


async def add_category_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    mode = ctx.user_data.get("mode", "add")

    raw = query.data[len("add_cat:"):]  # everything after "add_cat:"

    if raw == "NONE":
        category_id   = None
        category_name = "—"
    else:
        # Restore dashes into UUID format (8-4-4-4-12)
        h = raw
        full_id = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
        cats, _ = await _load_cats(ctx)
        matched = next((c for c in cats if c["id"].replace("-", "") == raw), None)
        category_id   = full_id
        category_name = matched["name"] if matched else full_id

    ctx.user_data["add_cat_id"]   = category_id
    ctx.user_data["add_cat_name"] = category_name

    # Remove the inline keyboard so the step message stays clean
    await query.edit_message_reply_markup(reply_markup=None)

    if mode == "template":
        return await _finalize_template(query.message, ctx)
    else:
        return await _finalize_add(query.message, ctx)


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

    success = False
    remaining_text = ""

    if notion:
        success = await asyncio.to_thread(
            notion.create_transaction, desc, -amt, dt, cid, notes
        )

    if success and cid:
        try:
            await asyncio.sleep(0.7)
            remaining = await asyncio.wait_for(
                asyncio.to_thread(notion.get_category_remaining, cid),
                timeout=5,
            )
            if remaining is not None:
                remaining_text = f"\n💼 На месяц осталось по категории: {remaining:,.2f} ₴"
        except Exception as e:
            logger.exception("Failed to get category remaining: %s", e)
            remaining_text = "\n💼 Остаток по категории пока не удалось получить"
    if success:
        await msg.reply_text(
            f"✅ <b>Транзакция сохранена в Notion!</b>\n\n"
            f"<b>{desc}</b>\n"
            f"💰 {_disp_with_type(amt)}\n"
            f"📅 {dt.strftime('%d.%m.%Y %H:%M')}\n"
            f"🏷 {cname}"
            + (f"\n💬 {notes}" if notes else "")
            + remaining_text,
            parse_mode=ParseMode.HTML,
            reply_markup=SAVE_TPL_KB,
        )
        return ADD_SAVE_CONFIRM
    else:
        await msg.reply_text(
            f"⚠️ <b>Не удалось сохранить в Notion.</b>\n"
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
            ADD_CATEGORY:     [CallbackQueryHandler(add_category_callback, pattern=r"^add_cat:")],
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
        await update.message.reply_text(
            "Введи дату и время:\n<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>",
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
            "❌ Неверный формат. Пример: <code>03.04.2025 14:30</code>",
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
            remaining = await asyncio.to_thread(
                notion.get_category_remaining,
                tpl["category_id"],
            )
            if remaining is not None:
                remaining_text = f"\n💼 На месяц осталось по категории: {remaining:,.2f} ₴"

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

def _build_category_inline_kb(txn_id: str, cats: list[dict]) -> InlineKeyboardMarkup:
    """Build inline keyboard with category buttons + No Category + Later."""
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, cat in enumerate(cats):
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

            text     = format_transaction_message(item)
            txn_id   = _pending_store.add(item, chat_id, text)
            keyboard = _build_category_inline_kb(txn_id, cats)

            bot: Bot = ctx.bot
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
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
    """Handle inline button presses on transaction messages."""
    query = update.callback_query
    await query.answer()

    # callback_data format: "cat:{txn_id}:{cat_id_or_NONE}"
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, txn_id, cat_choice = parts

    pending = _pending_store.get(txn_id)
    if not pending:
        # Already processed (e.g. duplicate tap)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    item = pending["item"]
    original_text = pending.get("text", "")

    notion = _notion(ctx)
    saved = False
    if notion:
        desc   = item.get("description", "Транзакция")
        amount = item.get("amount", 0) / 100
        dt     = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)

        category_id: Optional[str] = None
        cat_display = "без категории"

        if cat_choice != "NONE":
            # Restore hyphens: Notion expects UUID format
            raw = cat_choice
            if len(raw) == 32:
                cat_choice_uuid = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
            else:
                cat_choice_uuid = raw
            category_id = cat_choice_uuid

            # Find display name from cache
            cats: list[dict] = ctx.bot_data.get("cats_cache", [])
            match = next((c for c in cats if c["id"].replace("-", "") == raw), None)
            cat_display = match["name"] if match else "категория"

        saved = await asyncio.to_thread(
            notion.create_transaction, desc, -amount, dt, category_id
        )

    _pending_store.remove(txn_id)

    status_line = "✅ <b>Сохранено в Notion</b>"
    if saved and cat_choice != "NONE":
        status_line += f" · {cat_display}"
    elif not saved:
        status_line = "⚠️ <b>Ошибка сохранения в Notion</b>"

    if saved and category_id and notion:
        try:
            await asyncio.sleep(0.7)
            remaining = await asyncio.wait_for(
                asyncio.to_thread(notion.get_category_remaining, category_id),
                timeout=5,
            )
            if remaining is not None:
                status_line += f"\n💼 На месяц осталось по категории: {remaining:,.2f} ₴"
        except Exception as exc:
            logger.warning("Could not fetch category remaining: %s", exc)

    try:
        await query.edit_message_text(
            original_text + f"\n\n{status_line}",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
    except Exception as exc:
        logger.error("Failed to edit message after category save: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# Standalone /cancel
# ═════════════════════════════════════════════════════════════════════════════

async def cancel_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Нечего отменять.")

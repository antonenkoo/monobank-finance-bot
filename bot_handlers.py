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
import os
import random
from datetime import datetime, timezone, timedelta
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
    format_transaction_message,
    get_accounts,
    restart_webhook_server,
    trigger_queue,
    webhook_queue,
)
from notion_service import NotionService
from limit_store import LimitNotificationStore
from pending_store import PendingTransactionStore
from smart_categories import SmartCategoryStore

logger = logging.getLogger(__name__)


# Текущее время с таймзоной (безопасно на Windows)
def _local_now() -> datetime:
    """Current local time with timezone offset (e.g. +02:00). Safe on Windows."""
    return datetime.now().astimezone()


# Парсинг строки даты в datetime с таймзоной
def _parse_local_dt(s: str) -> datetime:
    """Parse 'DD.MM.YYYY HH:MM' as local time with timezone offset."""
    return datetime.strptime(s, "%d.%m.%Y %H:%M").astimezone()

# ── Persistent stores ──────────────────────────────────────────────────────────
_pending_store = PendingTransactionStore()
_smart_cats    = SmartCategoryStore()
_limit_store   = LimitNotificationStore()

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



_RELEASE_SHOWN_FILE = Path("release_shown.txt")


# Получить последнюю показанную версию из файла
def _get_shown_version() -> str:
    try:
        return _RELEASE_SHOWN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


# Отметить версию как показанную
def _mark_release_shown(version: str) -> None:
    try:
        _RELEASE_SHOWN_FILE.write_text(version, encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write release_shown.txt: %s", exc)


# Отправить приветствие при старте или обновлении
async def send_startup_message(bot: Bot, chat_id: str) -> None:
    """Send a random friendly startup message; show release notes once per version."""
    shown_ver = _get_shown_version()

    if shown_ver != BOT_VERSION:
        try:
            notes = _CHANGELOG.get(f"v{BOT_VERSION}", f"🚀 <b>v{BOT_VERSION}</b>")
            await bot.send_message(
                chat_id=chat_id,
                text=notes,
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

# Создать ReplyKeyboardMarkup из строк кнопок
def _kb(*rows: list[str], one_time: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(list(rows), resize_keyboard=True, one_time_keyboard=one_time)


MAIN_KB = _kb(["➕ Добавить", "📋 Шаблоны"], ["📊 Статистика", "⚙️ Настройки"], ["📝 Фидбек"])

# Клавиатура главного меню настроек
def _settings_kb(mode: str, notes: bool = True, smart_cats: bool = True) -> ReplyKeyboardMarkup:
    """Settings menu keyboard with mode-toggle, notes-toggle and smart-cats-toggle buttons."""
    mode_toggle   = "🔔 Детальный режим"            if mode       == "silent" else "🔇 Тихий режим"
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

# Клавиатура полей конфигурации
def _config_fields_kb() -> ReplyKeyboardMarkup:
    rows = [[FIELD_LABELS[f]] for f in EDITABLE_FIELDS]
    rows.append(["📋 Выбрать аккаунт Monobank"])
    rows.append(["◀️ Назад к настройкам"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# Клавиатура выбора аккаунта Монобанка
def _accounts_kb(labels: list[str]) -> ReplyKeyboardMarkup:
    rows = [[lbl] for lbl in labels]
    rows.append(["◀️ Назад"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# Клавиатура выбора категории Notion
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



# Клавиатура списка шаблонов
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

# Получить ConfigManager из bot_data
def _cfg(ctx: ContextTypes.DEFAULT_TYPE) -> ConfigManager:
    return ctx.bot_data["config"]

# Получить TemplateManager из bot_data
def _tpl(ctx: ContextTypes.DEFAULT_TYPE) -> TemplateManager:
    return ctx.bot_data["templates"]

# Получить NotionService (None если не настроен)
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

# Проверка авторизации — только владелец бота
def _auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = _cfg(ctx).get("TELEGRAM_CHAT_ID")
    return not chat_id or str(update.effective_chat.id) == chat_id

# Попытаться перезапустить вебхук Монобанка
def _try_restart_webhook(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg  = _cfg(ctx)
    mono = cfg.get("MONOBANK_TOKEN")
    if not mono:
        return
    t = restart_webhook_server(
        port=cfg.get_webhook_port(),
        ngrok_token=None,  # ngrok owned by feedback-bot, not monobank-bot
        mono_token=mono,
        account_id=cfg.get("MONOBANK_ACCOUNT_ID"),
        ngrok_domain="",
    )
    ctx.bot_data["webhook_started"] = True
    ctx.bot_data["webhook_thread"]  = t


# ── Amount display ─────────────────────────────────────────────────────────────

# Форматировать сумму для отображения (перевернуть знак для юзера)
def _disp(amount: float) -> str:
    """expense (neg internally) → shown as positive; income (pos internally) → shown as negative."""
    return f"{-amount:,.2f} ₴"

# Сумма + тип (Расход/Доход)
def _disp_with_type(amount: float) -> str:
    t = "расход" if amount < 0 else "доход"
    return f"{_disp(amount)} ({t})"


# ── Main menu text ─────────────────────────────────────────────────────────────

# Текст главного меню с балансом
def _menu_text(cfg: ConfigManager) -> str:
    mode = cfg.get_mode()
    mode_label = "🔔 Детальный" if mode == "pro" else "🔇 Тихий"
    return (
        "🏠 <b>Главное меню</b>\n\n"
        "Используй кнопки внизу экрана.\n\n"
        f"Режим: {mode_label}"
    )

# Отправить главное меню
async def _main_menu(msg: Message, cfg: ConfigManager) -> None:
    await msg.reply_text(_menu_text(cfg), parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)


# ── Category loader ────────────────────────────────────────────────────────────

# Загрузить категории из Notion (с кэшированием)
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

# /start — главное меню
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

# Статус конфигурации для отображения
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
        "• Режим 🔔 Детальный — каждая трата → сообщение с выбором категории\n"
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
        "• Кнопка ❌ Не сохранять — отменить сохранение прямо из сообщения (Детальный режим)\n"
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
    "v1.4.2": (
        "🔔 <b>v1.4.2 — Уведомления о лимите категорий</b>\n\n"
        "• ⚠️ Предупреждение когда остаток по категории ≤ 25% лимита — раз в месяц\n"
        "• 🚨 Уведомление при превышении лимита категории — раз в месяц\n"
        "• Дедупликация уведомлений через <code>limit_notifications.json</code>"
    ),
    "v1.4.3": (
        "🧹 <b>v1.4.3 — Очистка бюджета категорий</b>\n\n"
        "• Удалена устаревшая логика бюджета по категориям\n"
        "• Суммы в бюджетных сообщениях округлены до целых гривен"
    ),
    "v1.4.4": (
        "💰 <b>v1.4.4 — Улучшения статистики и отчёты</b>\n\n"
        "• 📊 Статистика загружается быстрее — один запрос к Notion вместо трёх\n"
        "• 📆 Средняя допустимая трата в день по каждой категории (остаток / дней до конца месяца)\n"
        "• 📄 Ежемесячный PDF-отчёт с графиками — приходит 1-го числа каждого месяца\n"
        "• 📝 История фидбеков — /feedbacks, статусы «решено» / «в работе», ничего не удаляется\n"
        "• 🕐 Ручное добавление: время-пример и ввод теперь в локальном времени (не UTC)\n"
        "• ⚠️ Уведомления об общем бюджете ≤25% и при превышении"
    ),
    "v1.5": (
        "🚀 <b>v1.5 — Аналитика и отчёты</b>\n"
        "───────────────────────\n\n"
        "📊 <b>Улучшения статистики</b>\n\n"
        "⚡ <b>Мгновенная загрузка</b>\n"
        "Статистика теперь открывается сразу — данные кэшируются и обновляются автоматически после каждой транзакции\n\n"
        "📆 <b>Лимит расходов в день</b>\n"
        "В статистике появился показатель: сколько можно тратить в день до конца месяца — по каждой категории и по общему бюджету\n\n"
        "🚨 <b>Индикатор превышения</b>\n"
        "Если лимит превышен — вместо дневной суммы отображается «Бюджет превышен»\n\n"
        "───────────────────────\n"
        "📄 <b>PDF-отчёт</b>\n\n"
        "Отчёт за любой месяц прямо в боте. Используй команду /report, выбери нужный месяц — бот пришлёт PDF-файл.\n\n"
        "Отчёт включает:\n"
        "  · круговую диаграмму расходов по категориям\n"
        "  · столбчатый график по категориям\n"
        "  · график расходов по дням с накопительной линией\n"
        "  · полный список транзакций\n\n"
        "───────────────────────\n"
        "🐛 <b>Исправления</b>\n\n"
        "🕐 <b>Время при ручном добавлении</b>\n"
        "При добавлении транзакции вручную время отображалось в UTC вместо местного — исправлено"
    ),
}

_VERSIONS_ORDERED = ["v1.5", "v1.4.4", "v1.4.3", "v1.4.2", "v1.4.1", "v1.4.0", "v1.2.1", "v1.2", "v1.1", "v1.0"]

_VERSION_KB = _kb(
    ["v1.5",   "v1.4.4"],
    ["v1.4.2", "v1.4.1"],
    ["v1.4.0", "v1.2.1"],
    ["v1.2",   "v1.1"],
    ["v1.0"],
    ["◀️ Назад к настройкам"],
)


# Вход в настройки (/config)
async def settings_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    cfg = _cfg(ctx)
    await update.message.reply_text(
        "⚙️ <b>Настройки</b>", parse_mode=ParseMode.HTML,
        reply_markup=_settings_kb(cfg.get_mode(), cfg.get_notes_enabled(), cfg.get_smart_cats_enabled())
    )
    return SETTINGS_MENU


# Выбор раздела в настройках
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

    if t == "🔔 Детальный режим":
        cfg.set_mode("pro")
        await update.message.reply_text(
            "🔔 <b>Детальный режим включён!</b>\n\n"
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


# Просмотр истории версий
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


# Выбор поля конфигурации
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


# Ожидание нового значения поля конфигурации
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


# Выбор аккаунта Монобанка из списка
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


# Выход из настроек
async def settings_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Отменено.")
    await _main_menu(update.message, _cfg(ctx))
    return ConversationHandler.END


# Создать ConversationHandler для настроек
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

# Вход в поток добавления транзакции (/add)
async def add_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["mode"] = "add"
    asyncio.create_task(_load_cats(ctx))  # preload while user types description
    return await _show_desc(update.message, "add")


# Вход в создание шаблона
async def create_template_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["mode"] = "template"
    asyncio.create_task(_load_cats(ctx))  # preload while user types name
    return await _show_desc(update.message, "template")


# ── Step handlers ──────────────────────────────────────────────────────────────

# Ввод описания транзакции
async def add_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Отменить":
        ctx.user_data.clear()
        await _main_menu(update.message, _cfg(ctx))
        return ConversationHandler.END

    ctx.user_data["add_desc"] = t
    return await _show_amount(update.message, mode)


# Ввод суммы транзакции
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


# Выбор знака (расход/доход)
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


# Выбор времени транзакции
async def add_time_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Назад":
        return await _show_sign(update.message, mode, ctx.user_data.get("add_amount_abs", 0))

    if t == "🕐 Сейчас":
        ctx.user_data["add_dt"] = _local_now()
        return await _show_category(update.message, ctx, mode)

    if t == "📅 Указать дату и время":
        _now_example = _local_now().strftime("%d.%m.%Y %H:%M")
        await update.message.reply_text(
            "Введи дату и время в формате:\n"
            f"<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\nПример: <code>{_now_example}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=BACK_KB,
        )
        return ADD_CUSTOM_TIME

    await update.message.reply_text("Выбери кнопку:", reply_markup=TIME_KB)
    return ADD_TIME_CHOICE


# Ввод своего времени
async def add_custom_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t    = update.message.text.strip()
    mode = ctx.user_data.get("mode", "add")

    if t == "◀️ Назад":
        return await _show_time(update.message)

    try:
        ctx.user_data["add_dt"] = _parse_local_dt(t)
    except ValueError:
        await update.message.reply_text(
            f"❌ Неверный формат. Пример: <code>{_local_now().strftime('%d.%m.%Y %H:%M')}</code>",
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


# Выбор категории из списка
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

# Сохранить как шаблон
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


# Финальное сохранение транзакции в Notion
async def _finalize_add(msg: Message, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    desc   = ctx.user_data["add_desc"]
    amt    = ctx.user_data["add_amount"]
    dt     = ctx.user_data.get("add_dt", _local_now())
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
        if success:
            _schedule_stats_refresh(ctx)

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

# Подтверждение сохранения транзакции
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


# Ввод имени шаблона
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

# Отмена добавления транзакции
async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("❌ Отменено.")
    await _main_menu(update.message, _cfg(ctx))
    return ConversationHandler.END


# Создать ConversationHandler для добавления транзакций
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

# Вход в браузер шаблонов
async def templates_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _auth(update, ctx):
        return ConversationHandler.END
    ctx.user_data.clear()
    return await _show_tpl_list(update.message, ctx)


# ── TPL_MENU ───────────────────────────────────────────────────────────────────

# Главный экран шаблонов
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

# Детали выбранного шаблона
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

# Выбор времени применения шаблона
async def tpl_use_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        return await _show_tpl_detail(update.message, ctx)

    if t == "🕐 Сейчас":
        return await _apply_template(update.message, ctx, _local_now())

    if t == "📅 Указать дату и время":
        _now_example = _local_now().strftime("%d.%m.%Y %H:%M")
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
        dt = _parse_local_dt(t)
    except ValueError:
        await update.message.reply_text(
            f"❌ Неверный формат. Пример: <code>{_local_now().strftime('%d.%m.%Y %H:%M')}</code>",
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

        if success:
            _schedule_stats_refresh(ctx)
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



# Обработать очередь вебхука Монобанка (Про/Тихий режим)
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
                    _schedule_stats_refresh(ctx)
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

# Пользователь выбрал категорию → шаг заметки
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


# Пропустить транзакцию без сохранения
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


# Пропустить заметку → сохранить в Notion
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
    """Send a budget alert in exactly two cases:

    1. ≤ 25% of the monthly budget remains  — once per category per month.
    2. Monthly budget is exceeded            — once per category per month.

    Both checks are deduplicated via limit_notifications.json so they
    survive bot restarts and never spam the user.
    """
    if cat_limit <= 0:
        return

    threshold = cat_limit * 0.25   # 25% of monthly budget

    if cat_rem <= 0:
        level = "exceeded"
    elif cat_rem <= threshold:
        level = "low25"
    else:
        return  # plenty remaining — no notification

    # Persistent dedup: one alert per (category, month, level)
    year_month = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    dedup_key  = f"{cat_display}:{year_month}:{level}"
    if _limit_store.already_notified(dedup_key):
        return
    _limit_store.mark_notified(dedup_key)

    if level == "exceeded":
        text = (
            f"🚨 <b>Бюджет исчерпан</b>\n\n"
            f"🏷 <b>{cat_display}</b>\n"
            f"Средства по этой категории на этот месяц закончились."
        )
    else:  # low25
        text = (
            f"⚠️ <b>Бюджет на исходе</b>\n\n"
            f"🏷 <b>{cat_display}</b>\n"
            f"Осталось меньше 25% — <b>{cat_rem:,.0f} ₴</b>"
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
    """Save a card transaction to Notion.

    Shows ✅ immediately after the Notion write completes, then updates
    the message with budget/remaining info in the background — the user
    is NOT blocked waiting for that second fetch.
    """
    item            = pending["item"]
    category_id     = pending.get("category_id")
    cat_display     = pending.get("cat_display", "без категории")
    original_text   = pending.get("text", "")
    message_id      = pending.get("message_id")
    pending_chat_id = pending.get("chat_id", chat_id)

    desc   = item.get("description", "Транзакция")
    amount = item.get("amount", 0) / 100
    dt     = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)

    cat_label  = f"🏷 {cat_display}" if cat_display != "без категории" else "⬜ Без категории"
    note_label = f"\n💬 {notes}" if notes else ""

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

    if saved and category_id and cat_display != "без категории":
        _smart_cats.set(desc, category_id, cat_display)
    if saved:
        _schedule_stats_refresh(ctx)

    _pending_store.remove(txn_id)

    status_line = "✅ <b>Сохранено в Notion</b>" if saved else "⚠️ <b>Ошибка сохранения в Notion</b>"
    result_text = original_text + f"\n\n{cat_label}{note_label}\n\n{status_line}"

    # ── Immediate edit — user sees ✅ right away, no budget wait ──────────────
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
                    chat_id=_edit_chat, text=result_text, parse_mode=ParseMode.HTML,
                )
            except Exception as exc2:
                logger.error("Failed to send result message: %s", exc2)

    # ── Background budget update — fires and forgets, does NOT block ──────────
    if saved and category_id and notion:
        asyncio.create_task(_update_budget_display(
            ctx=ctx,
            chat_id=_edit_chat,
            message_id=_edit_mid,
            base_text=result_text,
            category_id=category_id,
            cat_display=cat_display,
            notify_chat_id=pending_chat_id,
        ))


async def _update_budget_display(
    ctx,
    chat_id:        str,
    message_id,
    base_text:      str,
    category_id:    str,
    cat_display:    str,
    notify_chat_id: str,
) -> None:
    """Background task: fetch category/total remaining, edit the message, then
    fire limit notifications for both the category and the overall budget.

    Runs concurrently — the user is never blocked waiting for this.
    Any failure is logged and silently ignored.
    """
    await asyncio.sleep(0.8)   # give Notion time to recompute formula properties
    notion = _notion(ctx)
    if not notion or not chat_id or not message_id:
        return
    try:
        limit_prop = _cfg(ctx).get("NOTION_LIMIT_PROP", "").strip()

        # Always fetch: per-category remaining + total remaining
        # When limit_prop is set: also fetch per-category limit + total budget limit
        coros = [
            asyncio.to_thread(notion.get_category_remaining, category_id),  # [0]
            asyncio.to_thread(notion.get_total_remaining),                  # [1]
        ]
        if limit_prop:
            coros.append(asyncio.to_thread(notion.get_category_limit, category_id))  # [2]
            coros.append(asyncio.to_thread(notion.get_total_budget))                 # [3]

        results      = await asyncio.wait_for(asyncio.gather(*coros), timeout=10)
        cat_rem      = results[0]
        total_rem    = results[1]
        cat_limit    = results[2] if limit_prop and len(results) > 2 else None
        total_budget = results[3] if limit_prop and len(results) > 3 else None

        # ── Update the transaction message with remaining amounts ──────────────
        budget_lines = ""
        if cat_rem is not None:
            budget_lines += f"\n💼 По категории осталось: {cat_rem:,.0f} ₴"
        if total_rem is not None:
            budget_lines += f"\n💰 Общий бюджет: {total_rem:,.0f} ₴"

        if budget_lines:
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=base_text + budget_lines,
                parse_mode=ParseMode.HTML, reply_markup=None,
            )

        # ── Per-category limit notification ───────────────────────────────────
        if cat_rem is not None and cat_limit is not None:
            await _check_limit_notification(
                ctx=ctx, cat_rem=cat_rem, cat_limit=cat_limit,
                cat_display=cat_display, chat_id=notify_chat_id,
            )

        # ── Total budget notification ─────────────────────────────────────────
        if total_rem is not None and total_budget is not None:
            await _check_total_budget_notification(
                ctx=ctx, total_rem=total_rem, total_budget=total_budget,
                chat_id=notify_chat_id,
            )

    except Exception as exc:
        logger.debug("Budget display update skipped (non-critical): %s", exc)


async def _check_total_budget_notification(
    ctx,
    total_rem:    float,
    total_budget: float,
    chat_id:      str,
) -> None:
    """Send a total-budget alert in exactly two cases:

    1. ≤ 25% of the total monthly budget remains — once per month.
    2. Total monthly budget is exceeded          — once per month.
    """
    if total_budget <= 0:
        return

    threshold = total_budget * 0.25

    if total_rem <= 0:
        level = "exceeded"
    elif total_rem <= threshold:
        level = "low25"
    else:
        return

    year_month = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    dedup_key  = f"TOTAL:{year_month}:{level}"
    if _limit_store.already_notified(dedup_key):
        return
    _limit_store.mark_notified(dedup_key)

    if level == "exceeded":
        text = (
            "🚨 <b>Общий бюджет исчерпан</b>\n\n"
            "Расходы за этот месяц превысили общий бюджет."
        )
    else:  # low25
        text = (
            f"⚠️ <b>Общий бюджет на исходе</b>\n\n"
            f"Осталось меньше 25% — <b>{total_rem:,.0f} ₴</b>"
        )

    try:
        await ctx.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.error("Failed to send total budget notification: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# /stats — monthly analytics
# ═════════════════════════════════════════════════════════════════════════════

def _bar(ratio: float, width: int = 10) -> str:
    ratio  = max(0.0, min(1.0, ratio))
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


async def _build_stats_text(notion: "NotionService") -> str:
    """Fetch data from Notion and return a ready-to-send HTML stats string."""
    import calendar as _cal

    now        = _local_now()
    this_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    days_in_month  = _cal.monthrange(now.year, now.month)[1]
    days_remaining = days_in_month - now.day + 1
    day_progress   = now.day / days_in_month

    cats_full, this_txns = await asyncio.gather(
        asyncio.to_thread(notion.get_categories_full),
        asyncio.to_thread(notion.get_transactions_by_period, this_start, now),
    )

    cat_name_map: dict[str, str]         = {c["id"].replace("-", ""): c["name"]      for c in cats_full}
    cat_remaining: dict[str, float|None] = {c["id"].replace("-", ""): c["remaining"] for c in cats_full}
    cat_limits:    dict[str, float|None] = {c["id"].replace("-", ""): c["limit"]     for c in cats_full}
    total_rem = sum(r for r in cat_remaining.values() if r is not None) or None

    by_cat:    dict[str, float] = {}
    by_cat_id: dict[str, str]   = {}
    total_exp = 0.0
    total_inc = 0.0
    for txn in this_txns:
        amt    = txn.get("amount") or 0.0
        cat_id = (txn.get("category_id") or "").replace("-", "")
        cname  = cat_name_map.get(cat_id) or "Без категории"
        if amt > 0:
            by_cat[cname]    = by_cat.get(cname, 0.0) + amt
            by_cat_id[cname] = cat_id
            total_exp       += amt
        elif amt < 0:
            total_inc += (-amt)

    this_month = _MONTHS_RU[now.month - 1]

    budget_block = ""
    if total_rem is not None:
        total_budget = total_exp + total_rem
        if total_budget > 0:
            budget_pct = total_exp / total_budget * 100
            budget_bar = _bar(total_exp / total_budget)
            if total_rem <= 0:
                daily_hint = "🚨 Бюджет превышен"
            else:
                daily_val  = total_rem / days_remaining if days_remaining > 0 else 0
                daily_hint = f"📆 ~{daily_val:,.0f} ₴/день"
            budget_block = (
                f"\n💼 <b>Общий бюджет</b>\n"
                f"<code>{budget_bar}</code>  {budget_pct:.0f}%"
                f"  ·  {total_exp:,.0f} / {total_budget:,.0f} ₴\n"
                f"   Остаток: {total_rem:,.0f} ₴  ·  {daily_hint}"
            )

    cat_lines: list[str] = []
    for cname in sorted(by_cat, key=lambda c: by_cat[c], reverse=True):
        spent = by_cat[cname]
        cid   = by_cat_id.get(cname, "")
        limit = cat_limits.get(cid) if cid else None
        rem   = cat_remaining.get(cid) if cid else None

        if limit and limit > 0:
            pct  = spent / limit * 100
            bar  = _bar(spent / limit)
            line = (
                f"<b>{cname}</b>\n"
                f"<code>{bar}</code>  {pct:.0f}%"
                f"  ·  {spent:,.0f} / {limit:,.0f} ₴"
            )
            if rem is not None:
                if rem <= 0:
                    line += "\n   🚨 Бюджет превышен"
                else:
                    daily = rem / days_remaining if days_remaining > 0 else 0
                    line += f"\n   📆 ~{daily:,.0f} ₴/день"
        else:
            pct  = spent / total_exp * 100 if total_exp > 0 else 0.0
            bar  = _bar(spent / total_exp if total_exp > 0 else 0.0)
            line = (
                f"<b>{cname}</b>\n"
                f"<code>{bar}</code>  {pct:.0f}% от расходов"
                f"  ·  {spent:,.0f} ₴"
            )
        cat_lines.append(line)

    day_bar  = _bar(day_progress)
    day_info = f"<code>{day_bar}</code>  {now.day}/{days_in_month} дней  (осталось {days_remaining})"

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
        spaced: list[str] = []
        for i, line in enumerate(cat_lines):
            spaced.append(line)
            if i < len(cat_lines) - 1:
                spaced.append("")
        parts += ["", "📋 <b>По категориям:</b>", ""] + spaced
    else:
        parts += ["", "<i>Транзакций за этот месяц не найдено</i>"]

    return "\n".join(parts)


# Обновить кэш статистики в фоне
async def _refresh_stats_cache(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB job: rebuild stats cache in background. Skips if already running."""
    if ctx.bot_data.get("stats_refreshing"):
        return
    notion = _notion(ctx)
    if not notion:
        return
    ctx.bot_data["stats_refreshing"] = True
    try:
        text = await _build_stats_text(notion)
        ctx.bot_data["stats_cache"] = {"text": text, "updated_at": _local_now()}
        logger.info("Stats cache updated")
    except Exception as exc:
        logger.error("Stats cache refresh failed: %s", exc)
    finally:
        ctx.bot_data["stats_refreshing"] = False


# Запланировать обновление кэша статистики
def _schedule_stats_refresh(ctx: ContextTypes.DEFAULT_TYPE, delay: float = 5.0) -> None:
    """Schedule a background stats rebuild after a transaction is saved."""
    try:
        # Remove existing pending refresh to avoid pile-up
        current = ctx.application.job_queue.get_jobs_by_name("stats_refresh")
        for job in current:
            job.schedule_removal()
        ctx.application.job_queue.run_once(
            _refresh_stats_cache, when=delay, name="stats_refresh"
        )
    except Exception as exc:
        logger.debug("Could not schedule stats refresh: %s", exc)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show stats from cache instantly; rebuild cache in background if stale."""
    if not _auth(update, ctx):
        return

    notion = _notion(ctx)
    if not notion:
        await update.message.reply_text(
            "⚠️ Notion не настроен. Зайди в ⚙️ Настройки → ⚙️ Конфигурация."
        )
        return

    cache = ctx.bot_data.get("stats_cache")

    if cache:
        age_sec = int((_local_now() - cache["updated_at"]).total_seconds())
        if age_sec < 60:
            freshness = "<i>🟢 только что обновлено</i>"
        else:
            days, rem = divmod(age_sec, 86400)
            hours, rem = divmod(rem, 3600)
            mins = rem // 60
            parts = []
            if days:  parts.append(f"{days} д.")
            if hours: parts.append(f"{hours} ч.")
            if mins or not parts: parts.append(f"{mins} мин.")
            freshness = f"<i>🕐 обновлено {' '.join(parts)} назад</i>"

        await update.message.reply_text(
            cache["text"] + f"\n\n{freshness}",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KB,
        )
        # Refresh in background if older than 10 minutes
        if age_sec > 600:
            _schedule_stats_refresh(ctx, delay=0)
        return

    # No cache yet — build synchronously (first run)
    await update.message.reply_text("📊 Загружаю статистику…", reply_markup=MAIN_KB)
    try:
        text = await _build_stats_text(notion)
    except Exception as exc:
        logger.error("Stats fetch failed: %s", exc)
        await update.message.reply_text("❌ Не удалось загрузить данные из Notion.")
        return

    ctx.bot_data["stats_cache"] = {"text": text, "updated_at": _local_now()}
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KB)


# ═════════════════════════════════════════════════════════════════════════════
# Feedback conversation (all users can submit, no _auth required)
# ═════════════════════════════════════════════════════════════════════════════

FEEDBACK_TYPE  = 100
FEEDBACK_TEXT  = 101
FEEDBACK_VOICE = 102

_DEVELOPER_FEEDBACK_URL = os.getenv("DEVELOPER_FEEDBACK_URL") or os.getenv("FEEDBACK_BOT_URL", "")

_FEEDBACK_TYPE_KB = _kb(["🐛 Баг", "🌟 Хотелка"], ["◀️ Назад"])
_FEEDBACK_BACK_KB = _kb(["◀️ Назад"])


# Точка входа в фидбек (/feedback или кнопка)
async def feedback_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /feedback command or 📝 Фидбек button."""
    await update.message.reply_html(
        "📝 <b>Обратная связь</b>\n\n"
        "Опиши проблему или идею.\n"
        "Можно отправить текст или 🎤 голосовое сообщение.",
        reply_markup=_FEEDBACK_BACK_KB,
    )
    return FEEDBACK_TEXT


# Обработка текстового фидбека
async def feedback_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        cfg = _cfg(ctx)
        await _main_menu(update.message, cfg)
        return ConversationHandler.END

    ctx.user_data["feedback_draft"] = t
    await update.message.reply_html(
        "Выбери тип заявки:",
        reply_markup=_FEEDBACK_TYPE_KB,
    )
    return FEEDBACK_TYPE


# Выбор типа фидбека (Баг/Фича) и отправка
async def feedback_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()

    if t == "◀️ Назад":
        await update.message.reply_html(
            "📝 <b>Обратная связь</b>\n\n"
            "Опиши проблему или идею.\n"
            "Можно отправить текст или 🎤 голосовое сообщение.",
            reply_markup=_FEEDBACK_BACK_KB,
        )
        return FEEDBACK_TEXT

    if t not in ("🐛 Баг", "🌟 Хотелка"):
        await update.message.reply_text("Выбери тип:", reply_markup=_FEEDBACK_TYPE_KB)
        return FEEDBACK_TYPE

    fb_type = "bug" if t == "🐛 Баг" else "feature"
    text    = ctx.user_data.pop("feedback_draft", "")

    user     = update.message.from_user
    username = user.username or user.first_name or str(user.id)
    payload  = {
        "project":       "monobank-finance-bot",
        "type":          fb_type,
        "text":          text,
        "from_user_id":  user.id,
        "from_username": username,
        "timestamp":     datetime.now(tz=timezone.utc).isoformat(),
        "version":       BOT_VERSION,
    }

    cfg     = _cfg(ctx)
    dev_url = cfg.get("DEVELOPER_FEEDBACK_URL", "").strip() or _DEVELOPER_FEEDBACK_URL
    result  = await asyncio.to_thread(_http_post_feedback, dev_url, payload)
    if not result:
        logger.warning("Feedback POST failed to %s", dev_url)

    # If this was a voice feedback, also send the voice file
    ogg   = ctx.user_data.pop("feedback_voice_ogg", None)
    trans = ctx.user_data.pop("feedback_voice_trans", "")
    if ogg:
        feedback_id = result if isinstance(result, str) else "unknown"
        await asyncio.to_thread(_http_post_voice, dev_url, ogg, feedback_id, trans)

    type_label = "🐛 Баг" if fb_type == "bug" else "🌟 Хотелка"
    await update.message.reply_html(
        f"✅ <b>Заявка принята!</b>\n\n"
        f"{type_label}\n\n"
        "Спасибо за фидбек — разработчик получит уведомление.",
        reply_markup=MAIN_KB,
    )
    return ConversationHandler.END


# Транскрипция голосового фидбека
async def feedback_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Transcribe voice silently, ask user to confirm without showing transcription."""
    import voice_handler
    voice   = update.message.voice
    tg_file = await ctx.bot.get_file(voice.file_id)
    ogg     = await tg_file.download_as_bytearray()

    transcription = await asyncio.to_thread(voice_handler.transcribe, bytes(ogg))

    ctx.user_data["feedback_draft"]        = transcription or "[голосовое сообщение без расшифровки]"
    ctx.user_data["feedback_voice_ogg"]    = bytes(ogg)
    ctx.user_data["feedback_voice_trans"]  = transcription or ""
    ctx.user_data["feedback_voice_msg_id"] = update.message.message_id

    await update.message.reply_text(
        "🎤 Голосовое получено. Всё верно?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да", callback_data="fbv:confirm"),
            InlineKeyboardButton("✏️ Переписать", callback_data="fbv:retry"),
        ]]),
    )
    return FEEDBACK_VOICE


# Подтверждение/отмена голосового фидбека
async def feedback_voice_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    if query.data == "fbv:retry":
        voice_msg_id = ctx.user_data.pop("feedback_voice_msg_id", None)
        ctx.user_data.pop("feedback_draft", None)
        ctx.user_data.pop("feedback_voice_ogg", None)
        ctx.user_data.pop("feedback_voice_trans", None)
        # Delete the confirm prompt and the original voice message
        try:
            await query.message.delete()
        except Exception:
            pass
        if voice_msg_id:
            try:
                await query.bot.delete_message(chat_id, voice_msg_id)
            except Exception:
                pass
        await ctx.bot.send_message(
            chat_id,
            "📝 Опиши проблему или идею.\nМожно отправить текст или 🎤 голосовое сообщение.",
            reply_markup=_FEEDBACK_BACK_KB,
        )
        return FEEDBACK_TEXT

    # confirmed — remove inline buttons, ask for type via new message
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await ctx.bot.send_message(chat_id, "Выбери тип заявки:", reply_markup=_FEEDBACK_TYPE_KB)
    return FEEDBACK_TYPE


# Отмена фидбека
async def feedback_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = _cfg(ctx)
    await _main_menu(update.message, cfg)
    return ConversationHandler.END


# HTTP POST фидбека в feedback-bot (синхронный, запускается в потоке)
def _http_post_feedback(url: str, payload: dict) -> str | bool:
    """POST feedback, return feedback_id string on success or False on failure."""
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
            body = _json.loads(resp.read())
            return body.get("id", True)
    except Exception as exc:
        logger.debug("_http_post_feedback error: %s", exc)
        return False


# HTTP POST голосового файла в feedback-bot (multipart)
def _http_post_voice(url: str, ogg: bytes, feedback_id: str, transcription: str) -> None:
    """POST voice file as multipart/form-data to /feedback/voice."""
    import io
    import urllib.request as _req
    boundary = b"----TGVoiceBoundary"
    body = b""
    for name, value in (("feedback_id", feedback_id.encode()), ("transcription", transcription.encode("utf-8"))):
        body += b"--" + boundary + b"\r\n"
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += value + b"\r\n"
    body += b"--" + boundary + b"\r\n"
    body += b'Content-Disposition: form-data; name="file"; filename="voice.ogg"\r\n'
    body += b"Content-Type: audio/ogg\r\n\r\n"
    body += ogg + b"\r\n"
    body += b"--" + boundary + b"--\r\n"
    request = _req.Request(
        url.rstrip("/") + "/feedback/voice",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
        method="POST",
    )
    try:
        with _req.urlopen(request, timeout=10):
            pass
    except Exception as exc:
        logger.debug("_http_post_voice error: %s", exc)


# Создать ConversationHandler для фидбека
def make_feedback_handler() -> ConversationHandler:
    """ConversationHandler for the feedback flow. No auth — open to all users."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("feedback", feedback_start),
            MessageHandler(filters.Regex(r"^📝 Фидбек$"), feedback_start),
        ],
        states={
            FEEDBACK_TEXT: [
                MessageHandler(_TXT, feedback_text),
                MessageHandler(filters.VOICE, feedback_voice),
            ],
            FEEDBACK_TYPE:  [MessageHandler(_TXT, feedback_type)],
            FEEDBACK_VOICE: [CallbackQueryHandler(feedback_voice_confirm, pattern=r"^fbv:")],
        },
        fallbacks=[CommandHandler("cancel", feedback_cancel)],
        per_user=True, per_chat=True, per_message=False,
        allow_reentry=True,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Monthly report — PDF with charts, sent on 1st of each month
# ═════════════════════════════════════════════════════════════════════════════

def _enrich_txns(txns: list[dict], cats_full: list[dict]) -> list[dict]:
    """Add 'category' name field to each transaction using cats_full id→name map."""
    id_to_name = {c["id"].replace("-", ""): c["name"] for c in cats_full}
    enriched = []
    for t in txns:
        cat_id   = (t.get("category_id") or "").replace("-", "")
        cat_name = id_to_name.get(cat_id, "Без категории") if cat_id else "Без категории"
        enriched.append({**t, "category": cat_name})
    return enriched


def _generate_monthly_pdf(
    month_name: str,
    year: int,
    month: int,
    by_cat: dict[str, float],   # category name → total spent
    total_exp: float,
    total_inc: float,
    txns: list[dict],           # enriched: each has 'name', 'amount', 'category', 'date'
) -> bytes:
    """Generate a multi-page PDF report with charts and full transaction list."""
    import io
    import calendar as _cal
    from collections import defaultdict
    from datetime import date as _date
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.backends.backend_pdf as pdf_backend

    plt.rcParams["font.family"] = "DejaVu Sans"

    PALETTE = [
        "#4e9af1", "#f1914e", "#4ec27d", "#f14e6e", "#a04ef1",
        "#f1d44e", "#4ecdf1", "#f1724e", "#7df14e", "#f14eb0",
        "#6e9ef1", "#f1b04e",
    ]
    WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    buf = io.BytesIO()
    with pdf_backend.PdfPages(buf) as pdf:

        # ── PAGE 1: Summary + Pie chart ──────────────────────────────────────
        fig = plt.figure(figsize=(13, 9))
        fig.patch.set_facecolor("#f8f9fc")

        fig.text(0.5, 0.96, f"Финансовый отчёт  —  {month_name.capitalize()} {year}",
                 ha="center", fontsize=19, fontweight="bold", color="#1a2040")
        fig.text(0.5, 0.925, f"Транзакций: {len(txns)}   |   Расходы: {total_exp:,.0f} ₴   "
                             f"|   Доходы: {total_inc:,.0f} ₴   |   Баланс: {total_inc - total_exp:+,.0f} ₴",
                 ha="center", fontsize=10, color="#555")

        # Pie chart (left 50%)
        ax_pie = fig.add_axes([0.02, 0.08, 0.46, 0.80])
        cat_sorted_desc = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)
        if cat_sorted_desc:
            labels = [c for c, _ in cat_sorted_desc]
            values = [v for _, v in cat_sorted_desc]
            colors = PALETTE[:len(labels)]
            wedges, _, autotexts = ax_pie.pie(
                values, labels=None,
                autopct=lambda p: f"{p:.1f}%" if p >= 3 else "",
                startangle=140, colors=colors,
                wedgeprops=dict(edgecolor="white", linewidth=1.8),
                pctdistance=0.72,
            )
            for at in autotexts:
                at.set_fontsize(7.5)
            ax_pie.legend(
                wedges,
                [f"{l}  ({v:,.0f} ₴)" for l, v in zip(labels, values)],
                loc="lower center", bbox_to_anchor=(0.5, -0.13),
                fontsize=7.5, ncol=2, frameon=False,
            )
            ax_pie.set_title("Расходы по категориям", fontsize=11, pad=8, color="#333")
        else:
            ax_pie.text(0.5, 0.5, "Нет данных", ha="center", va="center")
            ax_pie.axis("off")

        # Summary table (right 45%)
        ax_tbl = fig.add_axes([0.52, 0.08, 0.46, 0.80])
        ax_tbl.axis("off")
        ax_tbl.set_xlim(0, 1)
        ax_tbl.set_ylim(0, 1)

        balance = total_inc - total_exp
        bal_color = "#27ae60" if balance >= 0 else "#c0392b"

        # Totals block
        for y_pos, label, value, color in [
            (0.92, "Расходы",  f"{total_exp:>12,.2f} ₴", "#c0392b"),
            (0.84, "Доходы",   f"{total_inc:>12,.2f} ₴", "#27ae60"),
            (0.76, "Баланс",   f"{balance:>+12,.2f} ₴",  bal_color),
        ]:
            ax_tbl.text(0.04, y_pos, label, fontsize=11, color="#444", va="top")
            ax_tbl.text(0.96, y_pos, value, fontsize=11, color=color, va="top",
                        ha="right", fontfamily="monospace", fontweight="bold")
        ax_tbl.axhline(0.73, color="#ccd", linewidth=0.8, xmin=0.02, xmax=0.98)

        # Categories breakdown
        ax_tbl.text(0.04, 0.69, "Категория", fontsize=8, color="#777", va="top", fontweight="bold")
        ax_tbl.text(0.96, 0.69, "Сумма  / %", fontsize=8, color="#777", va="top",
                    ha="right", fontweight="bold")
        ax_tbl.axhline(0.66, color="#eee", linewidth=0.5, xmin=0.02, xmax=0.98)

        y_cat = 0.63
        for i, (cname, spent) in enumerate(cat_sorted_desc):
            if y_cat < 0.02:
                break
            pct   = spent / total_exp * 100 if total_exp else 0
            color = PALETTE[i % len(PALETTE)]
            ax_tbl.add_patch(mpatches.Rectangle((0.02, y_cat - 0.01), 0.008, 0.030,
                                                  facecolor=color, edgecolor="none"))
            ax_tbl.text(0.05, y_cat, cname[:26], fontsize=8, color="#333", va="center")
            ax_tbl.text(0.96, y_cat, f"{spent:>9,.0f} ₴  {pct:4.1f}%",
                        fontsize=8, color="#333", va="center", ha="right", fontfamily="monospace")
            ax_tbl.axhline(y_cat - 0.018, color="#f0f0f0", linewidth=0.4, xmin=0.02, xmax=0.98)
            y_cat -= 0.038

        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── PAGE 2: Horizontal category bar chart ────────────────────────────
        if cat_sorted_desc:
            cat_asc = sorted(by_cat.items(), key=lambda x: x[1])
            n       = len(cat_asc)
            height  = max(5, n * 0.55 + 2)

            fig2, ax2 = plt.subplots(figsize=(13, height))
            fig2.patch.set_facecolor("#f8f9fc")

            names  = [c for c, _ in cat_asc]
            values = [v for _, v in cat_asc]
            colors = [PALETTE[i % len(PALETTE)] for i in range(n - 1, -1, -1)]
            max_v  = max(values) if values else 1

            bars = ax2.barh(names, values, color=colors, edgecolor="white",
                            height=0.62, linewidth=1.2)
            for bar, val in zip(bars, values):
                pct = val / total_exp * 100 if total_exp else 0
                ax2.text(bar.get_width() + max_v * 0.01,
                         bar.get_y() + bar.get_height() / 2,
                         f"{val:,.0f} ₴  ({pct:.1f}%)",
                         va="center", fontsize=8.5, color="#333")

            ax2.set_xlim(0, max_v * 1.35)
            ax2.set_xlabel("Сумма, ₴", fontsize=10)
            ax2.set_title(f"Расходы по категориям  —  {month_name.capitalize()} {year}",
                          fontsize=13, fontweight="bold", pad=12, color="#1a2040")
            ax2.spines["top"].set_visible(False)
            ax2.spines["right"].set_visible(False)
            ax2.tick_params(axis="y", labelsize=9)

            plt.tight_layout()
            pdf.savefig(fig2, bbox_inches="tight")
            plt.close(fig2)

        # ── PAGE 3: Daily bar chart + cumulative line ─────────────────────────
        days_in_month = _cal.monthrange(year, month)[1]
        daily_exp: dict[int, float] = {d: 0.0 for d in range(1, days_in_month + 1)}
        daily_inc: dict[int, float] = {d: 0.0 for d in range(1, days_in_month + 1)}

        for txn in txns:
            amt = txn.get("amount") or 0.0
            ds  = txn.get("date", "")
            if not ds:
                continue
            try:
                day = int(ds[8:10])
            except Exception:
                continue
            if amt > 0:
                daily_exp[day] += amt
            else:
                daily_inc[day] += abs(amt)

        days     = list(range(1, days_in_month + 1))
        exp_vals = [daily_exp[d] for d in days]
        inc_vals = [daily_inc[d] for d in days]
        cum_exp  = []
        running  = 0.0
        for v in exp_vals:
            running += v
            cum_exp.append(running)

        active_days = sum(1 for v in exp_vals if v > 0)
        avg_day     = total_exp / active_days if active_days > 0 else 0

        fig3, (ax3a, ax3b) = plt.subplots(2, 1, figsize=(14, 9),
                                           gridspec_kw={"height_ratios": [3, 1.6]})
        fig3.patch.set_facecolor("#f8f9fc")
        fig3.suptitle(f"Расходы по дням  —  {month_name.capitalize()} {year}",
                      fontsize=14, fontweight="bold", color="#1a2040", y=0.99)

        bw = 0.38
        ax3a.bar([d - bw/2 for d in days], exp_vals, width=bw,
                 color="#e74c3c", alpha=0.82, label="Расходы")
        ax3a.bar([d + bw/2 for d in days], inc_vals, width=bw,
                 color="#27ae60", alpha=0.82, label="Доходы")

        ax3a_r = ax3a.twinx()
        ax3a_r.plot(days, cum_exp, color="#c0392b", linewidth=2,
                    linestyle="--", alpha=0.7, label="Накопленные расходы")
        ax3a_r.set_ylabel("Накопленные расходы, ₴", fontsize=8, color="#c0392b")
        ax3a_r.tick_params(axis="y", labelcolor="#c0392b", labelsize=7)
        ax3a_r.spines["top"].set_visible(False)

        ax3a.set_xlim(0.5, days_in_month + 0.5)
        ax3a.set_xticks(days)
        ax3a.set_ylabel("Сумма, ₴", fontsize=9)
        ax3a.legend(loc="upper left", fontsize=8)
        ax3a.spines["top"].set_visible(False)
        ax3a.spines["right"].set_visible(False)
        ax3a.tick_params(axis="x", labelsize=7)

        # Bottom: daily bars with avg line
        bar_colors = ["#e74c3c" if v > avg_day * 1.5 else "#4e9af1" for v in exp_vals]
        ax3b.bar(days, exp_vals, color=bar_colors, alpha=0.85)
        ax3b.axhline(avg_day, color="#e74c3c", linestyle="--", linewidth=1.5,
                     label=f"Среднее в активный день: {avg_day:,.0f} ₴")
        ax3b.set_xlim(0.5, days_in_month + 0.5)
        ax3b.set_xticks(days)
        ax3b.set_xlabel("День месяца", fontsize=9)
        ax3b.set_ylabel("₴", fontsize=8)
        ax3b.legend(fontsize=7.5, loc="upper right")
        ax3b.spines["top"].set_visible(False)
        ax3b.spines["right"].set_visible(False)
        ax3b.tick_params(axis="x", labelsize=7)

        plt.tight_layout()
        pdf.savefig(fig3, bbox_inches="tight")
        plt.close(fig3)

        # ── PAGE 4+: Transaction list grouped by day ──────────────────────────
        # Only expenses, sorted by date
        expense_txns = sorted(
            [t for t in txns if (t.get("amount") or 0) > 0],
            key=lambda t: t.get("date", ""),
        )

        # Group by calendar date
        by_day: dict[str, list] = defaultdict(list)
        for t in expense_txns:
            by_day[t.get("date", "")[:10]].append(t)

        # Build flat row list: header rows + txn rows
        Row = dict
        rows: list[Row] = []
        for date_key in sorted(by_day.keys()):
            day_txns  = by_day[date_key]
            day_total = sum(t.get("amount", 0) for t in day_txns)
            try:
                d        = _date.fromisoformat(date_key)
                wd       = WEEKDAYS_RU[d.weekday()]
                day_lbl  = f"{d.day} {_MONTHS_RU[d.month-1]}  ({wd})  — итого: {day_total:,.0f} ₴"
            except Exception:
                day_lbl  = date_key
            rows.append({"type": "header", "label": day_lbl})
            for t in day_txns:
                rows.append({"type": "txn", "txn": t})

        ROWS_PER_PAGE = 32
        total_pages   = max(1, (len(rows) - 1) // ROWS_PER_PAGE + 1)

        for page_idx in range(0, max(1, len(rows)), ROWS_PER_PAGE):
            page_rows = rows[page_idx: page_idx + ROWS_PER_PAGE]
            pg_num    = page_idx // ROWS_PER_PAGE + 1

            fig_t = plt.figure(figsize=(14, 10))
            fig_t.patch.set_facecolor("#f8f9fc")
            ax_t  = fig_t.add_axes([0.01, 0.02, 0.98, 0.96])
            ax_t.axis("off")
            ax_t.set_xlim(0, 1)
            ax_t.set_ylim(0, 1)

            ax_t.text(0.5, 0.985,
                      f"Список расходов  —  {month_name.capitalize()} {year}"
                      f"   (стр. {pg_num}/{total_pages})",
                      ha="center", va="top", fontsize=12, fontweight="bold", color="#1a2040")

            # Column headers
            y = 0.955
            for xpos, label, align in [
                (0.01, "Время", "left"),
                (0.07, "Описание", "left"),
                (0.63, "Категория", "left"),
                (0.99, "Сумма, ₴", "right"),
            ]:
                ax_t.text(xpos, y, label, fontsize=7.5, fontweight="bold",
                          color="#666", va="top", ha=align)
            ax_t.axhline(y - 0.012, color="#aab", linewidth=0.8)
            y -= 0.030

            for i, row in enumerate(page_rows):
                if y < 0.01:
                    break
                if row["type"] == "header":
                    ax_t.add_patch(mpatches.FancyBboxPatch(
                        (0.005, y - 0.013), 0.99, 0.022,
                        boxstyle="round,pad=0.001",
                        facecolor="#dce5f5", edgecolor="none",
                    ))
                    ax_t.text(0.015, y, row["label"],
                              fontsize=8, fontweight="bold", color="#1a3080", va="top")
                    y -= 0.030
                else:
                    t      = row["txn"]
                    name   = (t.get("name") or "")[:50]
                    cat    = (t.get("category") or "—")[:24]
                    amt    = t.get("amount") or 0.0
                    ds     = t.get("date", "")
                    time_s = ds[11:16] if len(ds) > 10 else ""

                    bg = "#f4f4f8" if i % 2 == 0 else "#fafafa"
                    ax_t.add_patch(mpatches.FancyBboxPatch(
                        (0.005, y - 0.011), 0.99, 0.018,
                        boxstyle="square,pad=0",
                        facecolor=bg, edgecolor="none",
                    ))
                    ax_t.text(0.01,  y, time_s, fontsize=7,  color="#888",   va="top")
                    ax_t.text(0.07,  y, name,   fontsize=7.5, color="#222",  va="top")
                    ax_t.text(0.63,  y, cat,    fontsize=7.5, color="#555",  va="top")
                    ax_t.text(0.99,  y, f"{amt:,.0f}",
                              fontsize=7.5, color="#c0392b", va="top", ha="right",
                              fontfamily="monospace")
                    y -= 0.025

            # Footer
            ax_t.axhline(0.025, color="#ccc", linewidth=0.5)
            ax_t.text(0.5, 0.012,
                      f"Итого расходы за {month_name}: {total_exp:,.2f} ₴   "
                      f"|   Транзакций: {len(expense_txns)}",
                      ha="center", fontsize=7.5, color="#888", va="top")

            pdf.savefig(fig_t, bbox_inches="tight")
            plt.close(fig_t)

    buf.seek(0)
    return buf.read()


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/report — show month picker to choose which month to generate PDF for."""
    if not _auth(update, ctx):
        return
    notion = _notion(ctx)
    if not notion:
        await update.message.reply_text(
            "⚠️ Notion не настроен. Зайди в ⚙️ Настройки → ⚙️ Конфигурация."
        )
        return

    now = _local_now()
    buttons: list[list] = []
    row: list = []
    for i in range(6):
        # go back i months from current
        month_offset = now.month - 1 - i
        year  = now.year + month_offset // 12
        month = month_offset % 12 + 1
        label = f"{_MONTHS_RU[month - 1].capitalize()} {year}"
        row.append(InlineKeyboardButton(label, callback_data=f"rpt:{year}:{month:02d}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Current month option
    buttons.append([InlineKeyboardButton(
        f"📅 {_MONTHS_RU[now.month-1].capitalize()} {now.year} (текущий)",
        callback_data=f"rpt:{now.year}:{now.month:02d}:current"
    )])

    await update.message.reply_text(
        "📄 <b>Выбери месяц для отчёта:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_report_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle rpt:YYYY:MM or rpt:YYYY:MM:current inline button."""
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    # parts: ["rpt", "YYYY", "MM"] or ["rpt", "YYYY", "MM", "current"]
    if len(parts) < 3:
        return

    rep_year   = int(parts[1])
    rep_month  = int(parts[2])
    is_current = len(parts) > 3 and parts[3] == "current"

    notion = _notion(ctx)
    if not notion:
        await query.edit_message_text("⚠️ Notion не настроен.")
        return

    month_name = _MONTHS_RU[rep_month - 1]
    await query.edit_message_text(f"📄 Генерирую отчёт за {month_name} {rep_year}…")

    import calendar as _cal
    if is_current:
        now          = _local_now()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_end   = now
    else:
        days_in_month = _cal.monthrange(rep_year, rep_month)[1]
        period_start  = _local_now().replace(
            year=rep_year, month=rep_month, day=1,
            hour=0, minute=0, second=0, microsecond=0
        )
        period_end = period_start.replace(day=days_in_month, hour=23, minute=59, second=59)

    try:
        cats_full, txns = await asyncio.gather(
            asyncio.to_thread(notion.get_categories_full),
            asyncio.to_thread(notion.get_transactions_by_period, period_start, period_end),
        )
    except Exception as exc:
        logger.error("handle_report_callback: fetch failed: %s", exc)
        await query.edit_message_text("❌ Не удалось загрузить данные из Notion.")
        return

    txns_enriched = _enrich_txns(txns, cats_full)

    by_cat: dict[str, float] = {}
    total_exp = 0.0
    total_inc = 0.0
    for txn in txns_enriched:
        amt = txn.get("amount") or 0.0
        if amt > 0:
            cname = txn.get("category") or "Без категории"
            by_cat[cname] = by_cat.get(cname, 0.0) + amt
            total_exp    += amt
        elif amt < 0:
            total_inc += (-amt)

    try:
        pdf_bytes = await asyncio.to_thread(
            _generate_monthly_pdf,
            month_name, rep_year, rep_month,
            by_cat, total_exp, total_inc, txns_enriched,
        )
    except Exception as exc:
        logger.error("handle_report_callback: PDF generation failed: %s", exc)
        await query.edit_message_text(f"❌ Ошибка генерации PDF: {exc}")
        return

    import io as _io
    filename = f"report_{rep_year}_{rep_month:02d}.pdf"
    caption  = (
        f"📊 <b>Отчёт за {month_name} {rep_year}</b>\n\n"
        f"💸 Расходы: {total_exp:,.2f} ₴\n"
        f"💰 Доходы:  {total_inc:,.2f} ₴\n"
        f"💼 Баланс:  {total_inc - total_exp:+,.2f} ₴"
    )
    await query.message.reply_document(
        document=_io.BytesIO(pdf_bytes),
        filename=filename,
        caption=caption,
        parse_mode=ParseMode.HTML,
    )
    await query.edit_message_text(f"✅ Отчёт за {month_name} {rep_year} готов!")


# Отправить PDF-отчёт (запускается каждый день, отправляет только 1-го числа)
async def send_monthly_report(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB daily job: on the 1st of each month, send analytics PDF for previous month."""
    cfg     = _cfg(ctx)
    chat_id = cfg.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        return

    now = _local_now()
    if now.day != 1:
        return

    # Already sent this month?
    sent_key = f"monthly_report_{now.year}_{now.month}"
    if ctx.bot_data.get(sent_key):
        return
    ctx.bot_data[sent_key] = True

    notion = _notion(ctx)
    if not notion:
        return

    import calendar as _cal

    # Previous month
    first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_of_prev  = first_of_this - timedelta(seconds=1)
    prev_year     = last_of_prev.year
    prev_month    = last_of_prev.month
    prev_month_name = _MONTHS_RU[prev_month - 1]
    days_in_prev  = _cal.monthrange(prev_year, prev_month)[1]

    prev_start = last_of_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_end   = last_of_prev

    try:
        cats_full, txns = await asyncio.gather(
            asyncio.to_thread(notion.get_categories_full),
            asyncio.to_thread(notion.get_transactions_by_period, prev_start, prev_end),
        )
    except Exception as exc:
        logger.error("Monthly report: failed to fetch data: %s", exc)
        return

    txns_enriched = _enrich_txns(txns, cats_full)

    by_cat: dict[str, float] = {}
    total_exp = 0.0
    total_inc = 0.0
    for txn in txns_enriched:
        amt = txn.get("amount") or 0.0
        if amt > 0:
            cname = txn.get("category") or "Без категории"
            by_cat[cname] = by_cat.get(cname, 0.0) + amt
            total_exp    += amt
        elif amt < 0:
            total_inc += (-amt)

    try:
        pdf_bytes = await asyncio.to_thread(
            _generate_monthly_pdf,
            prev_month_name, prev_year, prev_month,
            by_cat, total_exp, total_inc, txns_enriched,
        )
    except Exception as exc:
        logger.error("Monthly report: PDF generation failed: %s", exc)
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=(
                f"📊 <b>Итоги {prev_month_name} {prev_year}</b>\n\n"
                f"💸 Расходы: {total_exp:,.2f} ₴\n"
                f"💰 Доходы:  {total_inc:,.2f} ₴\n"
                f"💼 Баланс:  {total_inc - total_exp:+,.2f} ₴\n\n"
                "<i>(PDF не сгенерирован — проверь установку matplotlib)</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    import io as _io
    filename = f"report_{prev_year}_{prev_month:02d}.pdf"
    caption  = (
        f"📊 <b>Отчёт за {prev_month_name} {prev_year}</b>\n\n"
        f"💸 Расходы: {total_exp:,.2f} ₴\n"
        f"💰 Доходы:  {total_inc:,.2f} ₴\n"
        f"💼 Баланс:  {total_inc - total_exp:+,.2f} ₴"
    )
    await ctx.bot.send_document(
        chat_id=chat_id,
        document=_io.BytesIO(pdf_bytes),
        filename=filename,
        caption=caption,
        parse_mode=ParseMode.HTML,
    )
    logger.info("Monthly report sent: %s %d", prev_month_name, prev_year)


# ═════════════════════════════════════════════════════════════════════════════
# Template trigger queue (from /trigger HTTP endpoint)
# ═════════════════════════════════════════════════════════════════════════════

# Обработать очередь быстрых шаблонов
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
        dt         = _local_now()

        notion = _notion(ctx)
        saved  = False
        if notion:
            saved = await asyncio.to_thread(
                notion.create_transaction,
                desc, -amount, dt, category_id, notes,  # -amount: matches template-use convention
            )
            if saved:
                _smart_cats.set(desc, category_id, cat_name) if category_id else None
                _schedule_stats_refresh(ctx)
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

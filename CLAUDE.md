# Monobank Finance Bot — Setup Guide for Claude Code

This is a Telegram bot that receives Monobank card transactions via webhook and saves them to Notion. It also lets the user manually add cash transactions and manage reusable templates.

## Project Structure

```
monobank-finance-bot/
├── main.py               ← entry point
├── bot_handlers.py       ← all Telegram handlers (ConversationHandlers)
├── monobank_service.py   ← FastAPI webhook server + ngrok tunnel
├── notion_service.py     ← Notion API client
├── config_manager.py     ← .env read/write + TemplateManager (templates.json)
├── .env                  ← secrets (create from .env.example)
├── .env.example          ← template — copy this to .env
├── templates.json        ← local template storage (auto-created)
└── requirements.txt
```

## Your job: set up this project so the user can run `python main.py`

Follow these steps IN ORDER. Do not skip any step.

---

### Step 1 — Check Python version

```bash
python --version
```

Must be **3.10 or higher**. If it's lower, tell the user to install Python 3.12 from python.org.

---

### Step 2 — Create virtual environment

```bash
python -m venv venv
```

Then activate it. On Windows (Git Bash / MINGW64):
```bash
source venv/Scripts/activate
```

On Windows (CMD):
```
venv\Scripts\activate.bat
```

On Windows (PowerShell):
```
venv\Scripts\Activate.ps1
```

---

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

If it fails, try:
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### Step 4 — Create .env from template

```bash
cp .env.example .env
```

---

### Step 5 — Set TELEGRAM_BOT_TOKEN

The user MUST provide a bot token from @BotFather on Telegram. This is the only field that cannot be set inside the bot itself.

Ask the user: "What is your Telegram Bot Token?" then write it to .env:

Edit the `.env` file and set:
```
TELEGRAM_BOT_TOKEN=<token_the_user_gives_you>
```

All other tokens (Monobank, Notion, ngrok) are set interactively inside the bot via the /config menu — no need to edit .env for them.

---

### Step 6 — Verify the setup

Run this import check:
```bash
python -c "import bot_handlers; import main; print('OK — все модули загружены')"
```

If it prints `OK — все модули загружены`, the setup is complete.

If there are import errors, diagnose and fix them (usually a missing package — run `pip install <package>`).

---

### Step 7 — Run the bot

```bash
python main.py
```

The bot will print a startup banner. Then open Telegram, find the bot, and send `/start`.

---

## What the user needs to configure inside the bot

After starting, the user goes to **⚙️ Настройки → ⚙️ Конфигурация** and fills in:

| Field | Where to get it |
|-------|----------------|
| Monobank Personal Token | monobank.ua → Особисті дані → Токен |
| Monobank Account ID | Press "📋 Выбрать аккаунт Monobank" button |
| Notion API Key | notion.so/my-integrations → create integration → copy Secret |
| Notion Transactions DB ID | 32 chars from URL of transactions database |
| Notion Categories DB ID | 32 chars from URL of categories database |
| Ngrok Auth Token | dashboard.ngrok.com → Your Authtoken (optional but needed for webhook) |

---

## Notion database structure required

**Transactions database** needs these properties:
- `Name` — Title
- `Date` — Date
- `Amount` — Number
- `Notes` — Rich text
- `Categories` — Relation (points to Categories database)

**Categories database** needs only:
- `Name` — Title (each page = one category)

Both databases must have the Notion integration connected: open the database → click `···` → `Connections` → add your integration.

---

## Common errors and fixes

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'telegram'` | venv not activated. Run `source venv/Scripts/activate` first |
| `TELEGRAM_BOT_TOKEN не задан` | Edit .env and add the token |
| Bot doesn't respond | Check that `python main.py` is still running in the terminal |
| Webhook not receiving | Check NGROK_AUTH_TOKEN, restart bot |
| Notion errors | Make sure integration is connected to BOTH databases |

---

## Tech notes for Claude Code

- Python-telegram-bot v21 (async, PTB v20+ API)
- All navigation uses `ReplyKeyboardMarkup` (bottom buttons), no InlineKeyboard
- 3 ConversationHandlers: settings, add/create-template, templates browser
- Templates stored locally in `templates.json` via `TemplateManager` in `config_manager.py`
- Monobank webhook → FastAPI server + ngrok tunnel (daemon thread)
- Amount convention: internally negative=expense, positive=income; displayed flipped to user
- `TELEGRAM_CHAT_ID` auto-set on first `/start`

# 🤖 Monobank Financist Bot

> A Telegram bot that automatically catches your Monobank card transactions, lets you tag them with a category, and saves everything to a personal Notion database — with monthly and yearly PDF spending reports built in.

---

## 📋 What you'll need

Before starting, make sure you have:

- A computer running **Windows**, **macOS**, or **Linux**
- A stable **internet connection**
- A **Telegram** account ([telegram.org](https://telegram.org))
- A **Monobank** account (the Ukrainian bank app)
- A **Notion** account — free tier is enough ([notion.so](https://www.notion.so))
- An **ngrok** account — free tier is enough, needed to receive real-time card alerts ([ngrok.com](https://ngrok.com))

---

## ✨ What the bot does

Once running, the bot will:

- **Catch every card transaction** the moment you pay — Monobank sends it instantly via webhook
- **Ask you to pick a category** (Food, Transport, Fun, etc.) for each transaction in Telegram
- **Save the tagged transaction** to your Notion database automatically
- **Track your monthly budget** per category and warn you when you're close to the limit
- **Remember your last choice** for each merchant so you can confirm with one tap next time
- **Let you log cash expenses** manually through the bot
- **Save reusable templates** for things you pay often (bus ticket, coffee, etc.)
- **Generate PDF reports** — monthly or yearly — with charts, category breakdowns, top transactions, and spending anomalies
- **Send a monthly summary** automatically on the 1st of each month

---

## 🛠️ Step-by-step installation

### Step 1 — Install Python

Python is the programming language this bot is written in. You need version **3.10 or newer**.

**Download:** Go to [python.org/downloads](https://www.python.org/downloads/) and click the big yellow **Download Python** button.

**Windows — important:** During installation, check the box that says **"Add Python to PATH"** before clicking Install. Without this, nothing will work.

**macOS:** The installer works like any other `.pkg` file. Follow the prompts.

**Linux:** Python is usually pre-installed. If not: `sudo apt install python3 python3-pip` (Ubuntu/Debian).

**Verify the installation** by opening a terminal and running:

```bash
python --version
```

You should see something like `Python 3.12.3`. If you see `3.10` or higher, you're good.

> **What is a terminal?**
> - **Windows:** Press `Win + R`, type `cmd`, press Enter. Or search "Command Prompt" in the Start menu.
> - **macOS:** Press `Cmd + Space`, type `Terminal`, press Enter.
> - **Linux:** Press `Ctrl + Alt + T`.

---

### Step 2 — Download the project

**Option A — Download as ZIP (easiest, no Git needed):**

1. Go to the GitHub page for this project
2. Click the green **Code** button near the top right
3. Click **Download ZIP**
4. Extract (unzip) the downloaded file to a folder you'll remember, for example `C:\bots\monobank-bot` or `~/bots/monobank-bot`

**Option B — Clone with Git (if you have Git installed):**

```bash
git clone https://github.com/YOUR_USERNAME/monobank-finance-bot.git
cd monobank-finance-bot
```

---

### Step 3 — Open a terminal inside the project folder

You need to run all following commands from inside the project folder.

**Windows:**
1. Open File Explorer and navigate to the extracted folder
2. Click on the address bar at the top (it shows the folder path)
3. Type `cmd` and press Enter — a terminal opens in the right place

**macOS:**
1. Open Finder and navigate to the folder
2. Right-click the folder → **New Terminal at Folder**
   (If you don't see this option: System Preferences → Keyboard → Shortcuts → Services → enable "New Terminal at Folder")

**Linux:**
1. Navigate to the folder in your file manager
2. Right-click → **Open Terminal Here**

**Verify you're in the right place** by running:

```bash
ls
```

You should see files like `main.py`, `bot_handlers.py`, `requirements.txt`, etc.

---

### Step 4 — Create a virtual environment (recommended)

A virtual environment (venv) is a clean, isolated place to install the bot's libraries so they don't interfere with other Python programs on your computer.

**Create the environment:**

```bash
python -m venv venv
```

**Activate it:**

| System | Command |
|--------|---------|
| Windows (Command Prompt) | `venv\Scripts\activate.bat` |
| Windows (PowerShell) | `venv\Scripts\Activate.ps1` |
| Windows (Git Bash) | `source venv/Scripts/activate` |
| macOS / Linux | `source venv/bin/activate` |

After activation, your terminal prompt will show `(venv)` at the beginning. This tells you the environment is active.

> You need to activate the environment every time you open a new terminal to run the bot.

---

### Step 5 — Install dependencies

Dependencies are the libraries the bot needs to work. Install them all with one command:

```bash
pip install -r requirements.txt
```

This downloads and installs everything automatically. It may take 1–3 minutes.

> **Note:** The `faster-whisper` package (used for voice message transcription in feedback) downloads a ~150 MB AI model on first use. This is normal.

---

### Step 6 — Get your Telegram Bot Token

A bot token is a secret key that connects your copy of the bot to Telegram.

1. Open **Telegram** on your phone or desktop
2. Search for **@BotFather** (the official Telegram bot for creating bots — it has a blue checkmark)
3. Send: `/newbot`
4. BotFather asks for two things:
   - A **display name** — anything you like, e.g. `My Finance Bot`
   - A **username** — must end in `bot`, e.g. `myfinance_bot`
5. After you finish, BotFather sends a token that looks like:

   ```
   7412638501:AAFxyz123abcDEF456ghiJKL789mnoPQR000
   ```

6. Copy and save this token — you'll add it to the config file in Step 10.

---

### Step 7 — Get your Monobank API Token

The Monobank token lets the bot receive your transaction data.

1. Open the **Monobank** [API website](https://api.monobank.ua/index.html)
2. If you've never done this before, tap the button to generate a token
3. Copy the token — it's a long string of letters and numbers

> This token gives read-only access to your account info and transaction list. Never share it publicly.

---

### Step 8 — Set up Notion

Notion is where all your transactions will be stored. You need two databases: one for transactions and one for spending categories.

#### 8a — Create a Notion account

Go to [notion.so](https://www.notion.so) and sign up for free.

#### 8b — Create the Categories database

This database holds your spending categories (Food, Transport, Rent, etc.).

1. In Notion, click **"+ New page"** in the left sidebar
2. Give it a title like `Finance`
3. Inside, type `/table` and select **"Table — Full page"**
4. Name the table `Categories`
5. The table already has a "Name" column (Title type) — keep it, that's correct
6. Add a row for each category you want, for example:

   | Name |
   |------|
   | Groceries |
   | Transport |
   | Dining out |
   | Entertainment |
   | Health |
   | Utilities |

   You can add or change categories at any time later, including from inside the bot.

#### 8c — Create the Transactions database

1. Create another table (on the same Finance page or elsewhere), name it `Transactions`
2. You need to add the following columns — **the names must match exactly**:

| Column name | Column type | Notes |
|-------------|-------------|-------|
| `Name` | Title | Already exists by default |
| `Date` | Date | Click `+` → choose "Date" |
| `Amount` | Number | Click `+` → choose "Number" |
| `Notes` | Text | Click `+` → choose "Text" |
| `Categories` | Relation | Click `+` → "Relation" → select your Categories table |

> To add a column: click the `+` button at the far right of the column header row.

#### 8d — Create a Notion integration

The bot needs explicit permission to read and write your databases.

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **"+ New integration"**
3. Give it a name — anything works, e.g. `Finance Bot`
4. Choose the workspace where your databases are
5. Click **"Save"**
6. On the next page, find **"Internal Integration Secret"** — it looks like `secret_abc123...`
7. Click **"Show"** and copy the token — this is your `NOTION_API_KEY`

#### 8e — Connect the integration to both databases

Integrations don't automatically have access to your pages. You must grant access manually to each database.

1. Open your **Transactions** database in Notion (as a full page, not in the sidebar)
2. Click the **`···`** (three dots) button in the top-right corner
3. Click **"Connections"**
4. Find your integration by name and click to connect it
5. Do the same for the **Categories** database

#### 8f — Get the database IDs

1. Open your **Transactions** database in Notion as a full page
2. Look at the URL in your browser — it looks like:

   ```
   https://www.notion.so/YourName/a1b2c3d4e5f6789012345678901234ab?v=...
   ```

3. The **32 characters** between the last `/` and `?` is the database ID:

   ```
   a1b2c3d4e5f6789012345678901234ab
   ```

4. Copy it — this is `NOTION_TRANSACTIONS_DB_ID`
5. Repeat for the **Categories** database → `NOTION_CATEGORIES_DB_ID`

---

### Step 9 — Set up ngrok (for real-time transaction alerts)

ngrok creates a secure public URL pointing to your computer. Monobank uses this URL to send you transaction events the moment you pay.

> Without ngrok, you won't receive automatic notifications when you spend. You can still use the bot manually (log cash expenses, view stats, generate reports), but card transactions won't appear automatically.

1. Go to [ngrok.com](https://ngrok.com) and create a free account
2. After signing in, go to **"Your Authtoken"** in the left sidebar (or Dashboard → Getting Started → Your Authtoken)
3. Copy the token — this is `NGROK_AUTH_TOKEN`

**Optional — get a permanent (static) URL:**

By default, ngrok gives you a different URL each time you restart. With a static domain, the URL stays the same forever so you only register with Monobank once.

1. In the ngrok dashboard, go to **"Domains"** in the left sidebar
2. Click **"New Domain"** — you get one free static domain
3. It looks like `your-chosen-name.ngrok-free.app`
4. Copy it — this is `NGROK_DOMAIN`

---

### Step 10 — Configure the bot

Now put all your tokens into the configuration file.

1. In the project folder, find `.env.example`
2. Make a copy of it named `.env`:

   **Windows (Command Prompt):**
   ```
   copy .env.example user_data\.env
   ```

   **macOS / Linux:**
   ```bash
   cp .env.example user_data/.env
   ```

   > The `.env` file lives inside the `user_data/` folder. This folder is excluded from Git, so your secrets are never accidentally uploaded to GitHub.

3. Open `user_data/.env` in any text editor (Notepad on Windows is fine)

4. Fill in your values. Here's a complete example with fake tokens:

   ```env
   # Your Telegram bot token from BotFather
   TELEGRAM_BOT_TOKEN=7412638501:AAFxyz123abcDEF456ghiJKL789mnoPQR012

   # Leave blank — filled in automatically when you first /start
   TELEGRAM_CHAT_ID=

   # Your Monobank developer token
   MONOBANK_TOKEN=uXhJa8K3mN9pQrZsT2vWdYeF5gB1cL7o

   # Leave blank — you'll pick your account inside the bot
   MONOBANK_ACCOUNT_ID=

   # Your Notion integration secret
   NOTION_API_KEY=secret_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6

   # Your Notion database IDs (32 characters each)
   NOTION_TRANSACTIONS_DB_ID=a1b2c3d4e5f6789012345678901234ab
   NOTION_CATEGORIES_DB_ID=b2c3d4e5f6789012345678901234ab12

   # Your ngrok auth token
   NGROK_AUTH_TOKEN=2abc123DEF456ghi789JKL012mno345PQR

   # Your ngrok static domain (without https://)
   # Leave blank if you didn't create one
   NGROK_DOMAIN=your-chosen-name.ngrok-free.app

   # Port for the local webhook server — 8080 works fine
   WEBHOOK_PORT=8080
   ```

5. Save the file.

> **Important:** Never add spaces around `=`. Write `TOKEN=abc`, not `TOKEN = abc`.

---

### Step 11 — Run the bot

Make sure your virtual environment is active (you see `(venv)` in your terminal), then run:

```bash
python main.py
```

A successful start looks like this:

```
──────────────────────────────────────────────────
 Monobank Finance Bot v1.5.x
 Статус: ⚠️ requires setup (/config)
 Режим: 🔇 Silent
──────────────────────────────────────────────────
Ngrok tunnel: https://your-chosen-name.ngrok-free.app → localhost:8080
Monobank webhook registered: https://your-chosen-name.ngrok-free.app/webhook
```

To stop the bot at any time, press **Ctrl + C** in the terminal.

> **Keep the terminal window open.** The bot runs as long as the terminal is open. Closing the terminal stops the bot.

---

### Step 12 — First-time setup inside Telegram

1. Open Telegram and search for the bot by the username you gave it in Step 6
2. Send `/start`
3. The bot replies with a welcome message and saves your Chat ID automatically

Now finish the configuration inside the bot:

1. Tap **⚙️ Settings** → **⚙️ Configuration**
2. Any field showing ❌ needs to be filled in — tap it and follow the prompt
3. Tap **"📋 Choose Monobank account"** — the bot fetches your cards and lets you pick one

**Test it:** Make a small purchase with your Monobank card. Within a few seconds, the bot should send you a message in Telegram showing the transaction amount and asking you to pick a category.

---

## ❓ Common problems & fixes

**`ModuleNotFoundError: No module named 'telegram'` (or any other module)**

The virtual environment is not active or dependencies were not installed.

```bash
# Activate venv (Windows):
venv\Scripts\activate.bat

# Activate venv (macOS/Linux):
source venv/bin/activate

# Then install:
pip install -r requirements.txt
```

---

**`TELEGRAM_BOT_TOKEN не задан` — bot doesn't start**

The `.env` file is missing or the token line is blank.
- Make sure you created `user_data/.env` (not just left `.env.example` as-is)
- Check for spaces: `TOKEN=abc` ✅ not `TOKEN = abc` ❌
- Make sure the token is on a single line with no line breaks

---

**Bot doesn't respond in Telegram**

- The terminal with `python main.py` must stay open and running
- Try sending `/start` again
- Make sure you're messaging the correct bot username

---

**Card transactions don't appear automatically**

ngrok isn't working or the webhook wasn't registered.

- Check that `NGROK_AUTH_TOKEN` is correctly set in `user_data/.env`
- Restart the bot and look for the line: `Ngrok tunnel: https://...`
- If you see `NGROK_AUTH_TOKEN не задан`, the token is missing
- If you recently created the ngrok account, try logging out and back in to the ngrok dashboard to find the correct token

---

**`python` command not found on Windows**

Python is not on the system PATH.

- **Best fix:** Uninstall Python from "Add or Remove Programs", re-download from [python.org](https://www.python.org), and this time check **"Add Python to PATH"** during installation
- **Quick fix:** Try `python3` instead of `python`

---

**Notion: transactions not saving**

- Make sure the integration is connected to **both** databases (each database needs the connection added separately — see Step 8e)
- Column names must match exactly: `Name`, `Date`, `Amount`, `Notes`, `Categories`
- The `Categories` column must be a Relation type pointing to the Categories database, not a plain text column
- Verify the database IDs are 32 characters with no extra dashes or spaces

---

**The bot says it's already configured but transactions still don't save**

- Try sending `/config` and check each field shows ✅
- Use the **"📋 Choose Monobank account"** button to make sure an account is selected — a missing Account ID is a common cause

---

## 📁 Project structure

```
monobank-finance-bot/
│
├── main.py               Entry point — starts the bot and all background services
├── bot_handlers.py       All Telegram commands and conversation flows
├── monobank_service.py   Webhook server that receives card transaction events
├── notion_service.py     Reads and writes to your Notion databases
├── config_manager.py     Reads settings from .env and manages templates
├── pending_store.py      Holds transactions waiting for you to pick a category
├── smart_categories.py   Remembers your last category pick per merchant
├── limit_store.py        Tracks budget limits and sends warnings
├── voice_handler.py      Transcribes voice messages using Whisper AI
│
├── .env.example          Template — copy to user_data/.env and fill in your tokens
├── requirements.txt      Python libraries the bot depends on
│
└── user_data/            All personal data lives here — never uploaded to GitHub
    ├── .env              Your actual tokens and settings (gitignored)
    ├── pending_transactions.json
    ├── smart_categories.json
    ├── feedbacks.json
    └── limit_notifications.json
```

---

## 💬 Bot commands

| Command | What it does |
|---------|-------------|
| `/start` | Opens the main menu |
| `/config` | Opens settings — configure tokens, pick Monobank account, set mode |
| `/add` | Manually log a cash expense or any transaction |
| `/create_template` | Save a reusable transaction template (e.g. monthly bus pass) |
| `/stats` | Show a spending summary for the current month |
| `/report` | Generate a PDF report — choose monthly or yearly |
| `/feedback` | Send a message or bug report to the developer |
| `/cancel` | Cancel whatever you're currently doing |
| `/restart` | Restart the bot (useful after changing settings) |
| `/update` | Pull the latest code from GitHub and restart automatically |

---

## 🏗️ Notion database reference

**Transactions database — required columns:**

| Property name | Type |
|--------------|------|
| `Name` | Title (exists by default) |
| `Date` | Date |
| `Amount` | Number |
| `Notes` | Text |
| `Categories` | Relation → Categories database |

**Categories database — required columns:**

| Property name | Type |
|--------------|------|
| `Name` | Title (exists by default) |

**Optional: budget tracking columns for Categories database**

If you want the bot to track how much budget is left per category and warn you when you're close, add:

| Property name | Type | Purpose |
|--------------|------|---------|
| `Remaining` | Number or Formula | Money left this month in this category |
| `Limit` | Number | Monthly spending cap for this category |

Then tell the bot about these column names in **⚙️ Settings → Configuration → "Notion column name with remaining amount"** and **"Notion column name with monthly limit"**.

---

## 🔒 Privacy

- Your tokens and financial data are stored **only on your own computer** (in `user_data/`) and in **your own Notion workspace** — nowhere else
- The only external services involved are Monobank (to register the webhook URL), Notion (to save transactions), and ngrok (to create the public tunnel)
- The `user_data/` folder is listed in `.gitignore`, so none of your personal data is ever accidentally committed or pushed to GitHub

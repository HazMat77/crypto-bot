╔════════════════════════════════════════════════════════════════╗
║               HazMat Crypto Bot — Windows Guide                ║
╚════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 1 — INSTALL THE BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Extract this zip to a folder on your PC (e.g. Desktop\HazMat Crypto Bot)
2. Double-click INSTALL.bat
   - It installs Python automatically if not already on your PC
   - It installs all required packages
   - It offers to add the bot to Windows startup (optional)
3. Done — no other software needed


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 2 — SET UP KUCOIN API KEYS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Log in to kucoin.com
2. Click your profile icon (top right) → API Management
3. Click "Create API"
4. Give it a name (e.g. "TraderBot")
5. IMPORTANT: Enable TRADE permission ONLY
   !! Do NOT enable Withdrawal permission !!
6. Set an API Passphrase (any password you choose — write it down)
7. Complete the verification (email/2FA)
8. Copy the three values:
     API Key
     API Secret
     Passphrase (the one you set in step 6)

9. Easiest: start the bot (see RUNNING THE BOT below), open the
   Config tab, pick KuCoin, paste in the three values, and click
   Save — no file editing needed.

   Or by hand: open config.py in Notepad and fill in:
     "api_key":    "paste your API key here"
     "api_secret": "paste your secret here"
     "passphrase": "paste your passphrase here"

!! SECURITY WARNING !!
Never share these keys in chat, email, or screenshots.
Anyone with your keys can trade on your account.
Keys with Trade-only permission CANNOT withdraw funds — keep it that way.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 3 — SET UP TELEGRAM NOTIFICATIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Open Telegram on your phone
2. Search for @BotFather and start a chat
3. Send: /newbot
4. Choose a name (e.g. "My Trader Bot")
5. Choose a username ending in "bot" (e.g. "mytraderbot_bot")
6. BotFather will send you a TOKEN — copy it

7. Now get your Chat ID:
   Search for @userinfobot in Telegram → start it
   It replies with your ID number — copy it

8. In config.py set:
     TELEGRAM_ENABLED = True
     TELEGRAM_TOKEN   = "paste your token here"
     TELEGRAM_CHAT_ID = "paste your ID here"


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 4 — SET UP AI (OPTIONAL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  NOTE: Paper trading uses FREE fake AI — no API key needed.
  Only get a real AI key when you switch to live trading.

  Once you have a key (either provider, see below): start the bot,
  open the Config tab -> Bot Settings, pick the provider from the
  dropdown, paste the key into the matching field, and click Save —
  no file editing needed. Or set AI_ENABLED/AI_PROVIDER in config.py
  and AI_API_KEY/GROK_API_KEY in bot_secrets.py by hand instead.

  ── OPTION A: Claude (Anthropic) ──────────────────────

  1. Go to console.anthropic.com
  2. Sign up for an account
  3. Click "API Keys" → "Create Key"
  4. Copy the key

  Cost: ~$5 free credit on signup, then ~$0.10-0.30/day
  Best for: nuanced market reasoning, news analysis

  ── OPTION B: Grok (xAI by Elon Musk) ────────────────

  1. Go to console.x.ai
  2. Sign up / log in with X (Twitter) account
  3. Click "API Keys" → create a key
  4. Copy the key

  Cost: Free tier available, paid plans from $25/month
  Best for: fast responses, real-time awareness


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 5 — OTHER EXCHANGE API KEYS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Each exchange follows the same pattern:
  Login → Account/Profile → API Management → Create Key
  Enable TRADE only, never Withdrawal.

  BINANCE:  binance.com → Account → API Management
  KRAKEN:   kraken.com → Security → API → Generate New Key
  BYBIT:    bybit.com → Account & Security → API Management
  OKX:      okx.com → User Centre → API → Create API Key
  GATE.IO:  gate.io → Account → API Keys
  MEXC:     mexc.com → Account → API Management

  Then either use the GUI's Config tab -> Exchange API Keys (pick the
  exchange, paste in the key/secret, check Enabled, Save), or in
  config.py find the exchange, set "enabled": True and paste your key
  and secret by hand.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  RUNNING THE BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Double-click START_BOT.bat (or the desktop shortcut, if you created
  one in INSTALL.bat) — this opens the GUI dashboard directly. From
  there, no Notepad required:

    - Config tab -> Exchange API Keys: pick an exchange, paste in your
      key/secret/passphrase, Save
    - Config tab -> Bot Settings: AI on/off + provider + API key,
      Telegram on/off, Watchdog auto-restart, paper starting pool size
    - Top bar -> Start: choose Paper (always start here) or Live (only
      after testing), confirm, and it launches the bot AND the
      watchdog together
    - Top bar -> Pause / Resume at any time
    - Top bar -> Updates: checks GitHub for a newer version and pulls
      it on request — your API keys are never touched by an update

  You can still edit config.py / bot_secrets.py by hand in Notepad
  instead, any time, if you prefer — nothing about the GUI requires it
  or gets in the way of it.

  Prefer the old console menu (direct paper/live, web dashboard,
  backtest)? Run:  START_BOT.bat cli

  Logs are saved in the logs/ folder either way.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FILES IN THIS FOLDER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  INSTALL.bat        One-time setup (run first)
  START_BOT.bat      Opens the GUI (double-click each time). "START_BOT.bat
                     cli" for the old console menu instead.
  config.py          All settings — editable via the GUI's Config tab,
                     or by hand with Notepad
  bot.py             Main bot engine
  exchanges.py       Exchange connectors
  ai_analyst.py      Claude/Grok AI integration
  fake_ai.py         Free AI simulation for paper trading
  coin_discovery.py  Auto-discovers tradeable coins
  price_feed.py      Live prices from CoinGecko (reduces API load)
  deposit_monitor.py Auto-detects deposits + monthly BTC/BCH/XRP conversion
  logs/              Created automatically — daily log files


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚠️  RISK DISCLAIMER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Crypto trading carries significant financial risk.
  This bot is provided for educational and personal use only.
  It is NOT licensed for sale or resale.
  Never trade with money you cannot afford to lose.
  Past performance does not guarantee future results.
  The author takes no responsibility for financial losses.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BACKTESTING & OPTIMIZATION (do before going live)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Open a terminal in the bot folder and run:

  Basic backtest (90 days of BTC data):
    python backtest.py --symbol BTC-USDT --days 90

  Test all top coins (60 days):
    python backtest.py --all-coins --days 60

  Test with chart (requires matplotlib):
    python backtest.py --symbol ETH-USDT --days 180 --plot

  Find best RSI/MA settings automatically:
    python backtest.py --symbol BTC-USDT --optimize --days 180

  Optimize all coins and get recommendations:
    python backtest.py --all-coins --optimize --days 180

  Use Binance data via CCXT:
    python backtest.py --symbol BTC-USDT --source ccxt --ccxt-exchange binance

  Test with offline CSV file:
    python backtest.py --symbol BTC-USDT --source csv --csv-path btc_data.csv

  Save data for offline use:
    python backtest.py --symbol BTC-USDT --save-data

The optimizer uses walk-forward validation (70% train / 30% test)
to avoid over-fitting — it only recommends settings that work on
data the optimizer never saw during training.

After running --optimize, copy the recommended settings into config.py.

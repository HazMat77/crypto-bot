╔════════════════════════════════════════════════════════════════╗
║                CryptoTradingBot — Windows Guide                ║
╚════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 1 — INSTALL THE BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Extract this zip to a folder on your PC (e.g. Desktop\CryptoTradingBot)
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

9. Open config.py in Notepad and fill in:
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

  ── OPTION A: Claude (Anthropic) ──────────────────────

  1. Go to console.anthropic.com
  2. Sign up for an account
  3. Click "API Keys" → "Create Key"
  4. Copy the key
  5. In config.py:
       AI_ENABLED   = True
       AI_PROVIDER  = "claude"
       AI_API_KEY   = "paste your key here"

  Cost: ~$5 free credit on signup, then ~$0.10-0.30/day
  Best for: nuanced market reasoning, news analysis

  ── OPTION B: Grok (xAI by Elon Musk) ────────────────

  1. Go to console.x.ai
  2. Sign up / log in with X (Twitter) account
  3. Click "API Keys" → create a key
  4. Copy the key
  5. In config.py:
       AI_ENABLED   = True
       AI_PROVIDER  = "grok"
       GROK_API_KEY = "paste your key here"

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

  Then in config.py find the exchange, set "enabled": True
  and paste your key and secret.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  RUNNING THE BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Double-click START_BOT.bat
  Choose:  [1] Paper trading  ← always start here
           [2] Live trading   ← only after testing
           [3] Exit

  The bot window shows live activity.
  Logs are saved in the logs/ folder.
  Press CTRL+C to stop.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FILES IN THIS FOLDER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  INSTALL.bat        One-time setup (run first)
  START_BOT.bat      Launch the bot (double-click each time)
  config.py          All settings — edit with Notepad
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

╔════════════════════════════════════════════════════════════════╗
║         CryptoTradingBot — Telegram Command Reference          ║
╚════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO SET UP YOUR TELEGRAM BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 1 — Create your bot:
  1. Open Telegram → search @BotFather
  2. Send: /newbot
  3. Choose a name:     e.g. "My Crypto Trader"
  4. Choose a username: e.g. "mycryptotrader_bot" (must end in 'bot')
  5. BotFather sends you a TOKEN — copy it

Step 2 — Get your Chat ID:
  1. Search @userinfobot in Telegram
  2. Start it — it replies with your numeric ID
  3. Copy that number

Step 3 — Configure the bot:
  Open config.py in Notepad and set:
    TELEGRAM_ENABLED  = True
    TELEGRAM_TOKEN    = "7123456789:AAFxxxxxx"   ← your token
    TELEGRAM_CHAT_ID  = "123456789"              ← your chat ID

Step 4 — Start the trading bot:
  Double-click START_BOT.bat → choose Paper or Live
  Your Telegram will receive: "Bot command handler ready! Send /help"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  COMPLETE COMMAND REFERENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INFORMATION COMMANDS
────────────────────
/help
  Shows all available commands with brief descriptions.
  Use this if you forget any command.

/status
  Current snapshot of everything:
  • Pool balance (Normal 80% + Aggressive 20% + listing reserve)
  • Trading mode (Paper/Live)
  • Bot status (Active/Paused)
  • All open positions with entry price
  • Total trades and P&L since bot started

/trades
  Last 10 completed trades today showing:
  • Coin and exchange
  • Which pool it came from (Normal/Aggressive)
  • Buy price → Sell price
  • Gross P&L, fees, net P&L per trade
  • Summary totals at the bottom

/daily
  Full day report:
  • Total trades, wins, losses, win rate percentage
  • Gross P&L, total fees paid, net profit
  • Breakdown by coin (which made/lost most)

/monthly
  Full month summary:
  • Total trades for the month
  • Best single trade and worst single trade
  • Top 5 performing coins
  • Pool balances per exchange
  • Total P&L for the month

/coins
  List of all coins currently being traded:
  • Which exchange each is on
  • Whether it's in Normal or Aggressive pool
  • Status (HOLDING a position or watching for signals)
  • Number of active coins vs pool capacity

/news
  Latest headlines from all 10 sources:
  The Block, CoinDesk, Blockworks, Cointelegraph,
  Bloomberg Crypto, Forbes Crypto, Messari,
  CoinGecko trending, CoinMarketCap movers
  Useful for understanding why the bot made decisions

/score
  Live news sentiment score for each active coin:
  • Scale -5 (very bearish) to +5 (very bullish)
  • Visual bar chart
  • Shows which coins have positive vs negative coverage
  • Updates hourly automatically

/regime
  Current detected market regime:
  • BULL_TREND / BEAR_TREND / SIDEWAYS / VOLATILE
  • Confidence percentage
  • Which strategy mode is active (conservative/normal/aggressive)
  • Recent regime history

/engine
  Adaptive strategy engine status:
  • Per-coin win rate, expectancy, profit factor
  • Current TP/SL calibration for each coin
  • Kelly position sizes
  • Filters passing/failing

CONTROL COMMANDS
─────────────────
/pause
  Immediately stops all NEW buy orders.
  • Existing open positions continue to be monitored
  • Stop-loss and take-profit still active
  • Listing hunter paused
  • Use when you see bad market news
  • Resume with /resume

/resume
  Restarts buying after a pause.
  • Bot will look for signals on next poll cycle (90s)

/stop
  Gracefully stops the bot completely.
  • Waits for current cycle to complete
  • Sends final P&L summary to Telegram
  • Does NOT close open positions
  • Must restart bot manually after this

AUTOMATIC NOTIFICATIONS (no command needed)
─────────────────────────────────────────────
These fire automatically — you don't type anything:

  🚀 Bot started         — on launch, shows mode and settings
  🟢 BUY order           — when a position is opened
     Shows: coin, pool type, price, amount, TP/SL targets
  🔴 SELL order          — when a position is closed
     Shows: entry/exit price, gross P&L, fees, net P&L
  🛑 Stop-loss hit       — forced exit, shows loss amount
  ✅ Take-profit hit     — target reached, shows gain
  📉 Trailing stop       — trailing stop triggered, locks in profit
  ⏰ Max hold time       — forced exit after 48h/24h
  🤖 AI approved         — AI confirmed a signal (shows confidence + news)
  🤖 AI vetoed           — AI blocked a signal (shows why)
  ⚠️ Invalid pair        — coin not found on exchange, skipped
  ⛔ Trade skipped       — pool too low, shows balance needed
  ⚠️ Drawdown alert      — pool dropped 15%, buys paused
  ✅ Drawdown recovered  — pool recovered, buying resumed
  🆕 New listing found   — upcoming listing detected, shows buy time
  🚀 Listing bought      — new coin bought at listing time
  📈/📉 Listing exit     — listing position closed with P&L
  📅 Auto-convert        — 15th/30th BTC/BCH/XRP → USDT conversion
  💵 Deposit detected    — new USDT added to pool
  💓 Heartbeat           — every 30 minutes: pool + P&L summary
  🔄 Coin re-rank        — hourly: active coins updated by news scores
  📊 Scaling update      — pool crossed tier threshold, more coins added
  🧠 Monthly AI review   — 1st of month: strategy analysis + updates
  🌍 Regime change       — market shifted bull/bear/sideways detected
  🛑 Bot stopped         — final summary on shutdown

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  EXAMPLE CONVERSATIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You: /status
Bot: 📊 Bot Status
     ━━━━━━━━━━━━━━━━
     Mode:     📄 PAPER
     Status:   ✅ ACTIVE
     Pool:     $100.00 USDT
       Normal: $79.00  Aggr: $16.00  Reserve: $5.00
     Trades:   7 today
     📈 P&L:   +$0.32 USDT
     Open positions:
       • BTC [KUCOIN] (NORMAL) — bought @ $67,432

You: /pause
Bot: ⛔ Trading Paused
     No new buys will be placed.
     Existing positions monitored.
     Send /resume to restart.

You: /resume
Bot: ✅ Trading Resumed
     Bot is now looking for buy signals.

You: /score
Bot: 📊 News Sentiment Scores
     ━━━━━━━━━━━━━━━━
     ▲ BTC      +4.2  ▓▓▓▓
     ▲ ETH      +3.1  ▓▓▓
     ▲ SOL      +2.8  ▓▓
     — DOGE     -0.8
     ▼ ATOM     -3.2  ▓▓▓

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TIPS FOR BEST USE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Check /score before major news events
   The bot updates scores hourly, but you can check anytime
   to see if the market mood has shifted

2. Use /pause during major uncertainty
   Fed announcements, exchange hacks, regulatory news —
   pause the bot manually and resume when things settle

3. /daily before bed, /monthly on the 1st
   Good habit to track performance consistently

4. /regime tells you if the bot changed strategy
   The bot auto-adapts but you can see what mode it's in

5. /engine shows if the bot is learning
   After 20+ trades, the engine has enough data to
   calibrate TP/SL and position sizes optimally

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SECURITY NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• The bot ONLY responds to your specific Telegram Chat ID
• Commands from any other chat ID are silently ignored
• /stop requires you to physically restart the bot after
• Never share your Telegram bot token — it's like a password
• Never screenshot config.py with API keys visible

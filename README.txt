╔════════════════════════════════════════════════════════════════╗
║              CryptoTradingBot — RSI + MA Strategy              ║
║                       Quick Start Guide                        ║
╚════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FIRST TIME SETUP (do this once)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Double-click  ▶ INSTALL.bat
   (If Python isn't installed, it will tell you how to get it)

2. Open  config.py  in Notepad and look it over.
   - PAPER_TRADING = True means NO real money is used (safe!)
   - You can change the coin (SYMBOL) if you want


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  RUNNING THE BOT (every time)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Double-click  ▶ START_BOT.bat
2. A window opens showing what the bot is doing
3. To stop it, press  CTRL + C  or close the window


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW THE STRATEGY WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BUY signal fires when:
  ✔ RSI drops below 35 (coin looks oversold / cheap)
  ✔ Price is above the 20-period moving average (upward trend)

SELL signal fires when:
  ✔ RSI rises above 65 (coin looks overbought / expensive)
  ✔ Price is below the 20-period moving average (losing momentum)

The bot checks every 60 seconds using 15-minute candles.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  GOING LIVE WITH REAL MONEY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  Only do this after you've watched it run in paper mode
    and you're happy with how it behaves.

1. Log in to KuCoin → Account → API Management → Create API
   - Give it "Trade" permission only (NOT withdrawal)
   - Copy your API Key, Secret, and Passphrase

2. Open config.py and fill in:
     API_KEY        = "paste your key here"
     API_SECRET     = "paste your secret here"
     API_PASSPHRASE = "paste your passphrase here"

3. Change:
     PAPER_TRADING = False

4. Set how much USDT the bot can use:
     TRADE_USDT = 100.0   ← adjust to your comfort level

5. Save config.py and start the bot normally.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VIEWING LOGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Logs are saved in the  logs/  folder with today's date.
Open them with Notepad to see every decision the bot made.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚠️  RISK DISCLAIMER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Crypto trading carries significant financial risk.
This bot is provided for educational purposes.
Never trade with money you can't afford to lose.
Past performance of a strategy does not guarantee future results.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FILES IN THIS FOLDER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  INSTALL.bat   → Run once to set up Python packages
  START_BOT.bat → Double-click to start the bot
  config.py     → All settings (edit with Notepad)
  bot.py        → The bot itself (don't need to touch this)
  logs/         → Log files created automatically

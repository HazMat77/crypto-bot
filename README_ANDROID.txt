╔════════════════════════════════════════════════════════════════╗
║                CryptoTradingBot — Android Guide                ║
╚════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHAT YOU NEED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Android phone (Android 7.0 or newer)
  2. Termux — a Linux terminal for Android
     !! Get it from F-Droid ONLY — not Google Play !!
     The Google Play version is outdated and breaks Python.
     F-Droid link: https://f-droid.org/en/packages/com.termux/

  3. Optional: Termux:Widget (for home screen button)
     F-Droid link: https://f-droid.org/en/packages/com.termux.widget/


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 1 — INSTALL TERMUX
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Install F-Droid from f-droid.org (it's an app store)
  2. Open F-Droid → search "Termux" → install
  3. Open Termux
  4. Run this command to allow storage access:
       termux-setup-storage
     (Tap "Allow" when Android asks for permission)


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 2 — COPY BOT FILES TO YOUR PHONE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Method A — USB cable:
    1. Connect phone to PC via USB
    2. On phone: select "File Transfer" mode
    3. Copy the entire kucoin_bot folder to your phone's
       internal storage (e.g. Phone/Documents/kucoin_bot)

  Method B — Email/cloud:
    1. Zip the kucoin_bot folder on your PC
    2. Email it to yourself or upload to Google Drive
    3. Download and extract on your phone

  Method C — Direct in Termux:
    In Termux, run:
      mkdir ~/kucoin_bot
    Then type each file manually (advanced users only)


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 3 — RUN THE INSTALL SCRIPT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  In Termux, navigate to where you copied the files:
    cd /sdcard/Documents/kucoin_bot
    (adjust path to wherever you copied the folder)

  Then run:
    bash INSTALL_ANDROID.sh

  This will:
    - Update Termux packages
    - Install Python 3
    - Install all bot dependencies
    - Create a start script
    - Optionally add a home screen widget


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 4 — EDIT CONFIG.PY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  In Termux:
    nano ~/kucoin_bot/config.py

  Use the arrow keys to navigate, edit your API keys,
  then press CTRL+X → Y → Enter to save.

  For full setup instructions (API keys, Telegram, AI)
  see README_WINDOWS.txt — the steps are identical,
  just done in Termux instead of Notepad.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 5 — RUN THE BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  In Termux:
    cd ~/kucoin_bot
    bash START_BOT.sh

  Choose Paper (1) or Live (2) trading.

  !! IMPORTANT — Keep bot running with screen off !!
  Before starting the bot, run:
    termux-wake-lock
  This prevents Android from killing Termux when idle.
  Run termux-wake-unlock when you want to stop it.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOME SCREEN WIDGET (one-tap launch)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Install Termux:Widget from F-Droid
  2. Long-press your home screen
  3. Tap Widgets → scroll to find Termux Widget
  4. Drag it to your home screen
  5. You'll see "CryptoTradingBot" as a shortcut — tap to launch


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  KEEPING THE BOT RUNNING 24/7
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Android aggressively kills background apps to save battery.
  To prevent this:

  1. Run termux-wake-lock (as above)

  2. Go to Android Settings → Apps → Termux
     → Battery → set to "Unrestricted" or "Don't optimise"

  3. On Samsung: Settings → Battery → Background usage limits
     → Make sure Termux is NOT in the sleeping apps list

  4. Keep your phone plugged in if possible for 24/7 operation

  For truly reliable 24/7 operation a Windows PC is more
  stable than a phone — phones can still be killed by
  Android's memory manager under heavy load.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TROUBLESHOOTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  "pkg: command not found"
    → Termux not installed properly. Reinstall from F-Droid.

  "Permission denied" on files
    → Run: chmod +x START_BOT.sh

  "Python not found"
    → Run: pkg install python

  "ModuleNotFoundError"
    → Run: pip install python-kucoin pandas requests

  Bot stops when screen turns off
    → Run termux-wake-lock before starting the bot
    → Disable battery optimisation for Termux in settings

  Can't find bot files in Termux
    → Files on internal storage are at /sdcard/
    → Run: ls /sdcard/ to see your folders

╔════════════════════════════════════════════════════════════════╗
║               HazMat Crypto Bot — Android Guide                ║
╚════════════════════════════════════════════════════════════════╝

Android runs the bot through Termux (a real Linux terminal app) plus
the same web dashboard used on desktop, opened in your phone's
browser. There's no separate "Android app" to install from a store —
Termux + one command is the whole install.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHAT YOU NEED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Android phone (Android 7.0 or newer)
  2. Termux — a Linux terminal for Android
     !! Get it from F-Droid ONLY — not Google Play !!
     The Google Play version is outdated and breaks Python.
     F-Droid link: https://f-droid.org/en/packages/com.termux/

  3. Recommended — Termux:Widget (one-tap home screen launcher)
     F-Droid link: https://f-droid.org/en/packages/com.termux.widget/

  4. Optional — Termux:API (lets the launcher auto-open your browser
     and enables Android notifications)
     F-Droid link: https://f-droid.org/en/packages/com.termux.api/


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 1 — INSTALL TERMUX
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Install F-Droid from f-droid.org (it's an app store)
  2. Open F-Droid -> search "Termux" -> install
  3. Open Termux
  4. Run this command to allow storage access:
       termux-setup-storage
     (Tap "Allow" when Android asks for permission)


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 2 — ONE COMMAND SETS UP EVERYTHING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  No file copying, no USB cable, no zip files. In Termux, paste:

    curl -sSL https://raw.githubusercontent.com/HazMat77/crypto-bot/main/INSTALL_ANDROID.sh | bash

  This pulls the entire bot straight from GitHub and:
    - Installs Python 3 and git (via Termux's own package manager)
    - Clones the bot into ~/crypto-bot
    - Installs all Python dependencies automatically
    - Creates a one-tap launcher + Termux:Widget shortcut

  This is also the only thing you ever need to send someone else to
  get them set up — this one script, not the whole project folder.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STEP 3 — START THE BOT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  In Termux:
    cd ~/crypto-bot && bash START_GUI_ANDROID.sh

  This opens the dashboard in your phone's browser at
  http://127.0.0.1:8501 — everything happens there, no config.py
  editing required:

    - Config tab -> pick your exchange, paste in your API keys, Save
    - Config tab -> Bot Settings: AI on/off, Telegram on/off, paper
      starting pool size
    - Sidebar -> pick Paper or Live, click "Start Bot + Watchdog"
    - Sidebar -> Pause / Resume at any time
    - Sidebar -> "Check for Updates" pulls the latest version from
      GitHub — your API keys are gitignored and never touched by
      an update, so there's nothing to re-enter afterward

  Android only gets the GUI on purpose — there's no command-line
  paper/live menu here, everything is one tap/click away instead.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOME SCREEN WIDGET (one-tap launch)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Install Termux:Widget from F-Droid (if you haven't already)
  2. Long-press your home screen
  3. Tap Widgets -> scroll to find Termux Widget
  4. Drag it to your home screen
  5. You'll see "HazMat Crypto Bot" as a shortcut — tap it to launch
     Termux, start the dashboard, and open it in your browser


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  KEEPING THE BOT RUNNING 24/7
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Android aggressively kills background apps to save battery.
  To prevent this:

  1. Run termux-wake-lock before starting the bot
     (run termux-wake-unlock when you want to stop it)

  2. Go to Android Settings -> Apps -> Termux
     -> Battery -> set to "Unrestricted" or "Don't optimise"

  3. On Samsung: Settings -> Battery -> Background usage limits
     -> Make sure Termux is NOT in the sleeping apps list

  4. Keep your phone plugged in if possible for 24/7 operation

  For truly reliable 24/7 operation a Windows/Linux PC is more
  stable than a phone — phones can still be killed by Android's
  memory manager under heavy load.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TROUBLESHOOTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  "pkg: command not found"
    -> Termux not installed properly. Reinstall from F-Droid.

  "curl: command not found" when pasting the setup command
    -> Run: pkg install curl    then try the setup command again

  Browser doesn't open automatically after START_GUI_ANDROID.sh
    -> Install "Termux:API" from F-Droid, then: pkg install termux-api
    -> Or just open the URL manually: http://127.0.0.1:8501

  "ModuleNotFoundError"
    -> cd ~/crypto-bot && pip install -r requirements.txt

  Bot stops when screen turns off
    -> Run termux-wake-lock before starting the bot
    -> Disable battery optimisation for Termux in settings

  Want to reset and start over
    -> rm -rf ~/crypto-bot   then re-run the Step 2 setup command
       (this deletes bot_secrets.py too — you'll need to re-enter
       your API keys in the dashboard afterward)

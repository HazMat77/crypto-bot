╔════════════════════════════════════════════════════════════════╗
║             HazMat Crypto Bot — RSI + MA Strategy              ║
║                    Linux Quick Start Guide                     ║
╚════════════════════════════════════════════════════════════════╝

Works on Debian/Ubuntu, Fedora/RHEL, Arch, and openSUSE.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FIRST TIME SETUP (do this once)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Open a terminal in this folder and run:
     chmod +x INSTALL_LINUX.sh
     ./INSTALL_LINUX.sh

   This installs Python 3, pip, tkinter (needed for the GUI
   dashboard), and all the bot's dependencies automatically.
   It will ask for your password if it needs sudo to install
   system packages.

2. Open config.py in a text editor (nano, vim, gedit, VS Code...):
     nano config.py
   - PAPER_TRADING = True means NO real money is used (safe!)
   - Fill in your KuCoin API key, secret, and passphrase
   - Fill in your Telegram token + chat ID (optional)


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  RUNNING THE BOT (every time)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Run:  ./START_BOT_LINUX.sh
2. Choose a mode from the menu (paper / live / GUI / web
   dashboard / backtest)
3. To stop it, press CTRL+C or close the terminal


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WATCHDOG (optional, recommended)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Run this in a SEPARATE terminal alongside the bot:
     ./WATCHDOG_LINUX.sh

It checks every 60 seconds that the bot is still alive and
alerts you on Telegram independently if it freezes or crashes.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  RUNNING ON LOGIN (optional)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INSTALL_LINUX.sh can set up a systemd user service for you so
the bot starts automatically when you log in (defaults to
paper mode for safety). If you skipped that during install,
or want to manage it manually:

  Start now:        systemctl --user start kucoin-bot
  Stop:              systemctl --user stop kucoin-bot
  Check status:      systemctl --user status kucoin-bot
  View live logs:    journalctl --user -u kucoin-bot -f
  Enable on login:   systemctl --user enable kucoin-bot
  Disable on login:  systemctl --user disable kucoin-bot

Edit ~/.config/systemd/user/kucoin-bot.service and change
"--mode paper" to "--mode live" only once you're ready to
trade with real funds, then run:
     systemctl --user daemon-reload
     systemctl --user restart kucoin-bot


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TROUBLESHOOTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"externally-managed-environment" error during pip install:
  This is normal on newer Debian/Ubuntu (PEP 668). The
  install scripts already handle this automatically by
  retrying with --break-system-packages or falling back to
  a virtual environment in ./venv — you shouldn't need to do
  anything, but if you're installing something manually,
  add --break-system-packages to the pip command.

GUI Dashboard won't open / "No module named tkinter":
  tkinter is a system package, not a pip package, on Linux.
  Install it manually:
    Debian/Ubuntu : sudo apt-get install python3-tk
    Fedora/RHEL   : sudo dnf install python3-tkinter
    Arch          : sudo pacman -S tk
  Everything else (paper/live trading, web dashboard,
  backtest) works fine without it.

"Permission denied" running a .sh file:
  Run: chmod +x INSTALL_LINUX.sh START_BOT_LINUX.sh WATCHDOG_LINUX.sh


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SEE ALSO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

README.txt           — strategy overview, general concepts
README_TELEGRAM.txt   — Telegram bot setup + full command list

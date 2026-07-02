#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
#  HazMat Crypto Bot — Linux Setup Script
#
#  Installs Python (if missing), system packages needed for the GUI/dashboard,
#  and all Python dependencies. Works on Debian/Ubuntu, Fedora/RHEL, and Arch.
#
#  Run with:
#    chmod +x INSTALL_LINUX.sh
#    ./INSTALL_LINUX.sh
# ══════════════════════════════════════════════════════════════════════════════

set -uo pipefail

echo ""
echo "  ============================================="
echo "   HazMat Crypto Bot — Linux Full Automatic Setup"
echo "  ============================================="
echo ""
echo "  This will install everything needed to run the bot."
echo ""

# ── Check for internet connection ──────────────────────────────────────────
echo "  Checking internet connection..."
if ! ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 && ! ping -c 1 -W 3 google.com >/dev/null 2>&1; then
    echo "  ERROR: No internet connection detected."
    echo "  Please connect to the internet and try again."
    exit 1
fi
echo "  OK."
echo ""

# ── Detect package manager / distro family ─────────────────────────────────
PKG_MANAGER=""
if command -v apt-get >/dev/null 2>&1; then
    PKG_MANAGER="apt"
elif command -v dnf >/dev/null 2>&1; then
    PKG_MANAGER="dnf"
elif command -v yum >/dev/null 2>&1; then
    PKG_MANAGER="yum"
elif command -v pacman >/dev/null 2>&1; then
    PKG_MANAGER="pacman"
elif command -v zypper >/dev/null 2>&1; then
    PKG_MANAGER="zypper"
fi

if [ -z "$PKG_MANAGER" ]; then
    echo "  WARNING: Could not detect a supported package manager"
    echo "  (apt/dnf/yum/pacman/zypper). You may need to install Python 3,"
    echo "  pip, and python3-tk manually for your distro, then re-run this"
    echo "  script — it will pick up from the dependency install step."
fi
echo "  Detected package manager: ${PKG_MANAGER:-none}"
echo ""

# ── Determine if we need sudo ───────────────────────────────────────────────
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo "  WARNING: Not running as root and 'sudo' not found."
        echo "  System package installs below may fail — install Python 3,"
        echo "  pip, and tk manually if so."
    fi
fi

# ── Install Python 3, pip, venv, and tkinter (system packages) ─────────────
# tkinter is required for gui_dashboard.py and is NOT installable via pip on
# Linux — it has to come from the distro's package manager.
echo "  Installing Python 3, pip, and tkinter (system packages)..."
case "$PKG_MANAGER" in
    apt)
        $SUDO apt-get update -y
        $SUDO apt-get install -y python3 python3-pip python3-venv python3-tk
        ;;
    dnf)
        $SUDO dnf install -y python3 python3-pip python3-tkinter
        ;;
    yum)
        $SUDO yum install -y python3 python3-pip python3-tkinter
        ;;
    pacman)
        $SUDO pacman -Sy --noconfirm python python-pip tk
        ;;
    zypper)
        $SUDO zypper install -y python3 python3-pip python3-tk
        ;;
    *)
        echo "  Skipping system package install (no supported package manager detected)."
        ;;
esac
echo ""

# ── Locate Python ────────────────────────────────────────────────────────────
PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if [ -z "$PYTHON_CMD" ]; then
    echo "  ERROR: Python still not found after install attempt."
    echo "  Please install Python 3.9+ manually for your distro and re-run this script."
    exit 1
fi

echo "  Python found: $($PYTHON_CMD --version)"
PYTHON_EXE="$($PYTHON_CMD -c 'import sys; print(sys.executable)')"
echo "  Python executable: $PYTHON_EXE"
echo ""

# ── Confirm tkinter actually works (covers distros where the package name
#    above didn't quite match, e.g. minimal/container images) ──────────────
if ! $PYTHON_CMD -c "import tkinter" >/dev/null 2>&1; then
    echo "  NOTE: tkinter is not available — gui_dashboard.py (option [3] in"
    echo "  START_BOT_LINUX.sh) will not run, but everything else"
    echo "  (paper/live trading, web dashboard, backtest) works fine without it."
    echo "  To fix later, install your distro's tkinter package, e.g.:"
    echo "    Debian/Ubuntu : sudo apt-get install python3-tk"
    echo "    Fedora/RHEL   : sudo dnf install python3-tkinter"
    echo "    Arch          : sudo pacman -S tk"
    echo ""
fi

# ── Install Python dependencies ─────────────────────────────────────────────
# Installed straight from requirements.txt (core + optional) so any package
# this project adds in the future is picked up automatically — no need to
# keep this script's package list in sync by hand.
echo "  Installing bot dependencies from requirements.txt..."
echo ""

$PYTHON_CMD -m pip install --upgrade pip --quiet
$PYTHON_CMD -m pip install -r requirements.txt --upgrade --quiet

# If pip refuses due to an externally-managed environment (PEP 668, common on
# newer Debian/Ubuntu), retry with --break-system-packages or fall back to
# a virtualenv so the install still succeeds.
if ! $PYTHON_CMD -c "import kucoin; import pandas; import requests; import websocket" >/dev/null 2>&1; then
    echo "  Initial install didn't take — retrying with --break-system-packages..."
    $PYTHON_CMD -m pip install -r requirements.txt --upgrade --quiet --break-system-packages 2>/dev/null

    if ! $PYTHON_CMD -c "import kucoin; import pandas; import requests; import websocket" >/dev/null 2>&1; then
        echo "  Still failing — creating a virtual environment instead (./venv)..."
        $PYTHON_CMD -m venv venv
        # shellcheck disable=SC1091
        source venv/bin/activate
        PYTHON_CMD="$(pwd)/venv/bin/python"
        $PYTHON_CMD -m pip install --upgrade pip --quiet
        $PYTHON_CMD -m pip install -r requirements.txt --upgrade --quiet
    fi
fi
echo "  (optional packages installed where available)"
echo ""

# ── Verify core packages ─────────────────────────────────────────────────────
if ! $PYTHON_CMD -c "import kucoin; import pandas; import requests; import websocket" >/dev/null 2>&1; then
    echo "  ERROR: Package installation failed."
    echo "  Try running this script with sudo, or install manually:"
    echo "    $PYTHON_CMD -m pip install --user -r requirements.txt"
    exit 1
fi

echo "  All dependencies installed successfully!"
echo ""

# ── Save the working python command for START_BOT_LINUX.sh / WATCHDOG to use ──
echo "$PYTHON_CMD" > python_path.txt

# ── Make launcher scripts executable ─────────────────────────────────────────
chmod +x START_BOT_LINUX.sh 2>/dev/null
chmod +x WATCHDOG_LINUX.sh 2>/dev/null

# ── Optional: run on login via systemd user service ─────────────────────────
echo "  ── Optional: Run bot automatically when you log in ──"
echo ""
read -r -p "  Set up auto-start on login via systemd? (y/N): " AUTOSTART

if [[ "$AUTOSTART" =~ ^[Yy]$ ]]; then
    BOT_DIR="$(pwd)"
    SERVICE_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SERVICE_DIR"
    cat > "$SERVICE_DIR/kucoin-bot.service" << EOF
[Unit]
Description=HazMat Crypto Bot (paper mode)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
ExecStart=$PYTHON_CMD $BOT_DIR/bot.py --mode paper
Restart=on-failure

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null
    systemctl --user enable kucoin-bot.service 2>/dev/null
    echo "  Created and enabled kucoin-bot.service (paper mode, starts on login)."
    echo "  NOTE: defaults to paper mode for safety — edit"
    echo "  $SERVICE_DIR/kucoin-bot.service and change --mode paper to"
    echo "  --mode live only once you're ready, then run:"
    echo "    systemctl --user daemon-reload && systemctl --user restart kucoin-bot"
    echo "  Manage it with: systemctl --user [start|stop|status] kucoin-bot"
else
    echo "  Skipped."
fi
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────
echo "  ============================================="
echo "   Setup complete! Everything is ready."
echo "  ============================================="
echo ""
echo "  NEXT STEPS:"
echo "    1. Edit config.py with your editor of choice (e.g. nano config.py)"
echo "    2. Fill in your KuCoin API key, secret, and passphrase"
echo "    3. Fill in your Telegram token + chat ID (optional)"
echo "    4. BACKTEST first: $PYTHON_CMD backtest.py --symbol BTC-USDT --days 90"
echo "    5. Run ./START_BOT_LINUX.sh to launch the bot"
echo ""
echo "  BACKTEST EXAMPLES:"
echo "    $PYTHON_CMD backtest.py --symbol BTC-USDT --days 90"
echo "    $PYTHON_CMD backtest.py --symbol ETH-USDT --days 180 --rsi-buy 35 --rsi-sell 65"
echo "    $PYTHON_CMD backtest.py --all-coins --days 60 --plot"
echo ""
echo "  Remember to regenerate your KuCoin API keys after testing!"
echo ""

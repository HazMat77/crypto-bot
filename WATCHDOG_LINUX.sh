#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
#  CryptoTradingBot Watchdog — Linux Launcher
#
#  Runs SEPARATELY from the bot itself. Keep this running alongside the
#  bot. It checks every 60 seconds whether the bot is still alive and
#  responsive, and alerts you on Telegram (independently of the bot's
#  own alerts) if something has frozen or crashed.
#
#  Run: ./WATCHDOG_LINUX.sh
# ══════════════════════════════════════════════════════════════════════════════

cd "$(dirname "$0")" || exit 1

echo ""
echo "  ============================================="
echo "   Watchdog — Independent Health Monitor"
echo "  ============================================="
echo ""

PYTHON_CMD=""
if [ -f "python_path.txt" ]; then
    PYTHON_CMD="$(cat python_path.txt | tr -d '[:space:]')"
fi

if [ -z "$PYTHON_CMD" ] || ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD="python"
    fi
fi

if [ -z "$PYTHON_CMD" ]; then
    echo "  ERROR: Python not found. Run ./INSTALL_LINUX.sh first."
    exit 1
fi

$PYTHON_CMD watchdog.py

read -r -p "  Press Enter to close..." _

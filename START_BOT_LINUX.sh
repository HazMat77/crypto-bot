#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
#  HazMat Crypto Bot — Linux Launcher
#  Mirrors START_BOT.bat's menu. Run: ./START_BOT_LINUX.sh
# ══════════════════════════════════════════════════════════════════════════════

cd "$(dirname "$0")" || exit 1

echo ""
echo "  ============================================="
echo "   HazMat Crypto Bot — RSI + MA Strategy"
echo "  ============================================="
echo ""

# ── Find Python — use saved path from install, or auto-detect ──────────────
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
    echo "  ERROR: Python not found."
    echo "  Please run ./INSTALL_LINUX.sh first."
    exit 1
fi

# ── Check dependencies ───────────────────────────────────────────────────────
if ! $PYTHON_CMD -c "import kucoin, pandas, numpy, requests, bs4, websocket" >/dev/null 2>&1; then
    echo "  Dependencies not found. Installing now from requirements.txt..."
    echo ""
    $PYTHON_CMD -m pip install -r requirements.txt --quiet 2>/dev/null \
        || $PYTHON_CMD -m pip install -r requirements.txt --quiet --break-system-packages
    echo ""
fi

# ── Mode selection menu ──────────────────────────────────────────────────────
menu() {
    echo "  Which mode would you like to run?"
    echo ""
    echo "    [1]  Paper trading      (simulation - no real money)"
    echo "    [2]  Live trading       (uses real money on KuCoin)"
    echo "    [3]  GUI Dashboard      (desktop monitoring window)"
    echo "    [4]  Web Dashboard      (browser-based Streamlit)"
    echo "    [5]  Backtest           (test strategy on historical data)"
    echo "    [6]  Exit"
    echo ""
    read -r -p "  Enter 1-6: " CHOICE

    case "$CHOICE" in
        1)
            echo ""
            echo "  Starting in PAPER TRADING mode (simulation only)..."
            echo "  No real money will be used."
            echo "  Logs are saved in the \"logs\" folder."
            echo "  Press CTRL+C to stop."
            echo ""
            $PYTHON_CMD bot.py --mode paper
            ;;
        2)
            echo ""
            echo "  !! WARNING: LIVE TRADING MODE !!"
            echo "  Real money will be used on your KuCoin account."
            echo "  Make sure your API keys are set in config.py"
            echo ""
            read -r -p "  Type YES to confirm and start live trading: " CONFIRM
            if [ "$CONFIRM" != "YES" ]; then
                echo ""
                echo "  Cancelled. Returning to menu..."
                echo ""
                menu
                return
            fi
            echo ""
            echo "  Starting LIVE trading... Press CTRL+C to stop."
            echo "  Logs are saved in the \"logs\" folder."
            echo ""
            $PYTHON_CMD bot.py --mode live
            ;;
        3)
            echo ""
            echo "  Opening GUI Dashboard..."
            echo "  (Close the window to return here)"
            echo ""
            $PYTHON_CMD gui_dashboard.py
            ;;
        4)
            echo ""
            echo "  Opening dashboard in your browser..."
            echo "  Press CTRL+C to stop the dashboard server."
            echo ""
            $PYTHON_CMD -m streamlit run dashboard.py
            ;;
        5)
            echo ""
            echo "  Backtest options:"
            echo "    [1]  Quick backtest (BTC, 90 days)"
            echo "    [2]  All coins backtest (90 days)"
            echo "    [3]  Full optimize + Monte Carlo (BTC, 180 days)"
            echo "    [4]  Custom (opens a shell)"
            echo ""
            read -r -p "  Enter 1-4: " BTCHOICE
            case "$BTCHOICE" in
                1) $PYTHON_CMD backtest.py --symbol BTC-USDT --days 90 ;;
                2) $PYTHON_CMD backtest.py --all-coins --days 90 ;;
                3) $PYTHON_CMD backtest.py --symbol BTC-USDT --optimize --monte-carlo --days 180 ;;
                4) exec "$SHELL" ;;
                *) echo "  Invalid choice." ;;
            esac
            ;;
        6)
            echo "  Goodbye."
            exit 0
            ;;
        *)
            echo ""
            echo "  Invalid choice."
            echo ""
            menu
            return
            ;;
    esac
}

menu

echo ""
echo "  Bot has stopped."
read -r -p "  Press Enter to close..." _

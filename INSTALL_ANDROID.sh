#!/data/data/com.termux/files/usr/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  CryptoTradingBot — Android (Termux) Setup Script
#  
#  REQUIREMENTS:
#    1. Install Termux from F-Droid (NOT Google Play — that version is outdated)
#       https://f-droid.org/en/packages/com.termux/
#    2. Copy this file to your Termux home folder
#    3. Run: bash INSTALL_ANDROID.sh
# ══════════════════════════════════════════════════════════════════════════════

echo ""
echo "  ============================================="
echo "   CryptoTradingBot — Android (Termux) Setup"
echo "  ============================================="
echo ""

# ── Update Termux packages ─────────────────────────────────────────────────
echo "  Step 1/4: Updating Termux packages..."
pkg update -y && pkg upgrade -y

# ── Install Python and dependencies ───────────────────────────────────────
echo ""
echo "  Step 2/4: Installing Python..."
pkg install python -y
pkg install python-pip -y
pkg install git -y
pkg install termux-api -y   # for notifications

# ── Install Python packages ────────────────────────────────────────────────
echo ""
echo "  Step 3/4: Installing bot dependencies..."
pip install --upgrade pip
pip install "python-kucoin==2.1.3" pandas requests beautifulsoup4
pip install ccxt matplotlib 2>/dev/null || echo "(ccxt/matplotlib optional — skipped if unavailable)"

# ── Verify install ─────────────────────────────────────────────────────────
python -c "import kucoin; import pandas; import requests" 2>/dev/null
if [ $? -ne 0 ]; then
    echo ""
    echo "  ERROR: Package installation failed."
    echo "  Try running: termux-setup-storage"
    echo "  Then re-run this script."
    exit 1
fi

# ── Create start script ────────────────────────────────────────────────────
echo ""
echo "  Step 4/4: Creating launch scripts..."

# Create the bot directory shortcut
BOT_DIR="$HOME/kucoin_bot"
mkdir -p "$BOT_DIR"

# Create START_BOT.sh for Android
cat > "$BOT_DIR/START_BOT.sh" << 'STARTEOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/kucoin_bot"
echo ""
echo "  ============================================="
echo "   CryptoTradingBot — RSI + MA Strategy"
echo "  ============================================="
echo ""
echo "  Which mode would you like to run?"
echo ""
echo "    [1]  Paper trading  (simulation - no real money)"
echo "    [2]  Live trading   (uses real money on KuCoin)"
echo "    [3]  Exit"
echo ""
read -p "  Enter 1, 2, or 3: " CHOICE

case $CHOICE in
  1)
    echo ""
    echo "  Starting PAPER TRADING mode..."
    echo "  Press CTRL+C to stop."
    echo ""
    python bot.py --mode paper
    ;;
  2)
    echo ""
    echo "  !! WARNING: LIVE TRADING MODE !!"
    echo "  Real money will be used."
    echo ""
    read -p "  Type YES to confirm: " CONFIRM
    if [ "$CONFIRM" = "YES" ]; then
      echo ""
      echo "  Starting LIVE trading..."
      python bot.py --mode live
    else
      echo "  Cancelled."
      bash START_BOT.sh
    fi
    ;;
  3)
    echo "  Goodbye."
    exit 0
    ;;
  *)
    echo "  Invalid choice."
    bash START_BOT.sh
    ;;
esac
STARTEOF

chmod +x "$BOT_DIR/START_BOT.sh"

# ── Create Termux shortcut widget (optional) ───────────────────────────────
SHORTCUT_DIR="$HOME/.shortcuts"
mkdir -p "$SHORTCUT_DIR"
cat > "$SHORTCUT_DIR/CryptoTradingBot" << 'WIDGETEOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/kucoin_bot"
bash START_BOT.sh
WIDGETEOF
chmod +x "$SHORTCUT_DIR/CryptoTradingBot"

# ── Instructions ───────────────────────────────────────────────────────────
echo ""
echo "  ============================================="
echo "   Setup Complete!"
echo "  ============================================="
echo ""
echo "  NEXT STEPS:"
echo ""
echo "  1. Copy your bot files into:"
echo "     $BOT_DIR"
echo "     (Use: adb push or a file manager app)"
echo ""
echo "  2. Edit config.py with your API keys:"
echo "     nano $BOT_DIR/config.py"
echo ""
echo "  3. Start the bot:"
echo "     cd $HOME/kucoin_bot && bash START_BOT.sh"
echo ""
echo "  OPTIONAL — One-tap widget on home screen:"
echo "    Install 'Termux:Widget' from F-Droid"
echo "    Long-press home screen → Widgets → Termux Widget"
echo "    You'll see 'CryptoTradingBot' as a one-tap launcher"
echo ""
echo "  TIP: To keep bot running when screen is off:"
echo "    termux-wake-lock"
echo "    (run this before starting the bot)"
echo ""

#!/data/data/com.termux/files/usr/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  CryptoTradingBot — Android (Termux) Setup Script
#
#  This is the ONLY file you need to copy onto your phone by hand —
#  everything else (the whole bot) gets pulled straight from GitHub.
#
#  REQUIREMENTS:
#    1. Install Termux from F-Droid (NOT Google Play — that version is
#       outdated and breaks Python):
#         https://f-droid.org/en/packages/com.termux/
#    2. Optional but recommended — Termux:Widget, for a one-tap home
#       screen launcher:
#         https://f-droid.org/en/packages/com.termux.widget/
#
#  USAGE — paste this one line into Termux, no file copying required:
#    curl -sSL https://raw.githubusercontent.com/HazMat77/crypto-bot/main/INSTALL_ANDROID.sh | bash
#
#  (Or, if you already have this file on the phone: bash INSTALL_ANDROID.sh)
#
#  Android gets the GUI only — the web dashboard (dashboard.py), opened in
#  your phone's browser. There's no command-line paper/live menu here on
#  purpose: everything (mode, API keys, settings, Start/Pause/Resume,
#  updates) is one tap/click away in the dashboard itself.
# ══════════════════════════════════════════════════════════════════════════════

set -uo pipefail

REPO_URL="https://github.com/HazMat77/crypto-bot.git"
BOT_DIR="$HOME/crypto-bot"

echo ""
echo "  ============================================="
echo "   CryptoTradingBot — Android (Termux) Setup"
echo "  ============================================="
echo ""

# ── Step 1: Termux packages ─────────────────────────────────────────────────
echo "  Step 1/5: Updating Termux packages..."
pkg update -y && pkg upgrade -y
pkg install -y python git

# ── Step 2: Pull the bot from GitHub ─────────────────────────────────────────
echo ""
echo "  Step 2/5: Getting the bot from GitHub..."
if [ -d "$BOT_DIR/.git" ]; then
    echo "  Already cloned — pulling the latest version instead."
    git -C "$BOT_DIR" pull
else
    git clone "$REPO_URL" "$BOT_DIR"
fi
cd "$BOT_DIR" || { echo "  ERROR: could not enter $BOT_DIR"; exit 1; }

# ── Step 3: Python dependencies (core + web GUI, no desktop-only extras) ───
echo ""
echo "  Step 3/5: Installing dependencies (this can take a few minutes)..."
pip install --upgrade pip --quiet
# Everything in requirements.txt except customtkinter (desktop-GUI-only —
# CustomTkinter needs a real display server tkinter doesn't have on
# Termux, so Android uses the browser-based dashboard.py instead).
grep -v -i customtkinter requirements.txt > /tmp/requirements_android.txt
pip install -r /tmp/requirements_android.txt

if ! python -c "import kucoin, pandas, requests, websocket, streamlit" >/dev/null 2>&1; then
    echo ""
    echo "  ERROR: Package installation failed."
    echo "  Try: termux-setup-storage   then re-run this script."
    exit 1
fi

# ── Step 4: Launcher + Termux:Widget shortcut ────────────────────────────────
echo ""
echo "  Step 4/5: Creating the one-tap launcher..."

cat > "$BOT_DIR/START_GUI_ANDROID.sh" << 'STARTEOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/crypto-bot" || exit 1
echo "Starting CryptoTradingBot dashboard..."
echo "(Leave this Termux session running — closing it stops the dashboard.)"

# Launch the web GUI in the background, then open it in the phone's browser.
nohup python -m streamlit run dashboard.py --server.headless true \
    --server.port 8501 > "$HOME/crypto-bot/logs/streamlit.log" 2>&1 &
echo $! > "$HOME/.crypto-bot-streamlit.pid"

# Give the server a few seconds to come up, then open it.
sleep 5
if command -v termux-open-url >/dev/null 2>&1; then
    termux-open-url "http://127.0.0.1:8501"
else
    echo "Open this URL in your browser: http://127.0.0.1:8501"
    echo "(Install 'termux-api' + the Termux:API app from F-Droid to have "
    echo " this open automatically next time.)"
fi
STARTEOF
chmod +x "$BOT_DIR/START_GUI_ANDROID.sh"

SHORTCUT_DIR="$HOME/.shortcuts"
mkdir -p "$SHORTCUT_DIR"
cat > "$SHORTCUT_DIR/CryptoTradingBot" << 'WIDGETEOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/crypto-bot" || exit 1
bash START_GUI_ANDROID.sh
WIDGETEOF
chmod +x "$SHORTCUT_DIR/CryptoTradingBot"

# ── Step 5: optional termux-api for auto-opening the browser + notifications ─
echo ""
echo "  Step 5/5: Optional — termux-api (lets the launcher auto-open your "
echo "  browser and enables notifications)..."
pkg install -y termux-api 2>/dev/null || echo "  (skipped — install 'Termux:API' from F-Droid + 'pkg install termux-api' later if you want this)"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "  ============================================="
echo "   Setup Complete!"
echo "  ============================================="
echo ""
echo "  START THE BOT:"
echo "    cd $BOT_DIR && bash START_GUI_ANDROID.sh"
echo "  This opens the dashboard in your browser at http://127.0.0.1:8501"
echo "  — from there you can enter API keys, pick paper/live mode, and"
echo "  click Start (which launches the bot + watchdog together)."
echo ""
echo "  ONE-TAP HOME SCREEN LAUNCHER:"
echo "    1. Install 'Termux:Widget' from F-Droid (if you haven't already)"
echo "    2. Long-press your home screen -> Widgets -> Termux Widget"
echo "    3. Drag it to your home screen — tap 'CryptoTradingBot' to launch"
echo ""
echo "  KEEP IT RUNNING WITH THE SCREEN OFF:"
echo "    Run: termux-wake-lock"
echo "    Also: Android Settings -> Apps -> Termux -> Battery -> Unrestricted"
echo ""
echo "  UPDATES: open the dashboard and click 'Check for Updates' any time —"
echo "  your API keys (bot_secrets.py) are never touched by an update."
echo ""

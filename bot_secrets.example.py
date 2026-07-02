"""
Secrets Template
==================
Copy this file to bot_secrets.py and fill in your real credentials there.
bot_secrets.py is listed in .gitignore and will NEVER be committed — that's
the entire point of splitting these out of config.py.

    cp bot_secrets.example.py bot_secrets.py
    # then edit bot_secrets.py with your real keys

config.py imports from bot_secrets.py automatically. If bot_secrets.py
doesn't exist yet (e.g. right after a fresh git clone), config.py falls
back to the placeholder values below so the bot still starts up — it
just won't be able to authenticate with any exchange or Telegram until
you create bot_secrets.py for real.

NOTE: this is named "bot_secrets.py", not "secrets.py" — Python already
has a built-in stdlib module called `secrets`, and naming this file the
same thing would shadow it in a way that breaks the safe-fallback logic
in config.py silently. Don't rename it back to secrets.py.

NEVER paste real keys into this file (bot_secrets.example.py) — it IS
meant to be committed to git. Only bot_secrets.py is private.
"""

# ── Exchange API credentials ────────────────────────────────────────────────
# Keys should have TRADE permission only — never withdrawal.
KUCOIN_API_KEY      = "YOUR_KUCOIN_API_KEY"
KUCOIN_API_SECRET   = "YOUR_KUCOIN_API_SECRET"
KUCOIN_PASSPHRASE   = "YOUR_KUCOIN_PASSPHRASE"

BINANCE_API_KEY     = ""
BINANCE_API_SECRET  = ""

KRAKEN_API_KEY      = ""
KRAKEN_API_SECRET   = ""

# Kraken FUTURES is a separate product from Kraken spot, with its own API
# key system — generate these at https://futures.kraken.com/settings/api
# (your regular Kraken spot keys above will NOT work for futures calls).
# Only needed if you enable "futures_enabled" for kraken in config.py.
KRAKEN_FUTURES_API_KEY    = ""
KRAKEN_FUTURES_API_SECRET = ""

BYBIT_API_KEY       = ""
BYBIT_API_SECRET    = ""

OKX_API_KEY         = ""
OKX_API_SECRET      = ""
OKX_PASSPHRASE      = ""

GATEIO_API_KEY      = ""
GATEIO_API_SECRET   = ""

MEXC_API_KEY        = ""
MEXC_API_SECRET     = ""

WEBULL_API_KEY      = ""
WEBULL_API_SECRET   = ""

VIRGOCX_API_KEY     = ""
VIRGOCX_API_SECRET  = ""

# Coinbase Advanced Trade API — generate at https://www.coinbase.com/settings/api
# api_key    = API key NAME (format: organizations/{org_id}/apiKeys/{key_id})
# api_secret = full EC private key PEM block (-----BEGIN EC PRIVATE KEY----- ... -----END EC PRIVATE KEY-----)
#              Paste the entire multi-line block as a single string with \n newlines,
#              or use a triple-quoted string in bot_secrets.py.
COINBASE_API_KEY    = ""
COINBASE_API_SECRET = ""

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID  = "YOUR_TELEGRAM_CHAT_ID"

# ── AI providers ─────────────────────────────────────────────────────────────
AI_API_KEY    = "YOUR_ANTHROPIC_API_KEY_HERE"
GROK_API_KEY  = "YOUR_GROK_API_KEY_HERE"

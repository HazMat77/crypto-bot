"""
Settings Writer
=================
Shared, GUI-agnostic logic for writing bot settings from a UI without
touching a .py file by hand — used by both gui_dashboard.py (desktop,
Tkinter/CustomTkinter) and dashboard.py (browser, Streamlit — the GUI
used on Android via Termux) so the file-writing rules live in exactly
one place.

Three things get written here, and only these three:
  - Exchange API credentials -> bot_secrets.py (gitignored, never committed)
  - Which exchange is enabled for trading -> config.py's EXCHANGES dict
  - Simple scalar bot settings (AI on/off, paper pool size, etc.) -> config.py
"""

import re
from pathlib import Path

# ── Exchange credential fields ────────────────────────────────────────────
# (config.py EXCHANGES-dict field, bot_secrets.py variable name, GUI label)
EXCHANGE_DISPLAY = {
    "kucoin": "KuCoin", "binance": "Binance", "kraken": "Kraken", "bybit": "Bybit",
    "okx": "OKX", "gateio": "Gate.io", "mexc": "MEXC", "webull": "Webull",
    "virgocx": "VirgoCX", "coinbase": "Coinbase",
}
EXCHANGE_FIELDS = {
    "kucoin": [
        ("api_key", "KUCOIN_API_KEY", "API Key"),
        ("api_secret", "KUCOIN_API_SECRET", "API Secret"),
        ("passphrase", "KUCOIN_PASSPHRASE", "Passphrase"),
    ],
    "binance": [
        ("api_key", "BINANCE_API_KEY", "API Key"),
        ("api_secret", "BINANCE_API_SECRET", "API Secret"),
    ],
    "kraken": [
        ("api_key", "KRAKEN_API_KEY", "API Key"),
        ("api_secret", "KRAKEN_API_SECRET", "API Secret"),
        ("futures_api_key", "KRAKEN_FUTURES_API_KEY", "Futures API Key (optional)"),
        ("futures_api_secret", "KRAKEN_FUTURES_API_SECRET", "Futures API Secret (optional)"),
    ],
    "bybit": [
        ("api_key", "BYBIT_API_KEY", "API Key"),
        ("api_secret", "BYBIT_API_SECRET", "API Secret"),
    ],
    "okx": [
        ("api_key", "OKX_API_KEY", "API Key"),
        ("api_secret", "OKX_API_SECRET", "API Secret"),
        ("passphrase", "OKX_PASSPHRASE", "Passphrase"),
    ],
    "gateio": [
        ("api_key", "GATEIO_API_KEY", "API Key"),
        ("api_secret", "GATEIO_API_SECRET", "API Secret"),
    ],
    "mexc": [
        ("api_key", "MEXC_API_KEY", "API Key"),
        ("api_secret", "MEXC_API_SECRET", "API Secret"),
    ],
    "webull": [
        ("api_key", "WEBULL_API_KEY", "App Key"),
        ("api_secret", "WEBULL_API_SECRET", "App Secret"),
    ],
    "virgocx": [
        ("api_key", "VIRGOCX_API_KEY", "API Key"),
        ("api_secret", "VIRGOCX_API_SECRET", "API Secret"),
    ],
    "coinbase": [
        ("api_key", "COINBASE_API_KEY", "API Key Name"),
        ("api_secret", "COINBASE_API_SECRET", "EC Private Key (PEM)"),
    ],
}


def write_bot_secrets(values: dict) -> None:
    """Writes API key/secret values into bot_secrets.py, creating it from
    bot_secrets.example.py first if it doesn't exist yet (it's gitignored,
    so a fresh clone never has one). repr() is used for the replacement
    value rather than manual quoting so any embedded quotes/backslashes/
    newlines round-trip safely as valid Python."""
    secrets_path = Path("bot_secrets.py")
    if secrets_path.exists():
        content = secrets_path.read_text(encoding="utf-8")
    else:
        content = Path("bot_secrets.example.py").read_text(encoding="utf-8")

    for var_name, value in values.items():
        pattern = re.compile(r"^" + re.escape(var_name) + r"\s*=.*$", re.MULTILINE)
        replacement = f"{var_name} = {value!r}"
        if pattern.search(content):
            content = pattern.sub(replacement, content, count=1)
        else:
            content += f"\n{replacement}\n"

    secrets_path.write_text(content, encoding="utf-8")


def write_exchange_enabled(exchange_key: str, enabled: bool) -> None:
    """Flips the "enabled" flag inside that exchange's block in config.py's
    EXCHANGES dict. Scoped to just that block (matches the opening
    `"exchange_key": {` line, then the "enabled" line right after it) so
    it can't accidentally touch another exchange's flag."""
    config_path = Path("config.py")
    content = config_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'("' + re.escape(exchange_key) + r'"\s*:\s*\{\s*\n\s*"enabled"\s*:\s*)(True|False)')
    new_content, n = pattern.subn(lambda m: m.group(1) + str(bool(enabled)), content, count=1)
    if n == 0:
        raise ValueError(f"Could not find '{exchange_key}' in config.py's EXCHANGES dict")
    config_path.write_text(new_content, encoding="utf-8")


def write_config_values(updates: dict) -> None:
    """Regex-based line replacement for simple `KEY = value` top-level
    assignments in config.py — used for presets and everyday bot settings
    alike. Only touches the exact keys given; everything else in the
    file, including comments and the EXCHANGES dict, is left untouched."""
    config_path = Path("config.py")
    content = config_path.read_text(encoding="utf-8")
    for key, val in updates.items():
        content = re.sub(
            rf"^({re.escape(key)}\s*=\s*)(.+?)(\s*(?:#.*)?)$",
            rf"\g<1>{val}\g<3>",
            content, flags=re.MULTILINE)
    config_path.write_text(content, encoding="utf-8")

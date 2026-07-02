"""
Trade Ledger
=============
A durable, append-only record of every completed trade (spot AND
futures), independent of the bot's in-memory daily_trades/monthly_trades
lists — those reset on their own schedules (daily/monthly) and are lost
entirely on a restart, which makes them unusable as the source of truth
for anything that needs to span a full tax year or survive a crash.

Format: JSON Lines (one JSON object per line) in logs/trade_ledger.jsonl
— chosen over a single JSON array specifically so a crash mid-write can
never corrupt previously-recorded trades (each line is independently
parseable; a truncated last line just gets skipped on read, not the
whole file).

Used by tax_export.py to generate realized-gains reports, and can be
used for any other reporting that needs the FULL trade history rather
than just "today" or "this month".
"""

import json
import logging
import os
import threading
from datetime import datetime

log = logging.getLogger(__name__)

LEDGER_PATH = os.path.join("logs", "trade_ledger.jsonl")
_write_lock = threading.Lock()


def record_trade(trade: dict) -> None:
    """
    Appends one completed trade to the durable ledger. Expected keys
    (extra keys are preserved, missing ones just won't be in the CSV
    later): exchange, coin, side ("spot_long" | "futures_short"),
    entry_time, exit_time, buy_price, sell_price, qty, spent, proceeds,
    fees, pnl_gross, pnl_net, exit_reason.

    Never raises — a ledger write failure (disk full, permissions)
    should never be able to interrupt or crash the trading loop that's
    calling this right after a real sell/close.
    """
    try:
        os.makedirs("logs", exist_ok=True)
        record = dict(trade)
        record.setdefault("recorded_at", datetime.now().isoformat())
        line = json.dumps(record, default=str)
        with _write_lock:
            with open(LEDGER_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        log.error(f"[LEDGER] Failed to record trade (trade itself already completed "
                 f"normally — only the durable record failed): {e}")


def load_trades(start_date: str = None, end_date: str = None) -> list:
    """
    Reads the full ledger back, optionally filtered to an inclusive
    ["YYYY-MM-DD", "YYYY-MM-DD"] range on exit_time. Silently skips any
    unparseable line (e.g. a truncated final line from a crash mid-write)
    rather than failing the whole read.
    """
    if not os.path.exists(LEDGER_PATH):
        return []

    trades = []
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if start_date or end_date:
        def _in_range(t):
            exit_time = t.get("exit_time", "")
            exit_day  = exit_time[:10] if exit_time else ""
            if start_date and exit_day < start_date:
                return False
            if end_date and exit_day > end_date:
                return False
            return True
        trades = [t for t in trades if _in_range(t)]

    return trades

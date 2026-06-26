"""
Deposit Monitor & Auto-Convert
================================
Two features running as background threads:

1. DEPOSIT MONITOR (runs every 10 minutes)
   - Checks real KuCoin USDT balance
   - If balance increased since last check, adds difference to trading pool
   - Sends Telegram notification when new USDT detected

2. AUTO-CONVERT (runs on 15th and 30th of each month)
   - Sells 80% of BTC, BCH, XRP holdings to USDT
   - Adds proceeds to trading pool
   - Sends Telegram report of conversion
   - Works in paper mode too (simulates the conversion)
"""

import time
import logging
import threading
import requests
from datetime import datetime

import config

log = logging.getLogger(__name__)

# Coins to auto-convert on payout days
CONVERT_COINS  = ["BTC", "BCH", "XRP"]
CONVERT_PCT    = 0.80      # sell 80%, keep 20%
PAYOUT_DAYS    = [15, 30]  # day of month to trigger


def tg_send(message):
    if not config.TELEGRAM_ENABLED:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"[DEPOSIT MON] Telegram error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  DEPOSIT MONITOR
# ══════════════════════════════════════════════════════════════════════════════

class DepositMonitor:
    """
    Polls each exchange for USDT balance changes.
    When new USDT is detected (manual deposit or conversion proceeds),
    adds it to the trading pool.
    """

    def __init__(self, exchanges: dict, pool_usdt: dict, pool_locks: dict):
        self.exchanges   = exchanges
        self.pool_usdt   = pool_usdt
        self.pool_locks  = pool_locks
        # Track last known balance per exchange
        self.last_balance = {}

    def check_once(self):
        for ex_name, exchange in self.exchanges.items():
            try:
                if config.PAPER_TRADING:
                    # In paper mode, balance is internal — skip live check
                    continue

                live_bal = exchange.get_usdt_balance()
                last     = self.last_balance.get(ex_name)

                if last is None:
                    # First check — just record baseline
                    self.last_balance[ex_name] = live_bal
                    log.info(f"[DEPOSIT MON:{ex_name.upper()}] Baseline USDT balance: ${live_bal:.2f}")
                    continue

                diff = live_bal - last
                if diff >= 1.0:   # at least $1 new USDT to count as deposit
                    log.info(f"[DEPOSIT MON:{ex_name.upper()}] 🟢 New USDT detected: +${diff:.2f}")
                    with self.pool_locks[ex_name]:
                        self.pool_usdt[ex_name] += diff
                    self.last_balance[ex_name]   = live_bal

                    tg_send(
                        f"💵 <b>New USDT Deposit Detected!</b>\n━━━━━━━━━━━━━━━━\n"
                        f"Exchange:   <b>{ex_name.upper()}</b>\n"
                        f"Amount:     <b>+${diff:.2f} USDT</b>\n"
                        f"New pool:   <b>${self.pool_usdt[ex_name]:.2f} USDT</b>\n"
                        f"Status:     Added to trading pool ✅\n"
                        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                elif diff < -1.0:
                    # Balance dropped — could be withdrawal, update baseline
                    log.warning(f"[DEPOSIT MON:{ex_name.upper()}] Balance dropped by ${abs(diff):.2f} — updating baseline")
                    self.last_balance[ex_name] = live_bal

            except Exception as e:
                log.warning(f"[DEPOSIT MON:{ex_name.upper()}] Check failed: {e}")

    def run(self, stop_event: threading.Event):
        log.info("[DEPOSIT MON] Started — checking every 10 minutes")
        while not stop_event.wait(timeout=10 * 60):
            self.check_once()


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-CONVERT (15th and 30th)
# ══════════════════════════════════════════════════════════════════════════════

class AutoConverter:
    """
    On the 15th and 30th of each month, sells CONVERT_PCT of BTC/BCH/XRP
    holdings and adds proceeds to the USDT trading pool.
    """

    def __init__(self, exchanges: dict, pool_usdt: dict, pool_locks: dict):
        self.exchanges  = exchanges
        self.pool_usdt  = pool_usdt
        self.pool_locks = pool_locks
        self.last_run   = None   # date of last conversion

    def _should_run_today(self) -> bool:
        today = datetime.now()
        day   = today.day
        # Handle months with < 30 days — treat last day as 30th
        if day == 28 and today.month == 2:
            day = 30
        if day not in PAYOUT_DAYS:
            return False
        # Only run once per payout day
        today_date = today.date()
        if self.last_run == today_date:
            return False
        return True

    def convert_once(self):
        today = datetime.now().date()
        log.info(f"[AUTO-CONVERT] Running payout conversion for {today}")
        self.last_run = today

        for ex_name, exchange in self.exchanges.items():
            total_usdt_gained = 0.0
            conversion_lines  = ""

            for coin in CONVERT_COINS:
                try:
                    symbol = f"{coin}-USDT"

                    if config.PAPER_TRADING:
                        # Simulate a small mining balance for demo
                        sim_balance = {"BTC": 0.0005, "BCH": 0.01, "XRP": 5.0}.get(coin, 0)
                        balance     = sim_balance
                    else:
                        balance = exchange.get_coin_balance(coin)

                    if balance <= 0:
                        log.info(f"[AUTO-CONVERT:{ex_name.upper()}] {coin}: no balance")
                        continue

                    sell_qty  = round(balance * CONVERT_PCT, 8)
                    keep_qty  = round(balance - sell_qty, 8)

                    try:
                        price = exchange.get_price(symbol)
                    except Exception:
                        log.warning(f"[AUTO-CONVERT] Can't get price for {symbol} — skipping")
                        continue

                    usdt_received = round(sell_qty * price, 4)

                    if config.PAPER_TRADING:
                        log.info(f"[AUTO-CONVERT PAPER] {coin}: selling {sell_qty:.8f} @ ${price:.4f} = ${usdt_received:.2f} USDT")
                    else:
                        exchange.place_market_sell(symbol, sell_qty)
                        log.info(f"[AUTO-CONVERT LIVE] {coin}: sold {sell_qty:.8f} @ ${price:.4f} = ${usdt_received:.2f} USDT")

                    with self.pool_locks[ex_name]:
                        self.pool_usdt[ex_name] += usdt_received

                    total_usdt_gained += usdt_received
                    conversion_lines  += (
                        f"  • {coin}: sold {sell_qty:.6f} ({CONVERT_PCT*100:.0f}%) "
                        f"@ ${price:,.4f} = <b>${usdt_received:.2f}</b>\n"
                        f"    Kept: {keep_qty:.6f} {coin}\n"
                    )

                except Exception as e:
                    log.error(f"[AUTO-CONVERT:{ex_name.upper()}:{coin}] Error: {e}")
                    conversion_lines += f"  • {coin}: ❌ conversion failed — {e}\n"

            if total_usdt_gained > 0 or conversion_lines:
                mode_tag = "📄 PAPER" if config.PAPER_TRADING else "💰 LIVE"
                tg_send(
                    f"📅 <b>Monthly Auto-Convert — {today}</b>\n━━━━━━━━━━━━━━━━\n"
                    f"Exchange: <b>{ex_name.upper()}</b>  |  Mode: {mode_tag}\n\n"
                    f"Converted (80% of each):\n{conversion_lines}\n"
                    f"💵 Total gained:  <b>${total_usdt_gained:.2f} USDT</b>\n"
                    f"🏦 New pool:      <b>${self.pool_usdt[ex_name]:.2f} USDT</b>\n"
                    f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                log.info(f"[AUTO-CONVERT:{ex_name.upper()}] No balances to convert")

    def run(self, stop_event: threading.Event):
        log.info(f"[AUTO-CONVERT] Started — will convert on days {PAYOUT_DAYS}")
        while not stop_event.wait(timeout=60 * 60):   # check hourly
            if self._should_run_today():
                self.convert_once()

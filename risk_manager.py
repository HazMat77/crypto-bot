"""
Risk Manager
=============
Handles all risk controls for every open position:

  1. Stop-Loss         — sell if price drops X% below buy price
  2. Take-Profit       — sell if price rises X% above buy price
  3. Trailing Stop     — follow price up, sell if it drops X% from peak
  4. Max Hold Time     — sell if position held longer than X hours
  5. Drawdown Alert    — pause buying if pool drops X% from peak

All checks run on every poll cycle before signal analysis.
Any triggered exit takes priority over RSI+MA signals.
"""

import logging
import threading
import requests
from datetime import datetime, timedelta
from collections import defaultdict
import config

log = logging.getLogger(__name__)

# ── Per-position tracking ──────────────────────────────────────────────────
# Keyed by (exchange_name, symbol)
position_entry_time  = {}   # when position was opened
position_peak_price  = {}   # highest price seen since entry (for trailing stop)

# ── Pool peak tracking (for drawdown) ─────────────────────────────────────
pool_peak            = {}   # { exchange_name: peak_pool_value }
pool_paused          = {}   # { exchange_name: True/False }
pool_drawdown_level  = {}   # { exchange_name: "normal"|"caution"|"pause"|"emergency" }

# Thread lock
risk_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
#  POSITION ENTRY / EXIT TRACKING
# ══════════════════════════════════════════════════════════════════════════════

def on_buy(exchange_name: str, symbol: str, price: float):
    """Call this when a buy is placed — records entry time and price."""
    key = (exchange_name, symbol)
    with risk_lock:
        position_entry_time[key]  = datetime.now()
        position_peak_price[key]  = price
    log.info(f"[RISK] {exchange_name.upper()}:{symbol.split('-')[0]} entry recorded @ ${price:.6f}")


def on_sell(exchange_name: str, symbol: str):
    """Call this when a sell is placed — clears position tracking."""
    key = (exchange_name, symbol)
    with risk_lock:
        position_entry_time.pop(key, None)
        position_peak_price.pop(key, None)


def get_entry_time(exchange_name: str, symbol: str):
    """Read-only lookup of when a position was opened — used by
    tax_export.py (via trade_ledger.py) to record the acquisition date
    for cost-basis reporting. Must be called BEFORE on_sell() clears the
    entry for this position, or it'll return None. Returns a
    datetime.datetime, or None if no entry is tracked (e.g. a position
    the self-checker adopted after a crash, with no real entry time known)."""
    with risk_lock:
        return position_entry_time.get((exchange_name, symbol))


def update_peak(exchange_name: str, symbol: str, current_price: float):
    """Update the trailing stop peak price if current price is higher."""
    key = (exchange_name, symbol)
    with risk_lock:
        if key in position_peak_price:
            if current_price > position_peak_price[key]:
                position_peak_price[key] = current_price


# ══════════════════════════════════════════════════════════════════════════════
#  RISK CHECKS
# ══════════════════════════════════════════════════════════════════════════════

def check_position(exchange_name: str, symbol: str,
                   current_price: float, buy_price: float) -> tuple:
    """
    Three exit conditions, checked in this order, first to trigger wins:

      1. STOP-LOSS FROM PEAK — if current_price has dropped STOP_LOSS_PCT
         (capped at MAX_STOP_LOSS_PCT) below the HIGHEST price reached
         since entry, sell. This applies whether the position is currently
         up or down overall — e.g. if price ran from $100 to $115 then
         fell back to $110.40 (a 4% drop from the $115 peak), this fires
         even though the position is still +10.4% versus the original
         buy price. The stop is measured from the live peak, never from
         the original entry price.

      2. TAKE-PROFIT FROM ENTRY — if current_price is TAKE_PROFIT_PCT
         above buy_price (default 25%), sell and lock in the gain.

      3. MAX HOLD TIME — if the position has been open longer than
         MAX_HOLD_HOURS (default 48h), sell regardless of price.

    Returns (should_exit: bool, reason: str)
    """
    if not buy_price or buy_price <= 0:
        return False, ""

    key        = (exchange_name, symbol)
    coin       = symbol.split("-")[0]
    pct_change = (current_price - buy_price) / buy_price

    # Update the peak BEFORE checking the stop — the peak must reflect
    # the current price if this is itself a new high.
    update_peak(exchange_name, symbol, current_price)

    with risk_lock:
        peak = position_peak_price.get(key, buy_price)

    drop_from_peak = (current_price - peak) / peak if peak > 0 else 0

    # ── 1. Stop-loss measured from the PEAK, not from entry ─────────────────
    if getattr(config, "STOP_LOSS_ENABLED", False):
        sl_pct = getattr(config, "STOP_LOSS_PCT", 0.04)
        sl_cap = getattr(config, "MAX_STOP_LOSS_PCT", 0.04)
        sl_pct = min(sl_pct, sl_cap)   # never allow more loss than the hard ceiling

        if drop_from_peak <= -sl_pct:
            reason = (f"🛑 STOP-LOSS: {coin} dropped {abs(drop_from_peak)*100:.2f}% "
                     f"from peak ${peak:.6f} (limit -{sl_pct*100:.0f}%) — "
                     f"current vs entry: {pct_change*100:+.2f}%")
            log.warning(f"[RISK] {reason}")
            return True, reason

    # ── 2. Take-profit measured from ENTRY ──────────────────────────────────
    if getattr(config, "TAKE_PROFIT_ENABLED", False):
        tp_pct = getattr(config, "TAKE_PROFIT_PCT", 0.25)
        if pct_change >= tp_pct:
            reason = f"✅ TAKE-PROFIT: {coin} gained {pct_change*100:.2f}% (target +{tp_pct*100:.0f}%)"
            log.info(f"[RISK] {reason}")
            return True, reason

    # ── 3. Max hold time ─────────────────────────────────────────────────────
    max_hours = getattr(config, "MAX_HOLD_HOURS", 48)
    if max_hours > 0:
        with risk_lock:
            entry_time = position_entry_time.get(key)
        if entry_time:
            hold_hours = (datetime.now() - entry_time).total_seconds() / 3600
            if hold_hours >= max_hours:
                reason = (f"⏰ MAX HOLD: {coin} held {hold_hours:.1f}h "
                         f"(limit {max_hours}h) — forced exit at "
                         f"{pct_change*100:+.2f}% vs entry")
                log.warning(f"[RISK] {reason}")
                return True, reason

    return False, ""


def check_drawdown(exchange_name: str, current_pool: float) -> dict:
    """
    Tiered drawdown circuit breaker. Checks pool drawdown from its peak
    and returns escalating responses instead of a single binary pause:

      < 10%   : normal — full size, trading as usual
      10-15%  : CAUTION   — new buy sizes cut by 50%
      15-25%  : PAUSE      — no new buys, existing positions still managed
      >= 25%  : EMERGENCY — close all positions AND pause everything

    Returns:
        {
          "level":           "normal" | "caution" | "pause" | "emergency",
          "paused":          bool      (True = block new buys)
          "size_multiplier": float     (1.0 normal, 0.5 in caution, 0 if paused)
          "close_all":       bool      (True only in emergency)
          "drawdown_pct":    float
        }
    """
    tier1 = getattr(config, "DRAWDOWN_CAUTION_PCT",   0.10)
    tier2 = getattr(config, "DRAWDOWN_PAUSE_PCT",     0.15)
    tier3 = getattr(config, "DRAWDOWN_EMERGENCY_PCT", 0.25)

    with risk_lock:
        peak = pool_peak.get(exchange_name, current_pool)
        if current_pool > peak:
            pool_peak[exchange_name] = current_pool
            peak = current_pool
            prev_level = pool_drawdown_level.get(exchange_name, "normal")
            if prev_level != "normal":
                pool_drawdown_level[exchange_name] = "normal"
                log.info(f"[RISK] {exchange_name.upper()} pool recovered to new peak — resuming normal trading")
                _tg_drawdown_recovered(exchange_name, current_pool)

        drawdown = (peak - current_pool) / peak if peak > 0 else 0
        prev_level = pool_drawdown_level.get(exchange_name, "normal")

        if drawdown >= tier3:
            level, paused, mult, close_all = "emergency", True, 0.0, True
        elif drawdown >= tier2:
            level, paused, mult, close_all = "pause", True, 0.0, False
        elif drawdown >= tier1:
            level, paused, mult, close_all = "caution", False, 0.5, False
        else:
            level, paused, mult, close_all = "normal", False, 1.0, False

        if level != prev_level:
            pool_drawdown_level[exchange_name] = level
            log.warning(f"[RISK] {exchange_name.upper()} drawdown {drawdown*100:.1f}% — "
                       f"level changed {prev_level} → {level}")
            _tg_drawdown_tier_change(exchange_name, current_pool, peak, drawdown, level, prev_level)

        # Recovery between tiers (e.g. emergency -> pause -> caution -> normal)
        # without hitting a brand new peak — still worth notifying on the way down/up
        pool_paused[exchange_name] = paused

        return {
            "level":           level,
            "paused":          paused,
            "size_multiplier": mult,
            "close_all":       close_all,
            "drawdown_pct":    drawdown,
        }


def get_size_multiplier(exchange_name: str) -> float:
    """Quick accessor for place_buy — returns 1.0, 0.5, or 0.0 based on current drawdown tier."""
    with risk_lock:
        level = pool_drawdown_level.get(exchange_name, "normal")
    return {"normal": 1.0, "caution": 0.5, "pause": 0.0, "emergency": 0.0}.get(level, 1.0)


def is_paused(exchange_name: str) -> bool:
    """Returns True if new buys are paused due to drawdown."""
    return pool_paused.get(exchange_name, False)


# ══════════════════════════════════════════════════════════════════════════════
#  ATR-BASED POSITION SIZING
# ══════════════════════════════════════════════════════════════════════════════

def calc_atr(highs, lows, closes, period: int = 14) -> float:
    """Calculate Average True Range for volatility-based sizing."""
    try:
        import pandas as pd
        h = pd.Series(highs)
        l = pd.Series(lows)
        c = pd.Series(closes)
        prev_c = c.shift(1)
        tr = pd.concat([
            h - l,
            (h - prev_c).abs(),
            (l - prev_c).abs()
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0


def volatility_adjusted_size(base_size: float, current_price: float,
                              atr: float) -> float:
    """
    Reduce trade size when volatility is high.
    ATR as % of price = volatility measure.
    Normal vol (~2%): full size
    High vol (~5%+): reduce to 50-70% of base size
    """
    if not getattr(config, "VOLATILITY_SIZING", False) or atr <= 0 or current_price <= 0:
        return base_size

    vol_pct = atr / current_price   # ATR as fraction of price

    if vol_pct < 0.02:
        multiplier = 1.0       # normal — full size
    elif vol_pct < 0.04:
        multiplier = 0.80      # elevated — 80%
    elif vol_pct < 0.06:
        multiplier = 0.65      # high — 65%
    else:
        multiplier = 0.50      # extreme — 50%

    adjusted = round(base_size * multiplier, 4)
    if multiplier < 1.0:
        log.info(f"[RISK] Volatility {vol_pct*100:.1f}% → trade size reduced "
                 f"${base_size:.2f} → ${adjusted:.2f} ({multiplier*100:.0f}%)")
    return adjusted


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

def _tg(message: str):
    if not getattr(config, "TELEGRAM_ENABLED", False):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def tg_risk_exit(exchange_name: str, symbol: str, reason: str,
                 price: float, buy_price: float, pnl: float, mode: str):
    coin = symbol.split("-")[0]
    tag  = "📄 PAPER" if mode == "paper" else "💰 LIVE"
    sign = "+" if pnl >= 0 else ""
    _tg(
        f"{tag} <b>Risk Exit — {coin} [{exchange_name.upper()}]</b>\n━━━━━━━━━━━━━━━━\n"
        f"{reason}\n"
        f"Entry:  <b>${buy_price:,.6f}</b>\n"
        f"Exit:   <b>${price:,.6f}</b>\n"
        f"P&amp;L: <b>{sign}${pnl:.4f} USDT</b>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _tg_drawdown_tier_change(exchange_name: str, pool: float, peak: float,
                              drawdown: float, new_level: str, old_level: str):
    icons = {"normal": "✅", "caution": "🟡", "pause": "⚠️", "emergency": "🚨"}
    messages = {
        "caution":   "Position sizes cut to 50% — trading continues at reduced risk.",
        "pause":     "New buys PAUSED. Existing positions still monitored normally.",
        "emergency": "EMERGENCY — closing ALL open positions and pausing the bot entirely.",
        "normal":    "Drawdown cleared — full size trading resumed.",
    }
    _tg(
        f"{icons.get(new_level,'⚠️')} <b>Drawdown Level Change — {exchange_name.upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Pool peak:    <b>${peak:.2f} USDT</b>\n"
        f"Current pool: <b>${pool:.2f} USDT</b>\n"
        f"Drawdown:     <b>{drawdown*100:.1f}%</b>\n"
        f"Level:        <b>{old_level.upper()} → {new_level.upper()}</b>\n"
        f"{messages.get(new_level,'')}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _tg_drawdown_recovered(exchange_name: str, pool: float):
    _tg(
        f"✅ <b>Drawdown Recovered — {exchange_name.upper()}</b>\n━━━━━━━━━━━━━━━━\n"
        f"Pool:   <b>${pool:.2f} USDT</b>\n"
        f"Status: ✅ New peak reached — full size trading resumed\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

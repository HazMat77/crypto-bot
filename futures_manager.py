"""
Futures Manager
================
Adds SHORT exposure on top of the existing spot bot — the one thing spot
trading can never do. Deliberately narrow in scope:

  - Leverage is hard-capped at 1x everywhere (config.MAX_LEVERAGE). This
    is not a leveraged-sizing feature; it exists purely so the bot can
    profit from/hedge a downtrend the same way it already profits from
    an uptrend via spot. At 1x, a futures position's maximum loss is
    bounded the same way a spot position's is.

  - Entry re-uses the EXACT signal the bot already computes for exiting
    a spot position: RSI overbought + price below its moving average
    (see bot.py's `rsi_sell = rsi > params["rsi_sell"] and price < ma`).
    That condition already means "this coin looks like it's rolling
    over" — for a spot holder that's a reason to exit; for futures it's
    the mirror-image reason to open a short. No separate strategy engine
    was invented for this; it's the same read on the market, applied to
    a second product.

  - One short position per (exchange, symbol) at a time, sized as a
    fraction of the SAME pool spot trading uses (see
    config.FUTURES_MAX_PER_TRADE_PCT) — there is no separate futures
    pool to keep track of.

  - Fixed TP/SL/max-hold (config.FUTURES_TAKE_PROFIT_PCT /
    FUTURES_STOP_LOSS_PCT / FUTURES_MAX_HOLD_HOURS) rather than the
    adaptive Kelly/ATR calibration strategy_engine.py uses for spot —
    kept simple and predictable for a first cut of a new position type.

  - A funding-rate guard refuses to open a short into a strongly
    negative funding rate (shorts pay longs when funding is negative),
    since that can bleed a position even while directionally correct.

Call sequence expected from bot.py's coin_worker, once per poll cycle,
per symbol:

    manage_open_short(...)     # always — closes on TP/SL/max-hold/signal-flip
    if not holding_spot:
        evaluate_and_open_short(...)   # only considered when spot isn't
                                        # already long this coin
"""

import logging
import threading
import requests
from datetime import datetime

log = logging.getLogger(__name__)

# ── Per-(exchange, symbol) short position tracking ─────────────────────────
_lock              = threading.Lock()
_position_side     = {}   # {(ex_name, symbol): "short" | "none"}
_entry_price       = {}
_position_usdt     = {}   # notional at entry
_entry_time        = {}
_trough_price      = {}   # lowest price seen since entry — favourable direction for a short


def _tg(config, message: str):
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


def futures_enabled_for(ex_name: str, exchange, config) -> bool:
    """
    Defence in depth — three independent opt-ins must ALL be true before
    the bot will ever place a real futures order on an exchange:
      1. config.FUTURES_ENABLED         (global master switch)
      2. ex_name in FUTURES_SUPPORTED_EXCHANGES  (bot-level allowlist)
      3. config.EXCHANGES[ex_name]["futures_enabled"]  (per-exchange opt-in)
    Plus the adapter itself must report futures_supported() == True —
    Webull/VirgoCX/Coinbase never will, no matter how config is set.
    """
    if not getattr(config, "FUTURES_ENABLED", False):
        return False
    if ex_name not in getattr(config, "FUTURES_SUPPORTED_EXCHANGES", set()):
        return False
    ex_cfg = config.EXCHANGES.get(ex_name, {})
    if not ex_cfg.get("futures_enabled", False):
        return False
    try:
        if not exchange.futures_supported():
            return False
    except Exception:
        return False
    return True


def has_open_short(ex_name: str, symbol: str) -> bool:
    with _lock:
        return _position_side.get((ex_name, symbol)) == "short"


def evaluate_and_open_short(ex_name, exchange, symbol, price, rsi, ma, pool, mode, config) -> bool:
    """
    Mirrors bot.py's spot SELL signal (RSI overbought + price below MA)
    as a futures SHORT entry. Returns True if a short was opened.
    """
    if not futures_enabled_for(ex_name, exchange, config):
        return False
    if has_open_short(ex_name, symbol):
        return False   # already short this symbol — manage_open_short() handles exit

    tag = f"{ex_name.upper()}:{symbol.split('-')[0]}"

    # ── Funding-rate guard ──────────────────────────────────────────────
    try:
        funding_rate = exchange.get_funding_rate(symbol)
        max_adverse  = getattr(config, "FUTURES_MAX_ADVERSE_FUNDING_RATE", 0.001)
        if funding_rate < -max_adverse:
            log.info(f"[FUTURES:{tag}] ⛔ Skipping short — funding rate {funding_rate:.4%} "
                    f"too negative (shorts would pay longs)")
            return False
    except Exception as e:
        log.debug(f"[FUTURES:{tag}] Funding rate check failed (non-fatal): {e}")

    # ── Hybrid gate: is this short actually a better use of this capital
    # than just staking it? See hybrid_allocator.py. A no-op unless both
    # HYBRID_OPTIMIZER_ENABLED and STAKING_ENABLED are on.
    try:
        from hybrid_allocator import evaluate_hybrid_gate
        hybrid = evaluate_hybrid_gate(
            ex_name, exchange, symbol, "futures_short",
            getattr(config, "FUTURES_TAKE_PROFIT_PCT", 0.04),
            getattr(config, "FUTURES_STOP_LOSS_PCT", 0.04),
            getattr(config, "FUTURES_MAX_HOLD_HOURS", 24) or 24,
            mode, config,
        )
        if not hybrid["proceed"]:
            log.info(f"[FUTURES:{tag}] ⛔ Hybrid gate — staking beats this short's edge: {hybrid['reason']}")
            return False
    except Exception as e:
        log.debug(f"[FUTURES:{tag}] Hybrid gate check failed (non-fatal, proceeding): {e}")

    # ── Position sizing — same pool spot uses, capped separately ────────
    max_pct  = getattr(config, "FUTURES_MAX_PER_TRADE_PCT", 0.10)
    min_trade = getattr(config, "MIN_TRADE_USDT", 10.0)
    usdt_amount = round(pool * max_pct, 2)
    if usdt_amount < min_trade:
        log.debug(f"[FUTURES:{tag}] Position size ${usdt_amount:.2f} below minimum ${min_trade:.2f} — skipping")
        return False

    log.info(f"[FUTURES:{tag}] 🔻 Opening SHORT ${usdt_amount:.2f} @ ${price:.6f} "
            f"(RSI={rsi:.1f}, price<MA) — 1x, isolated")

    if not config.PAPER_TRADING:
        try:
            exchange.set_leverage(symbol, 1)
            exchange.open_futures_short(symbol, usdt_amount)
        except Exception as e:
            log.error(f"[FUTURES:{tag}] Open short failed: {e}")
            _tg(config, f"⚠️ <b>Futures short FAILED — {tag}</b>\n{e}")
            return False

    with _lock:
        key = (ex_name, symbol)
        _position_side[key] = "short"
        _entry_price[key]   = price
        _position_usdt[key] = usdt_amount
        _entry_time[key]    = datetime.now()
        _trough_price[key]  = price

    mode_tag = "📄 PAPER" if mode == "paper" else "💰 LIVE"
    _tg(config, f"{mode_tag} 🔻 <b>Futures SHORT opened — {tag}</b>\n"
               f"Entry: <b>${price:,.6f}</b>\n"
               f"Size:  <b>${usdt_amount:.2f}</b> (1x)\n"
               f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return True


def _check_exit(ex_name, symbol, price, config) -> tuple:
    """Returns (should_exit, reason) for an open short. A short profits
    as price FALLS, so its stop-loss/take-profit are the mirror image of
    the spot logic in risk_manager.py: the trailing reference is the
    LOWEST price seen (trough), and a stop fires on a bounce UP from
    that trough, not a drop."""
    key = (ex_name, symbol)
    with _lock:
        entry  = _entry_price.get(key)
        trough = _trough_price.get(key, entry)
        opened = _entry_time.get(key)
        if price < trough:
            _trough_price[key] = price
            trough = price

    if not entry:
        return False, ""

    coin = symbol.split("-")[0]
    pct_change_from_entry = (entry - price) / entry          # positive = profit for a short
    bounce_from_trough     = (price - trough) / trough if trough > 0 else 0

    sl_pct = getattr(config, "FUTURES_STOP_LOSS_PCT", 0.04)
    if bounce_from_trough >= sl_pct:
        return True, (f"🛑 FUTURES STOP-LOSS: {coin} bounced {bounce_from_trough*100:.2f}% "
                      f"from trough ${trough:.6f} (limit {sl_pct*100:.0f}%)")

    tp_pct = getattr(config, "FUTURES_TAKE_PROFIT_PCT", 0.04)
    if pct_change_from_entry >= tp_pct:
        return True, (f"✅ FUTURES TAKE-PROFIT: short gained {pct_change_from_entry*100:.2f}% "
                      f"(target +{tp_pct*100:.0f}%)")

    max_hours = getattr(config, "FUTURES_MAX_HOLD_HOURS", 24)
    if max_hours > 0 and opened:
        hold_hours = (datetime.now() - opened).total_seconds() / 3600
        if hold_hours >= max_hours:
            return True, (f"⏰ FUTURES MAX HOLD: {coin} short held {hold_hours:.1f}h "
                          f"(limit {max_hours}h)")

    return False, ""


def manage_open_short(ex_name, exchange, symbol, price, rsi, ma, mode, config) -> None:
    """
    Call every poll cycle regardless of whether a short is open — no-ops
    immediately if there isn't one. Closes on TP/SL/max-hold, or on a
    signal-flip (RSI no longer overbought / price back above MA, i.e.
    the original short thesis no longer holds).
    """
    if not has_open_short(ex_name, symbol):
        return

    tag = f"{ex_name.upper()}:{symbol.split('-')[0]}"
    should_exit, reason = _check_exit(ex_name, symbol, price, config)

    if not should_exit:
        # Signal-flip exit: the same condition that would justify a NEW
        # long entry on spot (oversold + price above MA) means the
        # downtrend thesis behind this short has reversed.
        params_rsi_buy = getattr(config, "RSI_BUY", 45)
        if rsi < params_rsi_buy and price > ma:
            should_exit, reason = True, f"📈 Signal flipped bullish (RSI={rsi:.1f}, price>MA) — closing short"

    if should_exit:
        _close_short(ex_name, exchange, symbol, price, mode, config, reason)


def _close_short(ex_name, exchange, symbol, price, mode, config, reason: str) -> None:
    key  = (ex_name, symbol)
    tag  = f"{ex_name.upper()}:{symbol.split('-')[0]}"

    with _lock:
        entry = _entry_price.get(key)
        usdt_amount = _position_usdt.get(key, 0.0)
        opened_at = _entry_time.get(key)

    if not entry:
        return

    if not config.PAPER_TRADING:
        try:
            exchange.close_futures_position(symbol)
        except Exception as e:
            log.error(f"[FUTURES:{tag}] Close short failed: {e} — position may still be open on exchange, "
                     f"check manually")
            _tg(config, f"⚠️ <b>Futures close FAILED — {tag}</b>\n{e}\n"
                       f"Position may still be open — check the exchange directly.")
            return

    pnl_pct = (entry - price) / entry if entry > 0 else 0.0
    pnl_usdt = round(usdt_amount * pnl_pct, 4)

    with _lock:
        _position_side.pop(key, None)
        _entry_price.pop(key, None)
        _position_usdt.pop(key, None)
        _entry_time.pop(key, None)
        _trough_price.pop(key, None)

    # Record into the same self-optimisation tracker spot uses, under a
    # "FUT:" prefixed symbol so short performance is tracked separately
    # from this coin's spot mean-reversion stats rather than polluting them.
    try:
        from strategy_engine import record_trade_outcome
        record_trade_outcome(ex_name, f"FUT:{symbol}", pnl_usdt, pnl_pct, "futures_short")
    except Exception:
        pass

    # Durable ledger entry (tax_export.py) — a closed futures short is a
    # realized, taxable event exactly like a spot sell.
    try:
        from trade_ledger import record_trade
        record_trade({
            "exchange": ex_name, "coin": symbol.split("-")[0], "symbol": symbol,
            "side": "futures_short",
            "entry_time": opened_at.isoformat() if opened_at else None,
            "exit_time": datetime.now().isoformat(),
            "buy_price": entry, "sell_price": price,
            "spent": usdt_amount, "proceeds": usdt_amount + pnl_usdt,
            "pnl_gross": pnl_usdt, "fees": 0.0, "pnl_net": pnl_usdt,
            "mode": mode, "exit_reason": reason,
        })
    except Exception:
        pass

    sign = "+" if pnl_usdt >= 0 else ""
    log.info(f"[FUTURES:{tag}] 🔺 SHORT CLOSED @ ${price:.6f} | Net {sign}${pnl_usdt:.4f} | {reason}")

    mode_tag = "📄 PAPER" if mode == "paper" else "💰 LIVE"
    _tg(config, f"{mode_tag} 🔺 <b>Futures SHORT closed — {tag}</b>\n"
               f"{reason}\n"
               f"Entry: <b>${entry:,.6f}</b>\n"
               f"Exit:  <b>${price:,.6f}</b>\n"
               f"P&amp;L: <b>{sign}${pnl_usdt:.4f} USDT</b> ({pnl_pct*100:+.2f}%)\n"
               f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def close_all_shorts(exchanges, mode, config) -> None:
    """Emergency stop — mirrors bot.py's _trigger_emergency_close_all for
    spot. Called from the same drawdown circuit breaker path."""
    with _lock:
        open_keys = [k for k, side in _position_side.items() if side == "short"]

    for ex_name, symbol in open_keys:
        exchange = exchanges.get(ex_name)
        if not exchange:
            continue
        try:
            price = exchange.get_futures_price(symbol)
        except Exception:
            price = _entry_price.get((ex_name, symbol), 0.0)
        _close_short(ex_name, exchange, symbol, price, mode, config, "🚨 EMERGENCY CLOSE — drawdown circuit breaker")

"""
Breakout/Momentum Strategy
=============================
RSI+MA mean-reversion is the wrong tool for strong trending markets — it
buys oversold dips and sells overbought rallies, which means it actively
fights a strong trend instead of riding it. In a real BULL_STRONG or
BEAR_STRONG regime, mean-reversion logic keeps selling into strength and
buying into weakness.

This module is a STRUCTURALLY DIFFERENT signal generator, not a retuned
version of the same RSI+MA rule. It only activates for the two strongest
trending regimes (BULL_STRONG, BEAR_STRONG) — everything else (BULL_WEAK,
SIDEWAYS, BEAR_WEAK, VOLATILE) continues using the existing RSI+MA logic
in bot.py, since mean-reversion is the right tool for those conditions.

THE ACTUAL LOGIC — Donchian channel breakout:
  BUY signal:  price breaks above the highest high of the last N candles
               (a genuine new high, not "RSI says oversold")
  SELL signal: price breaks below the lowest low of the last N candles,
               OR a trailing stop based on ATR is hit

This is a trend-following rule, not mean-reversion — it buys strength and
sells weakness, which is the correct posture in a strongly trending market.

WHAT THIS MODULE DOES NOT DO:
  - It does not replace RSI+MA for any other regime
  - It does not run by itself — bot.py's coin_worker calls
    get_active_strategy() to decide which signal function to use each
    cycle, based on the CURRENT regime at that moment
  - It does not bypass any existing risk control (stop-loss ceiling,
    drawdown circuit breaker, approval gate) — those apply identically
    regardless of which strategy generated the signal
"""

import logging
import pandas as pd

log = logging.getLogger(__name__)

# Regimes where breakout/momentum logic should be used INSTEAD of RSI+MA.
BREAKOUT_REGIMES = {"BULL_STRONG", "BEAR_STRONG"}

# Regimes where dip-buy logic should be used instead of standard RSI+MA.
# Standard mean-reversion requires price > MA before buying, which means
# it never buys anything in a real downtrend — every coin's price sits
# below its MA by definition there. Dip-buy logic intentionally drops
# that requirement and instead looks for a genuine local-bottom signal
# (deeply oversold RSI that has started turning back up), since waiting
# for price to reclaim the MA in a weak bear regime can mean sitting out
# entirely for as long as the downtrend lasts.
DIPBUY_REGIMES = {"BEAR_WEAK"}


def calc_donchian(df: pd.DataFrame, period: int = 20) -> tuple:
    """
    Donchian channel — the highest high and lowest low over the lookback
    period, EXCLUDING the current candle (so "breakout" means price just
    exceeded the prior range, not that it's measuring itself).
    """
    highs = df["high"].shift(1).rolling(period).max()
    lows  = df["low"].shift(1).rolling(period).min()
    return highs, lows


def breakout_signal(df: pd.DataFrame, action: str,
                    donchian_period: int = 20) -> dict:
    """
    Generates a buy or sell signal using Donchian breakout logic instead
    of RSI+MA. Returns the same shape as the RSI+MA check so bot.py's
    calling code doesn't need to know which strategy produced it.

    Args:
        df:     candle dataframe with high/low/close columns
        action: "BUY" or "SELL" — which side to check
        donchian_period: lookback window for the channel

    Returns:
        {
          "signal":  bool,
          "reason":  str,
          "price":   float (current close),
          "level":   float (the channel level that triggered/didn't),
        }
    """
    if len(df) < donchian_period + 5:
        return {"signal": False, "reason": "Not enough candles for Donchian channel",
               "price": None, "level": None}

    highs, lows = calc_donchian(df, donchian_period)
    price       = df["close"].iloc[-1]
    upper       = highs.iloc[-1]
    lower       = lows.iloc[-1]

    if pd.isna(upper) or pd.isna(lower):
        return {"signal": False, "reason": "Donchian channel not yet warmed up",
               "price": price, "level": None}

    if action == "BUY":
        triggered = price > upper
        return {
            "signal": triggered,
            "reason": (f"Breakout: price ${price:.6f} > {donchian_period}-candle "
                      f"high ${upper:.6f}" if triggered else
                      f"No breakout: price ${price:.6f} below "
                      f"{donchian_period}-candle high ${upper:.6f}"),
            "price": price,
            "level": upper,
        }
    else:   # SELL
        triggered = price < lower
        return {
            "signal": triggered,
            "reason": (f"Breakdown: price ${price:.6f} < {donchian_period}-candle "
                      f"low ${lower:.6f}" if triggered else
                      f"No breakdown: price ${price:.6f} above "
                      f"{donchian_period}-candle low ${lower:.6f}"),
            "price": price,
            "level": lower,
        }


def atr_trailing_stop(df: pd.DataFrame, entry_price: float,
                      direction: str = "long", atr_period: int = 14,
                      atr_multiplier: float = 2.5) -> float:
    """
    ATR-based trailing stop level for a breakout position. Wider than the
    bot's normal fixed-percentage stop, since breakout trades are meant
    to ride a strong move — a tight stop would exit on normal volatility
    before the trend has a chance to develop.

    Returns the stop price (not a percentage) — bot.py compares the
    current price against this directly.
    """
    if len(df) < atr_period + 5:
        # Not enough data for ATR — fall back to a conservative fixed %
        return entry_price * 0.95 if direction == "long" else entry_price * 1.05

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean().iloc[-1]

    if pd.isna(atr) or atr <= 0:
        return entry_price * 0.95 if direction == "long" else entry_price * 1.05

    if direction == "long":
        return entry_price - (atr * atr_multiplier)
    else:
        return entry_price + (atr * atr_multiplier)


def get_active_strategy(regime: str) -> str:
    """
    The actual regime → strategy router. This is the function bot.py
    calls every cycle to decide which signal logic to use.

    Returns "breakout", "dip_buy", or "mean_reversion".
    """
    if regime in BREAKOUT_REGIMES:
        return "breakout"
    if regime in DIPBUY_REGIMES:
        return "dip_buy"
    return "mean_reversion"


def evaluate_dipbuy_buy(df: pd.DataFrame, rsi: float, rsi_buy_threshold: float = 30,
                        lookback: int = 3, pool_type: str = "normal") -> dict:
    """
    Dip-buy signal for weak-downtrend regimes (BEAR_WEAK).

    Standard mean-reversion (rsi < threshold AND price > MA) structurally
    cannot fire in a real downtrend, since price sits below its MA by
    definition there — it would mean sitting out of every coin for as
    long as the downtrend lasts, even when a genuine bounce shows up.

    This drops the price > MA requirement and instead requires TWO
    things together, so a still-falling knife doesn't get bought just
    because RSI is low:
      1. RSI is below rsi_buy_threshold (deeply oversold — BEAR_WEAK's
         own calibrated threshold, e.g. 30, is tighter than the normal
         mean-reversion 35 since a dip in a downtrend needs to be more
         extreme to be a real bounce candidate, not just noise)
      2. RSI has been RISING over the last `lookback` candles, not
         falling — this is the actual "bottom forming" signal. Without
         this check, the function would buy into continued capitulation
         the moment RSI crossed below the threshold, which is exactly
         the falling-knife problem mean-reversion's price > MA check
         was protecting against in the first place.

    Aggressive pool gets a looser (higher) RSI threshold and shorter
    lookback, mirroring the same "fires more often" intent used
    elsewhere for /aggressive coins.
    """
    if pool_type == "aggressive":
        rsi_buy_threshold = min(38, rsi_buy_threshold + 6)
        lookback = max(2, lookback - 1)

    if len(df) < lookback + 15:
        return {"signal": False, "reason": "Not enough candle history for dip-buy check"}

    if rsi >= rsi_buy_threshold:
        return {"signal": False,
                "reason": f"RSI={rsi:.1f} not oversold enough for a dip-buy "
                         f"(need <{rsi_buy_threshold})"}

    # Recompute RSI for the lookback window so we can see its recent
    # direction, not just its current value.
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss
    rsi_series = (100 - (100 / (1 + rs))).dropna()

    if len(rsi_series) < lookback + 1:
        return {"signal": False, "reason": "Not enough RSI history to confirm a turn"}

    recent_rsi = rsi_series.iloc[-(lookback + 1):]
    is_turning_up = recent_rsi.iloc[-1] > recent_rsi.iloc[0]

    if not is_turning_up:
        return {"signal": False,
                "reason": f"RSI={rsi:.1f} is oversold but still falling "
                         f"(was {recent_rsi.iloc[0]:.1f} {lookback} candles ago) "
                         f"— likely still capitulating, waiting for a turn"}

    return {"signal": True,
            "reason": f"Dip-buy: RSI={rsi:.1f} deeply oversold and turning up "
                     f"(was {recent_rsi.iloc[0]:.1f} {lookback} candles ago) "
                     f"in a weak downtrend — bottom-forming signal"}


def evaluate_dipbuy_sell(df: pd.DataFrame, rsi: float, buy_price: float,
                         rsi_sell_threshold: float = 58,
                         stop_loss_pct: float = 0.06) -> dict:
    """
    Exit logic for a position opened by evaluate_dipbuy_buy.

    A dip-buy position was opened BELOW its MA in a downtrend — it may
    never climb back above that MA even after a profitable bounce, so
    the standard mean-reversion exit (rsi > threshold AND price < ma)
    is the wrong tool here just like price > ma was the wrong entry
    gate. This uses two independent exits instead:

      1. RSI has bounced back up to rsi_sell_threshold (BEAR_WEAK's own
         calibrated value, e.g. 58) — the bounce played out, take it.
      2. Price has fallen stop_loss_pct below the actual entry price —
         a fixed stop from entry, not a trailing stop from a peak,
         since a failed bounce may never form a meaningful peak before
         reversing again straight from the entry price.
    """
    current_price = df["close"].iloc[-1]

    if rsi >= rsi_sell_threshold:
        return {"signal": True, "exit_type": "rsi_bounce_target",
                "reason": f"RSI={rsi:.1f} reached bounce target (>={rsi_sell_threshold}) — taking the bounce"}

    stop_price = buy_price * (1 - stop_loss_pct)
    if current_price <= stop_price:
        return {"signal": True, "exit_type": "dipbuy_stop_loss",
                "reason": f"Price ${current_price:.6f} <= stop ${stop_price:.6f} "
                         f"({stop_loss_pct:.0%} below entry ${buy_price:.6f}) — bounce failed"}

    return {"signal": False, "exit_type": None,
            "reason": f"Holding — RSI={rsi:.1f} (target >={rsi_sell_threshold}), "
                     f"price ${current_price:.6f} above stop ${stop_price:.6f}"}


def evaluate_breakout_buy(df: pd.DataFrame, donchian_period: int = 20,
                          require_volume_confirmation: bool = True,
                          volume_multiplier: float = 1.3,
                          pool_type: str = "normal") -> dict:
    """
    Full breakout buy evaluation including volume confirmation — a price
    breakout on thin volume is much more likely to be noise/a fakeout
    than a genuine trend continuation.

    pool_type makes this AWARE of /aggressive — without this, a coin in
    the aggressive pool would silently get IDENTICAL breakout behaviour
    to a safe-pool coin whenever a strong trending regime was active,
    making the /aggressive command meaningless during those regimes.
    Aggressive coins use a shorter (more responsive) Donchian window and
    a lower volume bar, mirroring the same "fires more often" intent as
    the wider RSI band aggressive mode uses in mean-reversion regimes.
    """
    if pool_type == "aggressive":
        donchian_period   = max(10, donchian_period - 8)   # shorter, more reactive channel
        volume_multiplier = max(1.0, volume_multiplier - 0.2)   # looser confirmation bar

    result = breakout_signal(df, "BUY", donchian_period)

    if not result["signal"]:
        return result

    if require_volume_confirmation and "volume" in df.columns and len(df) >= 20:
        current_vol = df["volume"].iloc[-1]
        avg_vol     = df["volume"].rolling(20).mean().iloc[-1]
        if pd.notna(avg_vol) and avg_vol > 0:
            vol_ratio = current_vol / avg_vol
            if vol_ratio < volume_multiplier:
                result["signal"] = False
                result["reason"] = (f"Breakout detected but volume too thin "
                                   f"({vol_ratio:.1f}x avg, need {volume_multiplier}x) "
                                   f"— likely a fakeout, not a genuine move")
            else:
                result["reason"] += f" (confirmed by {vol_ratio:.1f}x volume)"

    return result


def evaluate_breakout_sell(df: pd.DataFrame, buy_price: float,
                           donchian_period: int = 20,
                           atr_multiplier: float = 2.5,
                           pool_type: str = "normal") -> dict:
    """
    Full breakout sell evaluation — exits on EITHER a Donchian breakdown
    OR the ATR trailing stop, whichever triggers first. Returns the same
    shape as breakout_signal with an added "exit_type" field.

    pool_type mirrors evaluate_breakout_buy — aggressive positions use the
    same shorter Donchian window they entered on, so the exit rule stays
    consistent with the entry rule that actually opened the trade.
    """
    if pool_type == "aggressive":
        donchian_period = max(10, donchian_period - 8)

    donchian_result = breakout_signal(df, "SELL", donchian_period)
    if donchian_result["signal"]:
        donchian_result["exit_type"] = "donchian_breakdown"
        return donchian_result

    current_price = df["close"].iloc[-1]
    stop_price    = atr_trailing_stop(df, buy_price, "long", 14, atr_multiplier)

    if current_price <= stop_price:
        return {
            "signal":    True,
            "reason":    f"ATR trailing stop hit: price ${current_price:.6f} <= stop ${stop_price:.6f}",
            "price":     current_price,
            "level":     stop_price,
            "exit_type": "atr_trailing_stop",
        }

    return {
        "signal":    False,
        "reason":    f"Holding — price ${current_price:.6f} above ATR stop ${stop_price:.6f} "
                    f"and Donchian channel intact",
        "price":     current_price,
        "level":     stop_price,
        "exit_type": None,
    }

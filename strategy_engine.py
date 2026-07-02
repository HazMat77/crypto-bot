"""
Adaptive Strategy Engine
=========================
Addresses the core weaknesses:

1. RISK/REWARD FIX
   Default TP=4%, SL=6% requires >60% win rate just to break even.
   This engine auto-calibrates TP/SL based on actual win rate,
   volatility (ATR), and recent performance to ensure positive expectancy.

2. EDGE BEYOND RSI+MA
   Adds additional filters that reduce false signals:
   - Volume confirmation (high volume = real move)
   - Trend strength (ADX) - avoid choppy markets
   - Support/resistance levels
   - Multi-timeframe confirmation

3. LISTING HUNTER SAFETY
   Separate risk budget, position sizing, and liquidity checks.
   Treats listing trades as isolated experiments.

4. NEWS SENTIMENT GUARD
   Only uses news to VETO trades, not initiate them.
   News confirmation = slight confidence boost only.
   Never trades purely on news.

5. SELF-OPTIMISATION
   Tracks every trade's actual outcome.
   Adjusts parameters weekly based on real performance data.
   Uses Kelly Criterion for position sizing.
"""

import logging
import math
from collections import deque
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  RISK/REWARD CALCULATOR
# ══════════════════════════════════════════════════════════════════════════════

class RiskRewardCalibrator:
    """
    Dynamically sets TP/SL to ensure positive expected value.

    Formula:
        Expected Value = (win_rate × avg_win) - (loss_rate × avg_loss)
        For EV > 0: TP/SL ratio > loss_rate / win_rate

    Example:
        Win rate 55% → need TP/SL ratio > 0.45/0.55 = 0.818
        So if SL=6%, TP must be > 4.9% (not 4%!)
    """

    def __init__(self, min_tp: float = 0.025, max_tp: float = 0.25,
                 min_sl: float = 0.02,  max_sl: float = 0.10):
        self.min_tp = min_tp
        self.max_tp = max_tp
        self.min_sl = min_sl
        self.max_sl = max_sl

    def optimal_tp_sl(self, win_rate: float, atr_pct: float = 0.02) -> tuple:
        """
        Calculate TP/SL that gives positive expected value.

        Args:
            win_rate:  Historical win rate (0-1)
            atr_pct:   ATR as fraction of price (volatility measure)

        Returns:
            (take_profit, stop_loss) as fractions
        """
        if win_rate <= 0 or win_rate >= 1:
            win_rate = 0.55   # fallback

        loss_rate = 1 - win_rate

        # Minimum TP/SL ratio for positive EV
        min_ratio = loss_rate / win_rate   # e.g. 0.45/0.55 = 0.818

        # ATR-based SL — use 1.5x ATR as natural stop
        atr_sl = min(max(atr_pct * 1.5, self.min_sl), self.max_sl)

        # TP must exceed min_ratio * SL for positive EV
        min_tp = atr_sl * min_ratio * 1.1   # 10% buffer above breakeven

        # But also cap at max_tp
        tp = min(max(min_tp, self.min_tp), self.max_tp)
        sl = atr_sl

        log.debug(f"[RR] win_rate={win_rate:.1%} min_ratio={min_ratio:.2f} "
                 f"→ TP={tp:.2%} SL={sl:.2%} (ratio={tp/sl:.2f})")
        return round(tp, 4), round(sl, 4)

    def kelly_position_size(self, win_rate: float, avg_win: float,
                            avg_loss: float, pool: float,
                            max_pct: float = 0.25) -> float:
        """
        Kelly Criterion position sizing — maximises long-term growth.
        Kelly fraction = (win_rate/abs_loss) - (loss_rate/avg_win)

        Capped at max_pct of pool to prevent over-betting.
        """
        if avg_win <= 0 or avg_loss >= 0:
            return pool * 0.10   # fallback: 10% of pool

        loss_rate    = 1 - win_rate
        kelly_frac   = (win_rate / abs(avg_loss)) - (loss_rate / avg_win)
        kelly_frac   = max(0.0, min(kelly_frac, max_pct))   # cap at max_pct

        # Use half-Kelly for safety (common practice)
        half_kelly   = kelly_frac * 0.5
        position     = pool * half_kelly

        log.debug(f"[KELLY] win={win_rate:.1%} avg_win={avg_win:.4f} "
                 f"avg_loss={avg_loss:.4f} kelly={kelly_frac:.2%} "
                 f"half_kelly={half_kelly:.2%} position=${position:.2f}")
        return round(position, 2)


# ══════════════════════════════════════════════════════════════════════════════
#  ADDITIONAL SIGNAL FILTERS
# ══════════════════════════════════════════════════════════════════════════════

class SignalFilters:
    """
    Additional filters beyond RSI+MA to reduce false signals.
    Each filter returns True (signal valid) or False (veto).
    """

    @staticmethod
    def volume_confirmation(df: pd.DataFrame, threshold: float = 1.3) -> bool:
        """
        Volume should be above average for a genuine move.
        Threshold: current volume > 1.3x 20-period average = real move.
        """
        if "volume" not in df.columns or len(df) < 20:
            return True   # no data = don't block

        current_vol = df["volume"].iloc[-1]
        avg_vol     = df["volume"].rolling(20).mean().iloc[-1]

        if avg_vol <= 0:
            return True

        ratio   = current_vol / avg_vol
        passes  = ratio >= threshold
        log.debug(f"[FILTER] Volume: {ratio:.2f}x avg {'✅' if passes else '❌'}")
        return passes

    @staticmethod
    def adx_trend_strength(df: pd.DataFrame,
                           period: int = 14,
                           min_adx: float = 20.0) -> bool:
        """
        ADX (Average Directional Index) measures trend strength.
        ADX > 20 = meaningful trend present (good for RSI signals)
        ADX < 15 = choppy sideways market (many false signals)
        """
        if len(df) < period * 2 + 5:
            return True

        try:
            high  = df["high"]
            low   = df["low"]
            close = df["close"]

            # True Range
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low  - close.shift(1)).abs()
            ], axis=1).max(axis=1)

            # Directional movement
            dm_plus  = (high - high.shift(1)).clip(lower=0)
            dm_minus = (low.shift(1) - low).clip(lower=0)

            # Zero out where the other is larger
            dm_plus  = dm_plus.where(dm_plus > dm_minus, 0)
            dm_minus = dm_minus.where(dm_minus > dm_plus, 0)

            # Smoothed
            atr14   = tr.rolling(period).mean()
            di_plus = 100 * (dm_plus.rolling(period).mean()  / atr14)
            di_minus= 100 * (dm_minus.rolling(period).mean() / atr14)

            dx      = 100 * ((di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10))
            adx     = dx.rolling(period).mean().iloc[-1]

            passes  = adx >= min_adx
            log.debug(f"[FILTER] ADX={adx:.1f} (min={min_adx}) {'✅' if passes else '❌ choppy'}")
            return passes

        except Exception as e:
            log.debug(f"[FILTER] ADX calc failed: {e}")
            return True

    @staticmethod
    def multi_timeframe_check(exchange, symbol: str,
                               higher_tf: str = "1hour") -> bool:
        """
        Check that the higher timeframe (1h) RSI agrees with 15min signal.
        Reduces counter-trend trades.
        Returns True if higher TF supports the signal direction.
        """
        try:
            df_htf = exchange.get_candles(symbol, higher_tf)
            if df_htf is None or len(df_htf) < 20:
                return True

            from bot import calc_rsi, calc_ma
            rsi_htf = calc_rsi(df_htf["close"], 14)
            ma_htf  = calc_ma(df_htf["close"],  20)
            price   = df_htf["close"].iloc[-1]

            # 1h RSI between 30-70 = not extreme = ok to trade
            htf_ok  = 30 <= rsi_htf <= 70
            log.debug(f"[FILTER] HTF RSI={rsi_htf:.1f} {'✅' if htf_ok else '❌ extreme HTF'}")
            return htf_ok

        except Exception:
            return True   # fail open

    @staticmethod
    def news_veto_only(news_sentiment: str, action: str) -> bool:
        """
        News ONLY used to veto, never to initiate.
        Strong bearish news on a BUY signal = veto.
        Strong bullish news on SELL signal = veto.
        Neutral/unknown = allow through.
        """
        if not news_sentiment or news_sentiment == "unknown":
            return True

        # Veto BUY on strongly bearish news
        if action == "BUY" and news_sentiment == "bearish":
            log.info("[FILTER] News veto: bearish sentiment blocks BUY ❌")
            return False

        # Veto SELL on strongly bullish news
        if action == "SELL" and news_sentiment == "bullish":
            log.info("[FILTER] News veto: bullish sentiment blocks SELL ❌")
            return False

        return True

    @staticmethod
    def liquidity_check(df: pd.DataFrame,
                        min_volume_usdt: float = 2_000) -> bool:
        """
        Ensure enough liquidity to enter/exit cleanly.
        Thin markets = large slippage on execution.
        """
        if "volume" not in df.columns:
            return True
        try:
            close  = df["close"].iloc[-1]
            vol    = df["volume"].iloc[-1]
            usdt_vol = close * vol
            passes = usdt_vol >= min_volume_usdt
            if not passes:
                log.debug(f"[FILTER] Liquidity: ${usdt_vol:.0f} < ${min_volume_usdt:.0f} ❌")
            return passes
        except Exception:
            return True


# ══════════════════════════════════════════════════════════════════════════════
#  PERFORMANCE TRACKER
# ══════════════════════════════════════════════════════════════════════════════

class PerformanceTracker:
    """
    Tracks rolling performance metrics.
    Used by the strategy engine for self-optimisation.
    """

    def __init__(self, window: int = 50):
        self.window     = window
        self.trades     = deque(maxlen=window)   # rolling last N trades
        self._lock_data = []                      # all-time trade list

    def record(self, pnl_net: float, pnl_pct: float, exit_reason: str):
        trade = {
            "pnl_net":     pnl_net,
            "pnl_pct":     pnl_pct,
            "exit_reason": exit_reason,
            "timestamp":   datetime.now(),
        }
        self.trades.append(trade)
        self._lock_data.append(trade)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.55   # default assumption
        wins = sum(1 for t in self.trades if t["pnl_net"] >= 0)
        return wins / len(self.trades)

    @property
    def avg_win(self) -> float:
        wins = [t["pnl_net"] for t in self.trades if t["pnl_net"] >= 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t["pnl_net"] for t in self.trades if t["pnl_net"] < 0]
        return sum(losses) / len(losses) if losses else 0.0

    @property
    def expectancy(self) -> float:
        """Expected value per trade, in dollars."""
        if not self.trades:
            return 0.0
        return (self.win_rate * self.avg_win) + ((1-self.win_rate) * self.avg_loss)

    @property
    def avg_win_pct(self) -> float:
        wins = [t["pnl_pct"] for t in self.trades if t["pnl_net"] >= 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss_pct(self) -> float:
        losses = [t["pnl_pct"] for t in self.trades if t["pnl_net"] < 0]
        return sum(losses) / len(losses) if losses else 0.0

    @property
    def expectancy_pct(self) -> float:
        """
        Expected value per trade as a fraction of position size (not
        dollars) — used by hybrid_allocator.py to compare a trade's real,
        track-recorded edge against staking APR on an apples-to-apples
        %-return basis regardless of position size. Falls back to None
        (not 0.0 — 0.0 would look like "genuinely breakeven" rather than
        "no data yet") when there isn't enough trade history to trust,
        letting the caller fall back to a config-assumed win rate instead.
        """
        if len(self.trades) < 10:
            return None
        return (self.win_rate * self.avg_win_pct) + ((1 - self.win_rate) * self.avg_loss_pct)

    @property
    def profit_factor(self) -> float:
        gross_win  = sum(t["pnl_net"] for t in self.trades if t["pnl_net"] > 0)
        gross_loss = abs(sum(t["pnl_net"] for t in self.trades if t["pnl_net"] < 0))
        return gross_win / gross_loss if gross_loss > 0 else float("inf")

    def summary(self) -> dict:
        return {
            "trades":         len(self.trades),
            "win_rate":       round(self.win_rate, 3),
            "avg_win":        round(self.avg_win,  4),
            "avg_loss":       round(self.avg_loss, 4),
            "expectancy":     round(self.expectancy, 4),
            "profit_factor":  round(self.profit_factor, 2),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  ADAPTIVE STRATEGY ENGINE (main class)
# ══════════════════════════════════════════════════════════════════════════════

# Global trackers — one per exchange:coin pair
_trackers: dict = {}

def get_tracker(ex_name: str, symbol: str) -> PerformanceTracker:
    key = f"{ex_name}:{symbol}"
    if key not in _trackers:
        _trackers[key] = PerformanceTracker(window=50)
    return _trackers[key]


def evaluate_signal(
    action:          str,           # "BUY" or "SELL"
    symbol:          str,
    price:           float,
    rsi:             float,
    ma:              float,
    df:              pd.DataFrame,
    exchange,
    ex_name:         str,
    pool:            float,
    news_sentiment:  str = "unknown",
    pool_type:       str = "normal",
    config = None,
) -> dict:
    """
    Comprehensive signal evaluation with all filters and position sizing.

    Returns:
    {
        "approved":       True/False,
        "confidence":     0-100,
        "take_profit":    float,
        "stop_loss":      float,
        "position_size":  float (USDT),
        "reason":         str,
        "filters_passed": list,
        "filters_failed": list,
    }
    """
    filters_passed = []
    filters_failed = []

    tracker   = get_tracker(ex_name, symbol)
    calibrator = RiskRewardCalibrator()
    filt       = SignalFilters()

    # ── Get ATR for volatility-based sizing ───────────────────────────────
    atr_pct = 0.02   # default 2%
    if "high" in df.columns and len(df) >= 14:
        try:
            from bot import calc_atr as _calc_atr
            atr_val = _calc_atr(df["high"].tolist(), df["low"].tolist(),
                               df["close"].tolist(), 14)
            if price > 0:
                atr_pct = atr_val / price
        except Exception:
            pass

    # ── Calibrate TP/SL based on actual win rate ──────────────────────────
    wr      = tracker.win_rate
    tp, sl  = calibrator.optimal_tp_sl(wr, atr_pct)

    # Override with pool-specific settings if dual pool enabled
    if config and getattr(config, "DUAL_POOL_ENABLED", False):
        if pool_type == "aggressive":
            tp = getattr(config, "AGGRESSIVE_TAKE_PROFIT", tp)
            sl = getattr(config, "AGGRESSIVE_STOP_LOSS",   sl)
        else:
            tp = getattr(config, "NORMAL_TAKE_PROFIT", tp)
            sl = getattr(config, "NORMAL_STOP_LOSS",   sl)

    # ── HARD CEILING — final safety net, applies regardless of source ──────
    # No regime, preset, dual-pool setting, or AI suggestion can push the
    # stop loss past this. Enforced here as the last step before use.
    sl_ceiling = getattr(config, "MAX_STOP_LOSS_PCT", 0.04) if config else 0.04
    sl = min(sl, sl_ceiling)

    # ── Run filters ───────────────────────────────────────────────────────
    confidence = 60   # base — RSI+MA signal already confirmed before reaching here

    # 1. Volume confirmation
    if filt.volume_confirmation(df):
        filters_passed.append("volume")
        confidence += 10
    else:
        filters_failed.append("volume_low")
        confidence -= 5

    # 2. ADX trend strength (only matters for BUY signals — avoid choppy)
    if action == "BUY":
        if filt.adx_trend_strength(df):
            filters_passed.append("adx_trend")
            confidence += 15
        else:
            filters_failed.append("adx_choppy")
            confidence -= 8    # reduced from 20 — ADX absence shouldn't kill a valid RSI signal

    # 3. Liquidity check
    if filt.liquidity_check(df):
        filters_passed.append("liquidity")
        confidence += 5
    else:
        filters_failed.append("liquidity_thin")
        confidence -= 10   # reduced from 15

    # 4. News veto (only blocks, never boosts above neutral)
    if filt.news_veto_only(news_sentiment, action):
        filters_passed.append("news_ok")
        if news_sentiment == "bullish" and action == "BUY":
            confidence += 8
        elif news_sentiment == "bearish" and action == "SELL":
            confidence += 8
    else:
        filters_failed.append("news_veto")
        confidence -= 40   # strong news veto = don't trade

    # 5b. Multi-timeframe confirmation — requires the 1h RSI to agree with
    # (not be at a directional extreme opposing) the 15m entry signal.
    # Off by default in the sense that it fails OPEN (multi_timeframe_check
    # itself returns True on any data/network error, same as the ADX/
    # liquidity checks) — but when MULTI_TIMEFRAME_REQUIRED is True and it
    # actually gets a clean read that DISAGREES, it's a hard veto like news,
    # not just a confidence nudge, because "the higher timeframe already
    # looks played out" is exactly the kind of false-signal 15m alone can't see.
    if action == "BUY" and getattr(config, "MULTI_TIMEFRAME_CONFIRMATION_ENABLED", True):
        mtf_ok = filt.multi_timeframe_check(exchange, symbol)
        if mtf_ok:
            filters_passed.append("mtf_aligned")
            confidence += 8
        else:
            filters_failed.append("mtf_conflict")
            confidence -= 15
            if getattr(config, "MULTI_TIMEFRAME_REQUIRED", False):
                log.info(f"[ENGINE] {symbol} 1h/15m timeframe conflict — hard veto "
                        f"(MULTI_TIMEFRAME_REQUIRED=True)")

    # 5. Win rate sanity check — don't trade if recent performance is poor
    if len(tracker.trades) >= 10:
        if tracker.win_rate < 0.40:
            filters_failed.append("low_win_rate")
            confidence -= 25
            log.info(f"[ENGINE] {symbol} recent win rate {tracker.win_rate:.0%} < 40% — caution")
        elif tracker.win_rate >= 0.55:
            filters_passed.append("good_win_rate")
            confidence += 10

    # 6. Expectancy check — if strategy has negative expectancy, pause
    if len(tracker.trades) >= 20 and tracker.expectancy < -0.02:
        filters_failed.append("negative_expectancy")
        confidence -= 30
        log.warning(f"[ENGINE] {symbol} negative expectancy {tracker.expectancy:.4f} — signalling caution")

    confidence = max(0, min(100, confidence))

    # ── Position sizing via Kelly ─────────────────────────────────────────
    position_size = calibrator.kelly_position_size(
        wr, tracker.avg_win, tracker.avg_loss, pool
    )
    # Never more than pool allows
    position_size = min(position_size, pool * 0.15)

    # Aggressive pool gets slightly more per trade
    if pool_type == "aggressive":
        position_size = min(position_size * 1.2, pool * 0.20)

    # ── Decision ─────────────────────────────────────────────────────────
    # Engine approval threshold is intentionally separate from AI_CONFIDENCE_MIN
    # AI_CONFIDENCE_MIN gates the AI's own opinion; ENGINE_CONFIDENCE_MIN gates
    # the filter-stack score. Keeping these separate prevents double-penalising
    # signals that already passed RSI+MA confirmation.
    threshold = getattr(config, "ENGINE_CONFIDENCE_MIN", 55) if config else 55
    mtf_required = getattr(config, "MULTI_TIMEFRAME_REQUIRED", False) if config else False
    approved  = confidence >= threshold and "news_veto" not in filters_failed \
                and "liquidity_thin" not in filters_failed \
                and not (mtf_required and "mtf_conflict" in filters_failed)

    reason = (f"TP={tp:.1%} SL={sl:.1%} WR={wr:.0%} "
             f"filters={'✅' if approved else '❌'} "
             f"passed={filters_passed} "
             f"{'failed='+str(filters_failed) if filters_failed else ''}")

    log.info(f"[ENGINE] {ex_name.upper()}:{symbol} {action} → "
            f"{'APPROVED' if approved else 'REJECTED'} "
            f"conf={confidence}% pos=${position_size:.2f} "
            f"TP={tp:.1%} SL={sl:.1%}")

    return {
        "approved":       approved,
        "confidence":     confidence,
        "take_profit":    tp,
        "stop_loss":      sl,
        "position_size":  round(position_size, 2),
        "reason":         reason,
        "filters_passed": filters_passed,
        "filters_failed": filters_failed,
        "win_rate_used":  round(wr, 3),
        "atr_pct":        round(atr_pct, 4),
    }


def record_trade_outcome(ex_name: str, symbol: str,
                          pnl_net: float, pnl_pct: float,
                          exit_reason: str):
    """Call this after every sell to update the performance tracker."""
    tracker = get_tracker(ex_name, symbol)
    tracker.record(pnl_net, pnl_pct, exit_reason)

    s = tracker.summary()
    log.info(f"[ENGINE] {ex_name.upper()}:{symbol} updated — "
            f"WR={s['win_rate']:.0%} EV={s['expectancy']:.4f} "
            f"PF={s['profit_factor']:.2f} ({s['trades']} trades)")

"""
Market Regime Detector & Adaptive Intelligence
================================================
Detects current market conditions and automatically adapts
the entire strategy to perform optimally in any environment.

MARKET REGIMES:
  BULL_STRONG   — Strong uptrend, high momentum, rising volume
  BULL_WEAK     — Mild uptrend, consolidating
  SIDEWAYS      — No clear direction, choppy oscillation
  BEAR_WEAK     — Mild downtrend, losing momentum
  BEAR_STRONG   — Strong downtrend, high selling pressure
  VOLATILE      — Wild swings, unpredictable direction

ADAPTIVE BEHAVIOURS per regime:
  BULL_STRONG:  Wider RSI range, larger positions, higher TP
  BULL_WEAK:    Normal settings, moderate sizing
  SIDEWAYS:     Tighter range, smaller positions, quick TP
  BEAR_WEAK:    Very tight stops, minimal buying, focus on short holds
  BEAR_STRONG:  Pause most buying, only strongest signals, tiny sizes
  VOLATILE:     ATR-based sizing, extra filters, fast exits

SELF-LEARNING:
  Tracks which parameter sets performed best in each regime.
  After enough trades, automatically sets optimal parameters
  for the detected regime without any human intervention.
"""

import json
import logging
import threading
import requests
import time
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict, deque
import pandas as pd
import numpy as np

log = logging.getLogger(__name__)

REGIME_LOG = Path("logs/regime_history.json")
REGIME_LOG.parent.mkdir(exist_ok=True)

# ── Regime definitions ─────────────────────────────────────────────────────
REGIMES = {
    "BULL_STRONG":  {"emoji": "🚀", "color": "green",  "description": "Strong bull market"},
    "BULL_WEAK":    {"emoji": "📈", "color": "green",  "description": "Mild uptrend"},
    "SIDEWAYS":     {"emoji": "↔️", "color": "yellow", "description": "Choppy sideways"},
    "BEAR_WEAK":    {"emoji": "📉", "color": "orange", "description": "Mild downtrend"},
    "BEAR_STRONG":  {"emoji": "🐻", "color": "red",    "description": "Strong bear market"},
    "VOLATILE":     {"emoji": "⚡", "color": "purple", "description": "High volatility"},
}

# ── Strategy presets per regime ────────────────────────────────────────────
REGIME_STRATEGIES = {
    "BULL_STRONG": {
        "rsi_buy":              40,    # wider — catch more of the upswing
        "rsi_sell":             70,    # let winners run longer
        "stop_loss_pct":        0.10,  # full 10% ceiling — strongest trend, most room
        "take_profit_pct":      0.25,  # 25% target — trend has momentum to capture it
        "trailing_stop_pct":    0.04,  # trail up with the move
        "max_hold_hours":       72,    # hold longer — trend your friend
        "position_size_mult":   1.3,   # bigger bets in bull market
        "min_adx":              20,    # normal trend filter
        "volume_threshold":     1.2,   # slightly relaxed volume req
        "aggressive_pct":       0.30,  # give aggressive pool more capital
        "description": "Riding the bull — wider range, hold longer, size up",
    },
    "BULL_WEAK": {
        "rsi_buy":              37,
        "rsi_sell":             65,
        "stop_loss_pct":        0.08,  # 8% — mild trend, slightly under full ceiling
        "take_profit_pct":      0.20,  # 20% target
        "trailing_stop_pct":    0.03,
        "max_hold_hours":       48,
        "position_size_mult":   1.0,
        "min_adx":              20,
        "volume_threshold":     1.3,
        "aggressive_pct":       0.20,
        "description": "Mild bull — standard settings with slight bias to hold",
    },
    "SIDEWAYS": {
        "rsi_buy":              33,    # tighter — only strong oversold signals
        "rsi_sell":             62,    # exit sooner — no trend to ride
        "stop_loss_pct":        0.05,  # tight stop — choppy = quick reversals, well under ceiling
        "take_profit_pct":      0.15,  # quick profit — don't wait for trend, smaller target than trending regimes
        "trailing_stop_pct":    0.02,  # tight trail — lock in small gains fast
        "max_hold_hours":       24,    # get in and out quickly
        "position_size_mult":   0.7,   # smaller bets — no clear direction
        "min_adx":              25,    # higher ADX req — need real signals
        "volume_threshold":     1.5,   # strict volume — avoid false breakouts
        "aggressive_pct":       0.10,  # reduce aggressive pool
        "description": "Choppy market — tight ranges, quick in/out, small sizes",
    },
    "BEAR_WEAK": {
        "rsi_buy":              30,    # only extremely oversold
        "rsi_sell":             58,    # exit quickly before more downside
        "stop_loss_pct":        0.06,  # 6% — conservative for a weakening market
        "take_profit_pct":      0.15,
        "trailing_stop_pct":    0.025,
        "max_hold_hours":       24,
        "position_size_mult":   0.6,   # smaller bets
        "min_adx":              22,
        "volume_threshold":     1.4,
        "aggressive_pct":       0.05,  # minimal aggressive exposure
        "description": "Bear starting — conservative, fast exits, small sizes",
    },
    "BEAR_STRONG": {
        "rsi_buy":              25,    # only extreme capitulation signals
        "rsi_sell":             55,    # exit very quickly
        "stop_loss_pct":        0.04,  # tightest of all regimes — protect capital in a strong bear
        "take_profit_pct":      0.10,  # take wins fast, don't get greedy in a downtrend
        "trailing_stop_pct":    0.02,
        "max_hold_hours":       12,    # very short holds only
        "position_size_mult":   0.4,   # much smaller bets
        "min_adx":              30,    # only very clear signals
        "volume_threshold":     1.8,   # need strong confirmation
        "aggressive_pct":       0.0,   # no aggressive trading in strong bear
        "description": "Strong bear — minimal trading, only best signals, protect capital",
    },
    "VOLATILE": {
        "rsi_buy":              32,
        "rsi_sell":             68,    # wider to avoid whipsaw
        "stop_loss_pct":        0.10,  # full ceiling — volatility needs maximum room
        "take_profit_pct":      0.25,  # 25% target to clear the noise
        "trailing_stop_pct":    0.05,  # wider trail
        "max_hold_hours":       36,
        "position_size_mult":   0.7,   # smaller — high risk
        "min_adx":              25,
        "volume_threshold":     1.6,   # need strong volume confirmation
        "aggressive_pct":       0.15,
        "description": "Volatile market — wider stops, higher targets, reduced sizes",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
#  REGIME DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

class RegimeDetector:
    """
    Detects market regime from multiple signals:
    - Price trend (EMA cross, higher highs/lows)
    - Volatility (ATR vs historical ATR)
    - Volume trend
    - BTC dominance change
    - RSI of RSI (momentum of momentum)
    - Fear/Greed proxy from price action
    """

    def __init__(self):
        self.current_regime    = "SIDEWAYS"
        self.regime_confidence = 50
        self.regime_history    = deque(maxlen=30)
        self.last_detected     = datetime.min
        self._lock             = threading.Lock()

    def detect(self, btc_df: pd.DataFrame,
               market_change_24h: float = 0.0,
               btc_dominance: float = 50.0) -> tuple:
        """
        Analyse BTC price data + market data to detect regime.
        Returns (regime_name, confidence_pct, signals_dict)
        """
        if btc_df is None or len(btc_df) < 50:
            return self.current_regime, self.regime_confidence, {}

        close  = btc_df["close"]
        high   = btc_df["high"]   if "high"   in btc_df.columns else close
        low    = btc_df["low"]    if "low"    in btc_df.columns else close
        volume = btc_df["volume"] if "volume" in btc_df.columns else pd.Series([1]*len(close))

        signals = {}

        # ── 1. Trend direction (EMA 20 vs EMA 50) ─────────────────────────
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        ema_bull = float(ema20.iloc[-1]) > float(ema50.iloc[-1])
        ema_gap  = (float(ema20.iloc[-1]) - float(ema50.iloc[-1])) / float(ema50.iloc[-1])
        signals["ema_bull"]     = ema_bull
        signals["ema_gap_pct"]  = round(ema_gap * 100, 2)

        # ── 2. Trend strength (price vs EMA50) ────────────────────────────
        price_vs_ema50 = (float(close.iloc[-1]) - float(ema50.iloc[-1])) / float(ema50.iloc[-1])
        signals["price_vs_ema50"] = round(price_vs_ema50 * 100, 2)

        # ── 3. Volatility (current ATR vs 30-period avg ATR) ──────────────
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr14    = tr.rolling(14).mean()
        atr_norm = float(atr14.iloc[-1]) / float(close.iloc[-1])   # ATR as % of price
        atr_avg  = float(atr14.rolling(30).mean().iloc[-1])
        volatility_elevated = float(atr14.iloc[-1]) > atr_avg * 1.5
        signals["atr_pct"]             = round(atr_norm * 100, 2)
        signals["volatility_elevated"] = volatility_elevated

        # ── 4. Volume trend (rising or falling) ───────────────────────────
        vol20    = volume.rolling(20).mean()
        vol_bull = float(volume.iloc[-1]) > float(vol20.iloc[-1])
        signals["volume_above_avg"] = vol_bull

        # ── 5. Price momentum (last 10 candles) ───────────────────────────
        momentum_10 = (float(close.iloc[-1]) - float(close.iloc[-10])) / float(close.iloc[-10])
        signals["momentum_10_pct"] = round(momentum_10 * 100, 2)

        # ── 6. Market context ──────────────────────────────────────────────
        signals["market_change_24h"] = market_change_24h
        signals["btc_dominance"]     = btc_dominance

        # ── Classify regime ────────────────────────────────────────────────
        regime, confidence = self._classify(signals)

        with self._lock:
            old_regime           = self.current_regime
            self.current_regime  = regime
            self.regime_confidence = confidence
            self.last_detected   = datetime.now()
            self.regime_history.append({
                "time":       datetime.now().isoformat(),
                "regime":     regime,
                "confidence": confidence,
                "signals":    signals,
            })

        if regime != old_regime:
            log.info(f"[REGIME] ═══ REGIME CHANGE: {old_regime} → {regime} "
                    f"({confidence}% confidence) ═══")
            self._save_history()

        return regime, confidence, signals

    def _classify(self, s: dict) -> tuple:
        """Score each regime and return the best match with confidence."""
        scores = {r: 0 for r in REGIMES}

        ema_bull      = s.get("ema_bull", True)
        ema_gap       = s.get("ema_gap_pct", 0)
        price_ema50   = s.get("price_vs_ema50", 0)
        volatility    = s.get("volatility_elevated", False)
        vol_bull      = s.get("volume_above_avg", True)
        momentum      = s.get("momentum_10_pct", 0)
        mkt_chg       = s.get("market_change_24h", 0)

        # Bull signals
        if ema_bull:
            scores["BULL_STRONG"] += 3
            scores["BULL_WEAK"]   += 2
        if ema_gap > 2:
            scores["BULL_STRONG"] += 2
        if price_ema50 > 3:
            scores["BULL_STRONG"] += 2
        elif price_ema50 > 1:
            scores["BULL_WEAK"]   += 1
        if momentum > 3:
            scores["BULL_STRONG"] += 2
        elif momentum > 1:
            scores["BULL_WEAK"]   += 1
        if mkt_chg > 3:
            scores["BULL_STRONG"] += 2
        elif mkt_chg > 1:
            scores["BULL_WEAK"]   += 1
        if vol_bull and ema_bull:
            scores["BULL_STRONG"] += 1

        # Bear signals
        if not ema_bull:
            scores["BEAR_STRONG"] += 3
            scores["BEAR_WEAK"]   += 2
        if ema_gap < -2:
            scores["BEAR_STRONG"] += 2
        if price_ema50 < -3:
            scores["BEAR_STRONG"] += 2
        elif price_ema50 < -1:
            scores["BEAR_WEAK"]   += 1
        if momentum < -3:
            scores["BEAR_STRONG"] += 2
        elif momentum < -1:
            scores["BEAR_WEAK"]   += 1
        if mkt_chg < -3:
            scores["BEAR_STRONG"] += 2
        elif mkt_chg < -1:
            scores["BEAR_WEAK"]   += 1

        # Sideways signals
        if abs(ema_gap) < 1 and abs(momentum) < 1:
            scores["SIDEWAYS"] += 3
        if abs(price_ema50) < 2:
            scores["SIDEWAYS"] += 2
        if abs(mkt_chg) < 1:
            scores["SIDEWAYS"] += 1

        # Volatility signals — override if ATR is very high
        if volatility:
            scores["VOLATILE"] += 4
            if abs(momentum) > 4:
                scores["VOLATILE"] += 2

        # Winner
        best_regime = max(scores, key=lambda r: scores[r])
        total       = sum(scores.values()) or 1
        confidence  = int(scores[best_regime] / total * 100)

        return best_regime, min(confidence, 95)

    def _save_history(self):
        try:
            history = list(self.regime_history)[-100:]
            with open(REGIME_LOG, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, default=str)
        except Exception as e:
            log.debug(f"[REGIME] Could not save history: {e}")

    @property
    def info(self) -> dict:
        with self._lock:
            return {
                "regime":     self.current_regime,
                "confidence": self.regime_confidence,
                "emoji":      REGIMES[self.current_regime]["emoji"],
                "description":REGIMES[self.current_regime]["description"],
                "strategy":   REGIME_STRATEGIES[self.current_regime]["description"],
                "last_update":self.last_detected.strftime("%H:%M:%S"),
            }


# ══════════════════════════════════════════════════════════════════════════════
#  SELF-LEARNING PARAMETER STORE
# ══════════════════════════════════════════════════════════════════════════════

LEARNED_PARAMS_FILE = Path("logs/learned_params.json")

class LearnedParameters:
    """
    Stores the best-performing parameters per regime, learned from real trades.
    After enough trades in a regime, uses empirically proven params instead of presets.
    """

    MIN_TRADES_TO_LEARN = 20   # need at least 20 trades per regime before trusting learned params

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        try:
            if LEARNED_PARAMS_FILE.exists():
                with open(LEARNED_PARAMS_FILE, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save(self):
        try:
            with open(LEARNED_PARAMS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log.debug(f"[LEARN] Save failed: {e}")

    def record_trade(self, regime: str, params: dict, pnl_net: float, pnl_pct: float):
        """Record a completed trade's outcome for this regime."""
        if regime not in self.data:
            self.data[regime] = {"trades": [], "best_params": None}

        self.data[regime]["trades"].append({
            "time":    datetime.now().isoformat(),
            "params":  {k: params.get(k) for k in
                       ("rsi_buy","rsi_sell","stop_loss_pct","take_profit_pct")},
            "pnl_net": pnl_net,
            "pnl_pct": pnl_pct,
        })

        # Keep only last 200 trades per regime
        self.data[regime]["trades"] = self.data[regime]["trades"][-200:]
        self._save()

    def get_best_params(self, regime: str) -> dict:
        """
        Return empirically learned best params for this regime.
        Falls back to preset if not enough data.
        """
        regime_data = self.data.get(regime, {})
        trades      = regime_data.get("trades", [])

        if len(trades) < self.MIN_TRADES_TO_LEARN:
            log.debug(f"[LEARN] {regime}: only {len(trades)} trades, "
                     f"need {self.MIN_TRADES_TO_LEARN} — using preset")
            return REGIME_STRATEGIES[regime]

        # Group trades by parameter set and find best performing
        param_perf = defaultdict(list)
        for t in trades:
            key = f"{t['params'].get('rsi_buy',35)}/{t['params'].get('rsi_sell',65)}"
            param_perf[key].append(t["pnl_net"])

        best_key  = max(param_perf, key=lambda k: sum(param_perf[k]))
        best_pnl  = sum(param_perf[best_key])
        best_wr   = sum(1 for p in param_perf[best_key] if p > 0) / len(param_perf[best_key])

        # Find the trade that had these params
        for t in reversed(trades):
            key = f"{t['params'].get('rsi_buy',35)}/{t['params'].get('rsi_sell',65)}"
            if key == best_key:
                learned = dict(REGIME_STRATEGIES[regime])   # start from preset
                learned.update(t["params"])                  # override with learned
                log.info(f"[LEARN] {regime}: using learned params "
                        f"({len(trades)} trades, best WR={best_wr:.0%})")
                return learned

        return REGIME_STRATEGIES[regime]

    def summary(self) -> dict:
        result = {}
        for regime, data in self.data.items():
            trades = data.get("trades", [])
            if trades:
                wins = sum(1 for t in trades if t["pnl_net"] > 0)
                result[regime] = {
                    "trades":    len(trades),
                    "win_rate":  round(wins/len(trades)*100, 1),
                    "total_pnl": round(sum(t["pnl_net"] for t in trades), 4),
                    "learned":   len(trades) >= self.MIN_TRADES_TO_LEARN,
                }
        return result


# ══════════════════════════════════════════════════════════════════════════════
#  ADAPTIVE INTELLIGENCE (main orchestrator)
# ══════════════════════════════════════════════════════════════════════════════

class AdaptiveIntelligence:
    """
    Main orchestrator. Detects regime, applies optimal strategy,
    learns from outcomes, and updates config automatically.

    Runs as a background thread, checking every 30 minutes.
    """

    def __init__(self, config, exchanges: dict,
                 pool_usdt: dict, tg_send_fn=None):
        self.config      = config
        self.exchanges   = exchanges
        self.pool_usdt   = pool_usdt
        self.tg_send     = tg_send_fn
        self.detector    = RegimeDetector()
        self.learned     = LearnedParameters()
        self.last_regime = None
        self._lock       = threading.Lock()

    def _fetch_btc_data(self) -> pd.DataFrame:
        """Fetch BTC 1h candles for regime detection."""
        for ex_name, exchange in self.exchanges.items():
            try:
                df = exchange.get_candles("BTC-USDT", "1hour")
                if df is not None and len(df) >= 50:
                    return df
            except Exception:
                continue

        # Fallback: KuCoin public API
        try:
            resp = requests.get(
                "https://api.kucoin.com/api/v1/market/candles",
                params={"symbol":"BTC-USDT","type":"1hour"},
                timeout=10,
            )
            data = resp.json().get("data", [])
            data = list(reversed(data))
            df   = pd.DataFrame(data,
                                columns=["time","open","close","high","low","volume","turnover"])
            return df.astype({"open":float,"close":float,"high":float,
                              "low":float,"volume":float})
        except Exception as e:
            log.warning(f"[ADAPTIVE] BTC data fetch failed: {e}")
            return pd.DataFrame()

    def _get_market_context(self) -> dict:
        try:
            from news_aggregator import get_market_context
            return get_market_context()
        except Exception:
            return {}

    def _apply_regime_strategy(self, regime: str, strategy: dict):
        """Update config.py with regime-appropriate parameters."""
        import re
        import config as cfg

        updates = {
            "RSI_BUY":             strategy.get("rsi_buy",          cfg.RSI_BUY),
            "RSI_SELL":            strategy.get("rsi_sell",         cfg.RSI_SELL),
            "NORMAL_RSI_BUY":      strategy.get("rsi_buy",          cfg.RSI_BUY),
            "NORMAL_RSI_SELL":     strategy.get("rsi_sell",         cfg.RSI_SELL),
            "NORMAL_STOP_LOSS":    strategy.get("stop_loss_pct",    0.06),
            "NORMAL_TAKE_PROFIT":  strategy.get("take_profit_pct",  0.04),
            "AGGRESSIVE_POOL_PCT": strategy.get("aggressive_pct",   0.20),
            "MAX_DRAWDOWN_PCT":    0.10 if "BEAR" in regime else 0.15,
        }

        try:
            with open("config.py", "r", encoding="utf-8") as f:
                content = f.read()

            changed = []
            for key, val in updates.items():
                pattern = rf"^({re.escape(key)}\s*=\s*)(.+?)(\s*(?:#.*)?)$"
                new_val = re.sub(pattern, rf"\g<1>{val}\g<3>",
                                 content, flags=re.MULTILINE)
                if new_val != content:
                    content = new_val
                    old_val = getattr(cfg, key, "?")
                    if str(old_val) != str(val):
                        changed.append(f"{key}: {old_val} → {val}")

            if changed:
                # Add regime comment
                regime_comment = (
                    f"\n# Auto-adapted to {regime} regime "
                    f"({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
                )
                with open("config.py", "w", encoding="utf-8") as f:
                    f.write(content + regime_comment)

                from config_live import reload_config
                if reload_config():
                    log.info(f"[ADAPTIVE] Config updated AND live-reloaded for {regime}: {changed}")
                else:
                    log.error(f"[ADAPTIVE] Config written to disk but reload FAILED — "
                             f"bot still running on old settings: {changed}")
                return changed

        except Exception as e:
            log.error(f"[ADAPTIVE] Config update failed: {e}")

        return []

    def _notify_and_request_approval(self, old_regime: str, new_regime: str,
                                      confidence: int, strategy: dict,
                                      signals: dict):
        """Request approval before applying regime strategy change."""
        import config as cfg
        from approval_gate import request_approval

        old_strat = REGIME_STRATEGIES.get(old_regime, {})

        current_params = {
            "RSI_BUY":          cfg.RSI_BUY,
            "RSI_SELL":         cfg.RSI_SELL,
            "NORMAL_STOP_LOSS": getattr(cfg, "NORMAL_STOP_LOSS",   0.06),
            "NORMAL_TAKE_PROFIT":getattr(cfg,"NORMAL_TAKE_PROFIT", 0.04),
            "AGGRESSIVE_POOL_PCT":getattr(cfg,"AGGRESSIVE_POOL_PCT",0.20),
        }
        proposed_params = {
            "RSI_BUY":           strategy.get("rsi_buy",           cfg.RSI_BUY),
            "RSI_SELL":          strategy.get("rsi_sell",          cfg.RSI_SELL),
            "NORMAL_STOP_LOSS":  strategy.get("stop_loss_pct",     0.06),
            "NORMAL_TAKE_PROFIT":strategy.get("take_profit_pct",   0.04),
            "AGGRESSIVE_POOL_PCT":strategy.get("aggressive_pct",   0.20),
        }

        # Build what-I-learned message
        sig_lines = "\n".join([
            f"  • EMA trend:       {'Bullish ▲' if signals.get('ema_bull') else 'Bearish ▼'}",
            f"  • EMA gap:         {signals.get('ema_gap_pct',0):+.1f}%",
            f"  • Price vs MA50:   {signals.get('price_vs_ema50',0):+.1f}%",
            f"  • 10-candle move:  {signals.get('momentum_10_pct',0):+.1f}%",
            f"  • Market 24h:      {signals.get('market_change_24h',0):+.1f}%",
            f"  • Volatility:      {'Elevated ⚡' if signals.get('volatility_elevated') else 'Normal'}",
            f"  • Volume:          {'Above avg ✅' if signals.get('volume_above_avg') else 'Below avg'}",
        ])

        what_learned = (
            f"I analysed BTC 1h candles and detected a market regime shift:\n"
            f"  <b>{old_regime}</b> → <b>{new_regime}</b>\n\n"
            f"Evidence from market signals:\n{sig_lines}\n\n"
            f"Regime: {REGIMES.get(new_regime,{}).get('description','')}"
        )

        why_change = (
            f"The {new_regime} regime requires different parameters to be profitable:\n"
            f"  • {strategy.get('description','')}\n\n"
            f"Current settings were optimised for <b>{old_regime}</b>.\n"
            f"Trading with wrong regime settings reduces win rate and increases losses."
        )

        def _request():
            result = request_approval(
                change_type  = "regime_change",
                title        = f"Regime Change: {old_regime} → {new_regime}",
                what_learned = what_learned,
                why_change   = why_change,
                proposed     = proposed_params,
                current      = current_params,
                confidence   = confidence,
                config       = cfg,
                timeout_hours = 6,   # regime changes need faster response
            )
            if result == "approved":
                changes = self._apply_regime_strategy(new_regime, strategy)
                log.info(f"[ADAPTIVE] Regime strategy applied: {changes}")
            else:
                log.info(f"[ADAPTIVE] Regime change {result} — keeping current settings")

        # Run in background so bot doesn't block waiting for approval
        threading.Thread(target=_request, daemon=True,
                        name="regime_approval").start()

    def _notify_learning_milestone(self, regime: str, trade_count: int):
        """Notify when enough trades collected to start using learned params."""
        if not self.tg_send:
            return
        self.tg_send(
            f"🧠 <b>Learning Milestone!</b>\n━━━━━━━━━━━━━━━━\n"
            f"Regime: <b>{regime}</b>\n"
            f"Trades collected: <b>{trade_count}</b>\n"
            f"Bot will now use <b>empirically learned</b> parameters\n"
            f"instead of preset defaults for this regime.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def run_detection(self):
        """Run one cycle of regime detection and adaptation."""
        btc_df  = self._fetch_btc_data()
        ctx     = self._get_market_context()
        mkt_chg = ctx.get("market_change_24h", 0)
        btc_dom = ctx.get("btc_dominance", 50)

        regime, confidence, signals = self.detector.detect(btc_df, mkt_chg, btc_dom)

        # Get strategy — use learned params if available, else preset
        strategy = self.learned.get_best_params(regime)

        # Request approval for regime change (non-blocking)
        old_regime = self.last_regime
        if regime != old_regime and old_regime is not None:
            self._notify_and_request_approval(
                old_regime, regime, confidence, strategy, signals)
        elif old_regime is None:
            # First run — apply silently, no approval needed for initial setup
            self._apply_regime_strategy(regime, strategy)

        self.last_regime = regime

        log.info(f"[ADAPTIVE] Regime: {regime} ({confidence}%)  "
                f"Strategy: {strategy.get('description','')[:50]}")

        return regime, confidence, signals, strategy

    def record_trade_for_learning(self, regime: str, pnl_net: float,
                                   pnl_pct: float):
        """Call after every completed trade to feed the learning system."""
        import config as cfg
        current_params = {
            "rsi_buy":         cfg.RSI_BUY,
            "rsi_sell":        cfg.RSI_SELL,
            "stop_loss_pct":   getattr(cfg, "STOP_LOSS_PCT",   0.06),
            "take_profit_pct": getattr(cfg, "TAKE_PROFIT_PCT", 0.04),
        }
        self.learned.record_trade(regime, current_params, pnl_net, pnl_pct)

        # Check for learning milestone
        trades = self.learned.data.get(regime, {}).get("trades", [])
        if len(trades) == LearnedParameters.MIN_TRADES_TO_LEARN:
            self._notify_learning_milestone(regime, len(trades))

    def run(self, stop_event: threading.Event):
        """Background thread — detects regime every 30 minutes."""
        log.info("[ADAPTIVE] Intelligence engine started")

        # Initial detection
        try:
            self.run_detection()
        except Exception as e:
            log.warning(f"[ADAPTIVE] Initial detection failed: {e}")

        while not stop_event.wait(timeout=30 * 60):
            try:
                self.run_detection()
            except Exception as e:
                log.warning(f"[ADAPTIVE] Detection cycle failed: {e}")

        log.info("[ADAPTIVE] Intelligence engine stopped")

    @property
    def current_regime(self) -> str:
        return self.detector.current_regime

    @property
    def regime_info(self) -> dict:
        return self.detector.info

    def learning_summary(self) -> dict:
        return self.learned.summary()


# ── Singleton instance (set by bot.py on startup) ─────────────────────────
_intelligence: AdaptiveIntelligence = None

def get_intelligence() -> AdaptiveIntelligence:
    return _intelligence

def set_intelligence(inst: AdaptiveIntelligence):
    global _intelligence
    _intelligence = inst

"""
Hybrid Allocator
=================
Answers one question every time a spot BUY or futures SHORT signal fires:
"is this trade actually a better use of this capital than just staking it?"

This is the "assess spot vs futures vs staking, pick the best gain" layer.
It deliberately does NOT try to be a single do-everything optimiser that
picks between spot/futures/staking from scratch on every cycle — that
would mean re-deriving signal generation, which strategy_engine.py
(spot) and futures_manager.py (futures) already do well, each tuned to
its own product. Instead it sits ONE step downstream of both: once
either has already decided "this looks like a good trade," the hybrid
gate asks whether it's a good trade RELATIVE TO the risk-free-ish
alternative sitting right there on the same exchange — flexible
staking — and only lets the trade through if it clears that bar.
Otherwise the capital is left alone and staking_manager.py's normal
idle-capital sweep picks it up.

Why this design, not something fancier:
  - It's self-limiting: with STAKING_ENABLED=False (the default), the
    staking hurdle rate is always 0, so this gate never rejects a trade
    it wouldn't have rejected anyway. Turning HYBRID_OPTIMIZER_ENABLED
    on is safe to leave on by default for exactly this reason.
  - It uses REAL, track-recorded win rate / avg P&L per (exchange,
    symbol, side) once there's enough history (strategy_engine.py's
    PerformanceTracker, >=10 trades) — not a backtest assumption. Before
    that much history exists, it falls back to the same win-rate
    assumption (55%) strategy_engine.py's RiskRewardCalibrator already
    uses elsewhere in this bot, so a fresh bot with no history isn't
    permanently biased toward "just stake everything" for lack of data.
  - "Optimal gain, minimal loss" here means: don't take a trade whose
    own historical edge is worse than the yield already available for
    free on the same capital. It does not mean maximising raw expected
    return with no regard for risk — TP/SL/position-sizing (Kelly for
    spot, fixed for futures) still do that job; this only adds one more
    filter on top.
"""

import logging

log = logging.getLogger(__name__)

_FALLBACK_WIN_RATE = 0.55   # same assumption strategy_engine.RiskRewardCalibrator uses


def _staking_hurdle_pct(ex_name, exchange, coin: str, hold_hours: float, mode: str, config) -> float:
    """
    Returns the return (as a fraction, e.g. 0.004 = 0.4%) flexible staking
    would earn on this exchange/coin over `hold_hours` — i.e. the bar a
    trade needs to clear to be worth taking instead. Returns 0.0 (no
    hurdle at all) whenever staking isn't actually available as a real
    alternative on this exchange, so the gate never blocks a trade based
    on a yield that doesn't really exist for this capital.
    """
    if not getattr(config, "STAKING_ENABLED", False):
        return 0.0

    ex_cfg = config.EXCHANGES.get(ex_name, {})
    if not ex_cfg.get("staking_enabled", False):
        return 0.0
    if ex_name not in getattr(config, "STAKING_SUPPORTED_EXCHANGES", set()):
        return 0.0

    try:
        if not exchange.staking_supported():
            return 0.0
    except Exception:
        return 0.0

    try:
        if mode == "paper":
            apr = 0.04   # matches staking_manager.py's paper-mode illustrative APR
        else:
            apr = exchange.get_staking_apr(coin)
    except Exception as e:
        log.debug(f"[HYBRID] Could not fetch staking APR for {ex_name}:{coin}: {e}")
        return 0.0

    return max(0.0, apr) * (hold_hours / 8760.0)   # prorate annual APR to the comparison window


def evaluate_hybrid_gate(
    ex_name: str,
    exchange,
    symbol: str,
    side: str,              # "spot_long" | "futures_short" — which tracker to read
    tp_pct: float,
    sl_pct: float,
    hold_hours: float,
    mode: str,
    config,
) -> dict:
    """
    Call right before actually placing a spot buy or opening a futures
    short, once the underlying strategy has already approved the signal
    on its own terms. Returns:

        {
          "proceed":       bool   — False means skip the trade, let the
                                     capital stay/become staked instead
          "trade_edge_pct": float  — this trade's expected return over
                                      hold_hours, using real trade
                                      history once there's enough of it
          "staking_hurdle_pct": float,
          "reason":         str,
        }

    A no-op (always proceed=True) whenever config.HYBRID_OPTIMIZER_ENABLED
    is False, or staking isn't a real alternative right now (see
    _staking_hurdle_pct above) — in both cases there's nothing to gate
    against.
    """
    if not getattr(config, "HYBRID_OPTIMIZER_ENABLED", True):
        return {"proceed": True, "trade_edge_pct": None, "staking_hurdle_pct": 0.0,
                "reason": "hybrid optimiser disabled"}

    coin = symbol.split("-")[0]

    # ── This trade's own expected return, from real history if we have it ──
    from strategy_engine import get_tracker
    tracker_symbol = symbol if side == "spot_long" else f"FUT:{symbol}"
    tracker = get_tracker(ex_name, tracker_symbol)

    trade_edge_pct = tracker.expectancy_pct   # None if <10 trades recorded yet
    used_fallback = trade_edge_pct is None
    if used_fallback:
        trade_edge_pct = (_FALLBACK_WIN_RATE * tp_pct) - ((1 - _FALLBACK_WIN_RATE) * sl_pct)

    staking_hurdle_pct = _staking_hurdle_pct(ex_name, exchange, coin, hold_hours, mode, config)

    if staking_hurdle_pct <= 0.0:
        return {"proceed": True, "trade_edge_pct": trade_edge_pct,
                "staking_hurdle_pct": 0.0, "reason": "no staking alternative available"}

    min_edge = getattr(config, "HYBRID_MIN_EDGE_OVER_STAKING", 0.0)
    proceed  = trade_edge_pct >= (staking_hurdle_pct + min_edge)

    reason = (
        f"{side} {ex_name.upper()}:{coin} — trade edge {trade_edge_pct:.3%} "
        f"({'history' if not used_fallback else 'assumed 55% WR'}) vs staking "
        f"{staking_hurdle_pct:.3%} over {hold_hours:.0f}h "
        f"{'>= clears bar' if proceed else '< below staking, skipping trade'}"
    )
    log_fn = log.info if not proceed else log.debug
    log_fn(f"[HYBRID] {reason}")

    return {
        "proceed": proceed,
        "trade_edge_pct": trade_edge_pct,
        "staking_hurdle_pct": staking_hurdle_pct,
        "reason": reason,
    }

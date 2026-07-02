# Futures & Staking

Two new, both **off by default**, additive capabilities on top of the existing spot bot.
Nothing here changes spot behaviour when both are left disabled.

## What was added

### Futures (1x only — shorting/hedging, not leverage)

The bot can now open a **short** position — something spot trading can never do — using
the exact same bearish read it already uses to exit a spot position (RSI overbought +
price below its moving average). Leverage is **hard-capped at 1x** everywhere
(`config.MAX_LEVERAGE`); this is not a leveraged-sizing feature. At 1x, a futures
position's maximum loss is bounded the same way a spot position's is.

- Entry/exit/position tracking: `futures_manager.py`
- Exchange order-placement methods: `exchanges.py` (`open_futures_short`, `close_futures_position`, `get_futures_position`, `get_funding_rate`, `set_leverage`)
- A funding-rate guard refuses to open a short into a strongly negative funding rate (shorts pay longs when funding is negative).
- Fixed TP/SL/max-hold (`FUTURES_TAKE_PROFIT_PCT` / `FUTURES_STOP_LOSS_PCT` / `FUTURES_MAX_HOLD_HOURS`) — simpler and more predictable than spot's adaptive Kelly/ATR calibration, appropriate for a first cut of a new position type.
- The emergency drawdown circuit breaker (`DRAWDOWN_EMERGENCY_PCT`) now also closes any open short.

### Staking (flexible/no-lockup only)

Idle USDT that isn't currently deployed in a trade gets parked in the exchange's
flexible earn product when the APR clears a minimum threshold, and is automatically
redeemed the moment a trade signal wants that capital back.

- Decision logic: `staking_manager.py`
- Exchange methods: `exchanges.py` (`stake_flexible`, `unstake_flexible`, `get_staking_apr`, `get_staked_balance`)
- **Flexible products only** — no fixed-term/locked staking is ever used, so capital is never unavailable when a signal wants it.
- `place_buy()`'s live-order path calls `ensure_liquid()` before spending, which redeems just enough staked capital to cover the trade — and **aborts the buy** (rather than sending an order likely to fail) if even a full redemption wouldn't cover it.

## Exchange coverage

| Exchange | Futures (1x short) | Staking | Notes |
|---|---|---|---|
| Binance  | ✅ USDT-M | ✅ Simple Earn | Most liquid, best documented |
| Bybit    | ✅ linear perps | ✅ Earn | |
| OKX      | ✅ SWAP | ✅ Savings | Assumes net (non-hedge) position mode |
| KuCoin   | ✅ KuCoin Futures | ✅ Earn v3 | Verify contract multiplier before live use — see code comment |
| Gate.io  | ✅ USDT perps | ✅ Earn Uni | |
| Kraken   | ✅ Kraken Futures | ✅ Earn | **Futures needs SEPARATE API keys** from `futures.kraken.com` — your spot keys won't work. Not cross-checked against a live account; test on Kraken's demo futures environment first. |
| MEXC     | ⚠️ Contract API | ❌ | Futures implemented from docs but not independently verified — test small before trusting real size. No documented public staking API. |
| Webull   | ❌ | ❌ | Product doesn't exist on this platform |
| VirgoCX  | ❌ | ❌ | Product doesn't exist on this platform |
| Coinbase | ❌ | ❌ | Futures needs separate INTX/derivatives eligibility this account doesn't have (confirmed); staking is lockup-based on Coinbase regardless of account type, not a fit here |

## Turning it on

**Now ON by default** at both opt-in levels, for every exchange that actually supports
each product: `FUTURES_ENABLED = True` and `STAKING_ENABLED = True` globally, plus
`"futures_enabled": True` / `"staking_enabled": True` on each of Binance, Bybit, OKX,
KuCoin, Gate.io, and Kraken's `EXCHANGES` entries (MEXC gets `futures_enabled: True`
only — it has no staking product, see coverage table above; Webull/VirgoCX/Coinbase
have neither, see their comment blocks in `exchanges.py`).

This runs in **both paper and live mode** — the mode split is inside `futures_manager.py`
and `staking_manager.py` themselves (paper never calls a real exchange endpoint for
either), not in whether the feature is switched on. `PAPER_TRADING` still defaults to
`True`, so nothing places a real order until you flip that separately.

None of this does anything on an exchange until that exchange also has `"enabled": True`
and real API keys in `bot_secrets.py` — most entries ship with `"enabled": False` and
blank keys, so turning futures/staking "on" here just means the capability is armed and
ready the moment you actually connect an exchange, not that anything is trading yet.

Kraken futures additionally needs `KRAKEN_FUTURES_API_KEY` / `KRAKEN_FUTURES_API_SECRET`
in `bot_secrets.py` (see `bot_secrets.example.py`) — generated separately at
`futures.kraken.com/settings/api`. Leaving those blank is safe: `futures_supported()`
checks for actual non-empty values (not just that the config keys exist), so Kraken
futures stays correctly off until you fill them in for real, even with
`"futures_enabled": True` set.

**Recommended order:** leave `PAPER_TRADING = True` and watch the logs/Telegram for a
few days first. Paper mode exercises the full short entry/exit decision logic and logs
what staking *would* do, without ever calling a real exchange endpoint for either
feature. Only flip to live once you've seen it behave the way you expect.

## What this deliberately does not do

- No leveraged position sizing (1x is a hard ceiling, checked in code, not just config).
- No fixed-term/locked staking products.
- No options, market-making, or cross-exchange arbitrage — those are bigger, separate
  projects; see the chat summary for what's worth considering next.

---

# Hybrid AI (cost optimisation)

**Off by default** (`AI_HYBRID_MODE = False`) — live mode keeps calling real Claude/Grok
on every signal exactly as before until you opt in. Paper mode is unaffected either way
(always uses fake AI, hybrid or not).

When turned on, `ai_analyst.py` runs the free local `fake_ai.py` heuristic on every
signal first, and only escalates to a real API call when it's actually worth paying
for: a configurable random sample (`AI_REAL_USAGE_RATE`, default 15%), anything fake AI
is already confident about (`AI_MIN_CONFIDENCE_FOR_REAL`), or anything fake AI is unsure
about (a "HOLD" verdict — the borderline cases most worth a second, better opinion).
Real AI's verdict always wins when escalated. A per-coin cooldown
(`AI_REAL_CALL_COOLDOWN_SECS`, default 1h) stops a fast poll loop from re-escalating the
same coin repeatedly. `LIVE_FAKE_AI_ONLY = True` forces fake AI even in live mode, for
cost control or dry-running real-money logic before spending on API calls.

New Telegram command: **`/ai_stats`** — shows current mode, and how many signals used
fake vs real AI (i.e. how much the hybrid is actually saving).

---

# Hybrid Allocator (spot / futures vs staking — "is this trade worth it?")

**On by default** (`HYBRID_OPTIMIZER_ENABLED = True`), but self-limiting: with
`STAKING_ENABLED = False` (also the default), there's no staking yield to compare
against, so it never rejects a trade it wouldn't have rejected anyway. It only starts
actually changing behaviour once staking is ALSO turned on.

Before the bot takes a spot buy or opens a futures short, `hybrid_allocator.py` checks
whether that trade's own expected return beats what the same capital would earn just
sitting in this exchange's flexible staking product over a comparable hold time:

- Uses **real, track-recorded** win rate / P&L per (exchange, symbol, side) once there's
  enough trade history (`strategy_engine.PerformanceTracker.expectancy_pct`, needs 10+
  trades) — not a backtest guess.
- Before that much history exists, falls back to the same 55%-win-rate assumption
  `strategy_engine.py`'s `RiskRewardCalibrator` already uses elsewhere in this bot, so a
  fresh bot isn't permanently biased toward "just stake everything" for lack of data.
- If the trade's edge doesn't clear staking's prorated return (plus
  `HYBRID_MIN_EDGE_OVER_STAKING`, default 0), the trade is skipped and the capital is
  left for `staking_manager.py`'s normal idle-capital sweep to pick up instead.

This is "optimise for gain, minimise loss" applied literally: don't take a trade whose
own historical edge is worse than the yield already sitting there for free. It does not
replace TP/SL/position-sizing (Kelly for spot, fixed for futures) — those still do the
job of sizing and bounding risk on trades that DO clear the bar; this is one more filter
on top, not a replacement decision engine.

New Telegram command: **`/hybrid`** — shows whether the gate is currently active per
exchange, and the configured minimum edge.

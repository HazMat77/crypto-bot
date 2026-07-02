# Roadmap Items Added

From the "Recommended New Features" list, here's what was built, what already existed, and what was deliberately left out (with your sign-off).

## Built this round

**Multi-Timeframe Confirmation** (`strategy_engine.py`) — `SignalFilters.multi_timeframe_check()` existed in the code but was never actually called anywhere. Wired it into `evaluate_signal()`: checks the 1h RSI isn't at a directional extreme that would contradict a 15m BUY signal. Soft confidence penalty by default; set `MULTI_TIMEFRAME_REQUIRED = True` in config.py to make it a hard veto instead (left off by default since it's new — watch `/engine` for a while first to see how often it would actually fire).

**Backtester enhancements** (`backtest.py`):
- `slippage_pct` — fills are now worse than the exact close price on both entry and exit, in the direction that always hurts.
- `funding_rate_pct_per_8h` + `position_side="short"` — the backtester can now simulate the futures shorting strategy against history, not just spot longs, with funding cost accrued over the actual hold time.
- Fixed a real accounting gap while I was in there: the previous version added raw sell proceeds back to the pool without ever deducting the exit-side half of the round-trip fee, so `final_pool`/`roi_pct` understated total fee drag even though each trade's own `fees` field was correct. Now consistent.

**Walk-forward + parameter stability testing** — already existed (`strategy_optimizer.run_walk_forward_backtest`, wired into the quarterly review via `QUARTERLY_WALKFORWARD_ENABLED`). No changes needed.

**Monte Carlo with regime shifts** (`monte_carlo.py`) — `MonteCarlo.run(regime_aware=True)` now simulates a market that can drift between regimes mid-run (Markov chain, configurable persistence) instead of assuming one fixed win-rate distribution for the whole simulated path. In testing, this consistently showed a more conservative probability-of-profit than naive resampling (58% vs 97% on one test series) — which is the point: naive resampling can't represent "what if we hit a sustained bear stretch partway through."

**Portfolio VaR + stress testing** (new `portfolio_risk.py`) — builds on the existing `CorrelationChecker`. `value_at_risk()` gives both historical and parametric 1-day VaR; `stress_test()` answers "what if BTC drops 30%?" using each coin's real historical beta to the shock coin, not just raw correlation (so a correlated-but-less-volatile coin correctly shows a smaller estimated move).

**Telegram commands**:
- `/portfolio` — correlation report + VaR estimate for currently active coins, plus a heatmap image if matplotlib is installed.
- `/optimize` — runs a quick backtest + grid search on BTC-USDT, suggests settings. Runs in the background (can take a minute or two) and only suggests — never modifies config.py automatically.
- `/tax_export` — see below.
- `/hybrid`, `/ai_stats` — added in an earlier round, unrelated to this doc.

**Tax reporting export** (new `tax_export.py` + `trade_ledger.py`) — this needed more than just a CSV writer: the bot's `daily_trades`/`monthly_trades` lists are in-memory only and reset on their own schedules, so there was no durable record spanning a full tax year. Added `trade_ledger.py`, an append-only JSON-lines log (`logs/trade_ledger.jsonl`) that every closed spot AND futures trade now writes to, including entry timestamp (previously only exit time was recorded anywhere). `/tax_export` reads the full ledger and produces a CSV with Date Acquired / Date Sold / Proceeds / Cost Basis / Gain-Loss / Term (short vs long, 366-day US-simplification threshold). FIFO is trivial here since this bot never holds more than one open lot per symbol at a time — every ledger row already is one complete, correctly-ordered tax lot. **Not tax advice** — the CSV export makes this explicit; verify against your own jurisdiction's rules.

## Already covered by what exists

Nothing else — everything above that already existed (walk-forward) is called out.

## Deliberately not built (per your answers)

- **Dynamic leverage beyond 1x** — stays out; removing the 1x cap is a real increase in risk (liquidation) that the rest of this system isn't designed around.
- **Cross-exchange arbitrage / basis trading** — needs new, harder execution-risk engineering (precisely-timed simultaneous orders across two venues).
- **On-chain DeFi yield (Aave/Compound/Pendle)** — needs a wallet private key in the bot, a fundamentally different and less revocable risk than exchange API keys. Staking already covers "yield on idle capital" without that risk.
- **ML signal enhancer (LSTM/Transformer)** and **native mobile app** — both substantial standalone projects; flagged for a separate scoping conversation if you want either later.

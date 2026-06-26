"""
Deep Market Study
==================
Continuously studies the market to find patterns that improve returns.
Works in the background, learns over time, and proposes changes via
the approval gate — you always decide what gets applied.

What it studies:
  1. Win/loss patterns by time of day (crypto has peak hours)
  2. Win/loss patterns by day of week
  3. Which coins are actually making money vs losing
  4. RSI threshold effectiveness (is 35 really optimal?)
  5. Hold time analysis (are we holding too long or not long enough?)
  6. Regime correlation (which settings work best in each market phase)
  7. Fee drag analysis (are fees eating too much of profits?)
  8. Volatility timing (better to trade high or low volatility periods?)

Every week it compiles findings and proposes specific improvements.
Nothing changes without your Y approval.
"""

import json
import logging
import threading
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

log = logging.getLogger(__name__)

STUDY_LOG = Path("logs/market_study.json")
STUDY_LOG.parent.mkdir(exist_ok=True)
DECAY_HISTORY_LOG = Path("logs/strategy_decay_history.json")


class MarketStudy:

    def __init__(self, monthly_trades: list, monthly_lock: threading.Lock,
                 config, approval_gate):
        self.monthly_trades  = monthly_trades
        self.monthly_lock    = monthly_lock
        self.config          = config
        self.gate            = approval_gate
        self.last_study      = datetime.min
        self.study_results   = {}

    # ══════════════════════════════════════════════════════════════════════
    #  STRATEGY DECAY DETECTION
    # ══════════════════════════════════════════════════════════════════════

    def _load_decay_history(self) -> list:
        """Load the rolling history of weekly win rates."""
        try:
            if DECAY_HISTORY_LOG.exists():
                with open(DECAY_HISTORY_LOG) as f:
                    return json.load(f)
        except Exception as e:
            log.warning(f"[DECAY] Could not load history: {e}")
        return []

    def _save_decay_history(self, history: list):
        try:
            with open(DECAY_HISTORY_LOG, "w") as f:
                json.dump(history[-26:], f, indent=2)   # keep ~6 months of weeks
        except Exception as e:
            log.warning(f"[DECAY] Could not save history: {e}")

    def record_weekly_performance(self, win_rate_pct: float, trade_count: int) -> list:
        """
        Appends this week's win rate to the rolling history and returns
        the full updated history. Always records, even with zero trades —
        a quiet week is itself a data point, not something to skip.
        """
        history = self._load_decay_history()
        history.append({
            "date":        datetime.now().strftime("%Y-%m-%d"),
            "win_rate":    win_rate_pct,
            "trade_count": trade_count,
        })
        self._save_decay_history(history)
        return history

    def check_strategy_decay(self, history: list,
                             threshold_pct: float = 52.0,
                             consecutive_weeks: int = 4,
                             min_trades_per_week: int = 5) -> dict:
        """
        Detects sustained underperformance: win rate below threshold_pct
        for consecutive_weeks IN A ROW. A single bad week is normal
        variance and ignored — this only fires on a genuine trend.

        Weeks with fewer than min_trades_per_week are excluded from the
        streak count entirely (not enough data to judge that week one
        way or the other) rather than counted as either a pass or a fail.

        Returns:
            {
              "decayed":        bool,
              "streak_weeks":   int,
              "recent_weeks":   list (the weeks that make up the streak),
              "reason":         str,
            }
        """
        # Only consider weeks with enough trades to be statistically meaningful
        judged_weeks = [w for w in history if w["trade_count"] >= min_trades_per_week]

        if len(judged_weeks) < consecutive_weeks:
            return {
                "decayed": False, "streak_weeks": 0, "recent_weeks": [],
                "reason": f"Only {len(judged_weeks)} weeks with enough trade data "
                         f"to judge — need {consecutive_weeks} to detect decay",
            }

        recent = judged_weeks[-consecutive_weeks:]
        streak = all(w["win_rate"] < threshold_pct for w in recent)

        rates_str = ", ".join(f"{w['win_rate']:.0f}%" for w in recent)

        return {
            "decayed":      streak,
            "streak_weeks": len(recent) if streak else 0,
            "recent_weeks": recent,
            "reason": (
                f"Win rate below {threshold_pct:.0f}% for {len(recent)} consecutive weeks "
                f"({rates_str})"
                if streak else
                f"No sustained decay — win rate has been above {threshold_pct:.0f}% "
                f"at some point in the last {consecutive_weeks} judged weeks"
            ),
        }

    def trigger_ultra_conservative_fallback(self, decay_info: dict):
        """
        Pushes the bot into a deliberately defensive, low-risk configuration
        when sustained strategy decay is detected: tight stops, tight take-
        profit, fewer active coins, smaller aggressive pool. This goes
        through the SAME approval gate as everything else — even in a
        decay scenario, no config change happens without your Y/N (unless
        it qualifies for auto-apply, in which case you're still notified).
        """
        import config as cfg
        from approval_gate import request_approval

        proposed = {
            "RSI_BUY":             30,     # tighter — only strongest oversold signals
            "RSI_SELL":            70,
            "NORMAL_RSI_BUY":      30,
            "NORMAL_RSI_SELL":     70,
            "NORMAL_STOP_LOSS":    min(0.03, getattr(cfg, "MAX_STOP_LOSS_PCT", 0.04)),
            "NORMAL_TAKE_PROFIT":  0.10,    # take smaller, more reliable gains
            "AGGRESSIVE_POOL_PCT": 0.0,     # disable aggressive trading entirely
            "ENGINE_CONFIDENCE_MIN": 70,    # only the most confident signals
        }
        current = {k: getattr(cfg, k, "?") for k in proposed}

        weeks_str = ", ".join(f"{w['date']}: {w['win_rate']:.0f}%" for w in decay_info["recent_weeks"])

        what_learned = (
            f"I've detected sustained strategy decay:\n"
            f"  {decay_info['reason']}\n\n"
            f"Weekly win rates: {weeks_str}\n\n"
            f"This pattern over {decay_info['streak_weeks']} consecutive weeks suggests "
            f"current market conditions or settings aren't working well, rather than "
            f"normal week-to-week variance."
        )
        why_change = (
            "Falling back to an ultra-conservative configuration: tighter RSI band "
            "(only the most oversold/overbought signals), smaller take-profit target "
            "for more reliable wins, aggressive pool disabled entirely, and a higher "
            "confidence bar before any trade fires. This is a defensive posture, not "
            "a permanent change — once performance recovers you can manually return "
            "to normal settings, or run the optimizer/backtester to find better ones."
        )

        log.warning(f"[DECAY] Sustained decay detected — proposing ultra-conservative fallback: "
                   f"{decay_info['reason']}")

        result = request_approval(
            change_type   = "strategy_decay",
            title         = "Strategy Decay Detected — Conservative Fallback",
            what_learned  = what_learned,
            why_change    = why_change,
            proposed      = proposed,
            current       = current,
            confidence    = 85,   # high confidence this IS decay — the data is unambiguous
            config        = cfg,
            timeout_hours = 24,   # decay is urgent enough to ask sooner than the usual 48h
        )

        if result == "approved":
            self._apply_proposals(proposed)
            log.info("[DECAY] ✅ Conservative fallback applied")
        else:
            log.info(f"[DECAY] Conservative fallback {result} — no changes made")

    # ══════════════════════════════════════════════════════════════════════
    #  STUDY METHODS
    # ══════════════════════════════════════════════════════════════════════

    def study_time_patterns(self, trades: list) -> dict:
        """Find which hours and days produce the best results."""
        hourly = defaultdict(list)
        daily  = defaultdict(list)

        for t in trades:
            try:
                # Parse time from trade
                time_str = t.get("time", "")
                date_str = t.get("date", datetime.now().strftime("%Y-%m-%d"))
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                hourly[dt.hour].append(t.get("pnl_net", 0))
                daily[dt.weekday()].append(t.get("pnl_net", 0))
            except Exception:
                continue

        hour_perf = {}
        for h, pnls in hourly.items():
            if len(pnls) >= 3:
                hour_perf[h] = {
                    "trades": len(pnls),
                    "win_rate": sum(1 for p in pnls if p > 0) / len(pnls),
                    "avg_pnl":  statistics.mean(pnls),
                }

        day_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        day_perf  = {}
        for d, pnls in daily.items():
            if len(pnls) >= 2:
                day_perf[day_names[d]] = {
                    "trades": len(pnls),
                    "win_rate": sum(1 for p in pnls if p > 0) / len(pnls),
                    "avg_pnl":  statistics.mean(pnls),
                }

        best_hours = sorted(hour_perf.items(),
                           key=lambda x: x[1]["win_rate"], reverse=True)[:3]
        worst_hours = sorted(hour_perf.items(),
                            key=lambda x: x[1]["win_rate"])[:3]

        return {
            "best_hours":   [f"{h:02d}:00 ({v['win_rate']:.0%} WR)"
                            for h, v in best_hours],
            "worst_hours":  [f"{h:02d}:00 ({v['win_rate']:.0%} WR)"
                            for h, v in worst_hours],
            "best_day":     max(day_perf.items(),
                               key=lambda x: x[1]["win_rate"])[0] if day_perf else "?",
            "worst_day":    min(day_perf.items(),
                               key=lambda x: x[1]["win_rate"])[0] if day_perf else "?",
            "hourly":       hour_perf,
            "daily":        day_perf,
        }

    def study_coin_performance(self, trades: list) -> dict:
        """Which coins are actually making money?"""
        coin_stats = defaultdict(lambda: {"trades":0,"wins":0,"net":0.0,"fees":0.0})

        for t in trades:
            coin = t.get("coin", "?")
            pnl  = t.get("pnl_net", 0)
            coin_stats[coin]["trades"] += 1
            coin_stats[coin]["fees"]   += t.get("fees", 0)
            coin_stats[coin]["net"]    += pnl
            if pnl > 0:
                coin_stats[coin]["wins"] += 1

        results = {}
        for coin, s in coin_stats.items():
            if s["trades"] >= 3:
                results[coin] = {
                    "trades":   s["trades"],
                    "win_rate": round(s["wins"] / s["trades"] * 100, 1),
                    "net_pnl":  round(s["net"], 4),
                    "avg_pnl":  round(s["net"] / s["trades"], 4),
                    "fee_drag": round(s["fees"] / s["trades"], 4),
                }

        profitable   = [c for c, s in results.items() if s["net_pnl"] > 0]
        losing       = [c for c, s in results.items() if s["net_pnl"] <= 0]

        return {
            "profitable":     profitable,
            "consistently_losing": [c for c in losing
                                   if results[c]["win_rate"] < 45],
            "details":        results,
        }

    def study_hold_times(self, trades: list) -> dict:
        """Are we holding positions the optimal amount of time?"""
        exit_analysis = defaultdict(lambda: {"trades":0,"net":0.0})

        for t in trades:
            reason = t.get("exit_reason", t.get("exit", "unknown"))
            pnl    = t.get("pnl_net", 0)
            exit_analysis[reason]["trades"] += 1
            exit_analysis[reason]["net"]    += pnl

        results = {}
        for reason, data in exit_analysis.items():
            if data["trades"] > 0:
                results[reason] = {
                    "count":   data["trades"],
                    "avg_pnl": round(data["net"] / data["trades"], 4),
                    "total":   round(data["net"], 4),
                }

        # Find if max_hold is firing too often (suggests exits are being forced)
        max_hold_count = results.get("max_hold", {}).get("count", 0)
        total_trades   = len(trades)
        max_hold_rate  = max_hold_count / total_trades if total_trades > 0 else 0

        # Find if stop_loss is the primary exit (suggests entries are wrong)
        sl_count = results.get("stop_loss", {}).get("count", 0)
        sl_rate  = sl_count / total_trades if total_trades > 0 else 0

        return {
            "by_exit_reason":    results,
            "max_hold_rate":     round(max_hold_rate * 100, 1),
            "stop_loss_rate":    round(sl_rate * 100, 1),
            "take_profit_rate":  round(results.get("take_profit",{}).get("count",0) /
                                      total_trades * 100 if total_trades > 0 else 0, 1),
            "recommendation": (
                "Reduce MAX_HOLD_HOURS — too many forced exits"
                if max_hold_rate > 0.25 else
                "Tighten STOP_LOSS_PCT — too many stop-outs"
                if sl_rate > 0.40 else
                "Exit distribution is healthy"
            ),
        }

    def study_rsi_effectiveness(self, trades: list) -> dict:
        """
        Analyse whether the current RSI thresholds are optimal.
        Groups trades by entry RSI and finds which levels had best outcomes.
        """
        # We don't store entry RSI in trades by default, but we can analyse
        # by pool type and correlate with win rate
        normal_trades = [t for t in trades if t.get("pool_type","normal") == "normal"]
        aggr_trades   = [t for t in trades if t.get("pool_type","aggressive") == "aggressive"]

        def pool_stats(pool_trades):
            if not pool_trades:
                return {}
            wins = [t for t in pool_trades if t.get("pnl_net",0) > 0]
            return {
                "trades":   len(pool_trades),
                "win_rate": round(len(wins)/len(pool_trades)*100, 1),
                "avg_pnl":  round(statistics.mean(t.get("pnl_net",0) for t in pool_trades), 4),
                "total":    round(sum(t.get("pnl_net",0) for t in pool_trades), 4),
            }

        return {
            "normal_pool":     pool_stats(normal_trades),
            "aggressive_pool": pool_stats(aggr_trades),
            "better_pool":     (
                "aggressive" if (aggr_trades and normal_trades and
                                pool_stats(aggr_trades)["win_rate"] >
                                pool_stats(normal_trades)["win_rate"])
                else "normal"
            ),
        }

    def study_fee_drag(self, trades: list) -> dict:
        """How much are fees eating into profits?"""
        if not trades:
            return {}

        total_gross = sum(t.get("pnl_gross", 0) for t in trades)
        total_fees  = sum(t.get("fees", 0)      for t in trades)
        total_net   = sum(t.get("pnl_net",  0)  for t in trades)
        fee_drag    = total_fees / abs(total_gross) if total_gross != 0 else 0

        avg_trade_size = statistics.mean(t.get("spent", 10) for t in trades)

        return {
            "total_gross":     round(total_gross, 4),
            "total_fees":      round(total_fees,  4),
            "total_net":       round(total_net,   4),
            "fee_drag_pct":    round(fee_drag * 100, 2),
            "avg_trade_size":  round(avg_trade_size, 2),
            "recommendation": (
                "Increase trade size — fees too high relative to gains"
                if fee_drag > 0.25 and avg_trade_size < 20
                else
                "Fee drag is acceptable"
            ),
        }

    # ══════════════════════════════════════════════════════════════════════
    #  PROPOSAL GENERATOR
    # ══════════════════════════════════════════════════════════════════════

    def generate_proposals(self, findings: dict) -> dict:
        """
        Translate study findings into specific parameter proposals.
        Only proposes changes when evidence is strong.
        """
        import config as cfg
        proposals = {}
        reasons   = []

        coin_study  = findings.get("coins", {})
        hold_study  = findings.get("holds", {})
        fee_study   = findings.get("fees",  {})
        pool_study  = findings.get("rsi",   {})

        # ── Proposal 1: Drop consistently losing coins ─────────────────
        losing_coins = coin_study.get("consistently_losing", [])
        if losing_coins:
            reasons.append(
                f"• Coins {losing_coins} have <45% win rate over "
                f"multiple trades — recommend excluding from active list"
            )
            # This is informational — we can't auto-exclude from config
            # but we flag it for the user
            proposals["LOSING_COINS_NOTE"] = ", ".join(losing_coins)

        # ── Proposal 2: Adjust MAX_HOLD_HOURS if forced exits too common
        max_hold_rate = hold_study.get("max_hold_rate", 0)
        if max_hold_rate > 25:
            current_mh = getattr(cfg, "MAX_HOLD_HOURS", 48)
            new_mh     = max(12, current_mh - 12)
            proposals["NORMAL_MAX_HOLD_HOURS"] = new_mh
            reasons.append(
                f"• {max_hold_rate:.0f}% of trades hit max hold time "
                f"— reducing from {current_mh}h to {new_mh}h to improve capital efficiency"
            )

        # ── Proposal 3: Adjust aggressive pool allocation ──────────────
        better_pool  = pool_study.get("better_pool", "normal")
        aggr_stats   = pool_study.get("aggressive_pool", {})
        normal_stats = pool_study.get("normal_pool", {})
        current_aggr = getattr(cfg, "AGGRESSIVE_POOL_PCT", 0.20)

        if (better_pool == "aggressive" and
            aggr_stats.get("win_rate",0) > normal_stats.get("win_rate",0) + 10 and
            current_aggr < 0.35):
            new_aggr = min(0.35, current_aggr + 0.05)
            proposals["AGGRESSIVE_POOL_PCT"] = new_aggr
            reasons.append(
                f"• Aggressive pool win rate "
                f"({aggr_stats.get('win_rate',0):.0f}%) outperforms normal "
                f"({normal_stats.get('win_rate',0):.0f}%) — "
                f"increasing aggressive allocation from "
                f"{current_aggr*100:.0f}% to {new_aggr*100:.0f}%"
            )
        elif (better_pool == "normal" and
              normal_stats.get("win_rate",0) > aggr_stats.get("win_rate",0) + 10 and
              current_aggr > 0.10):
            new_aggr = max(0.10, current_aggr - 0.05)
            proposals["AGGRESSIVE_POOL_PCT"] = new_aggr
            reasons.append(
                f"• Normal pool outperforming aggressive — "
                f"reducing aggressive allocation to {new_aggr*100:.0f}%"
            )

        # ── Proposal 4: Stop loss adjustment if SL rate is high ────────
        sl_rate = hold_study.get("stop_loss_rate", 0)
        if sl_rate > 40:
            current_sl = getattr(cfg, "NORMAL_STOP_LOSS", 0.06)
            new_sl     = min(0.09, current_sl + 0.01)
            proposals["NORMAL_STOP_LOSS"] = new_sl
            reasons.append(
                f"• Stop loss triggering on {sl_rate:.0f}% of trades — "
                f"widening from {current_sl*100:.0f}% to {new_sl*100:.0f}% "
                f"to reduce noise-triggered exits"
            )
        elif sl_rate < 10 and sl_rate > 0:
            current_sl = getattr(cfg, "NORMAL_STOP_LOSS", 0.06)
            new_sl     = max(0.03, current_sl - 0.01)
            proposals["NORMAL_STOP_LOSS"] = new_sl
            reasons.append(
                f"• Stop loss only triggering {sl_rate:.0f}% of the time — "
                f"tightening from {current_sl*100:.0f}% to {new_sl*100:.0f}% "
                f"to better protect capital"
            )

        return proposals, reasons

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN STUDY RUNNER
    # ══════════════════════════════════════════════════════════════════════

    def run_study(self) -> dict:
        """Run all studies and return compiled findings."""
        with self.monthly_lock:
            trades = list(self.monthly_trades)

        if len(trades) < 15:
            log.info(f"[STUDY] Only {len(trades)} trades — need 15+ for meaningful study")
            return {}

        log.info(f"[STUDY] Running deep market study on {len(trades)} trades...")

        findings = {
            "timestamp": datetime.now().isoformat(),
            "trade_count": len(trades),
            "time":   self.study_time_patterns(trades),
            "coins":  self.study_coin_performance(trades),
            "holds":  self.study_hold_times(trades),
            "rsi":    self.study_rsi_effectiveness(trades),
            "fees":   self.study_fee_drag(trades),
        }

        # Save study results
        try:
            with open(STUDY_LOG, "w") as f:
                json.dump(findings, f, indent=2, default=str)
        except Exception:
            pass

        self.study_results = findings
        return findings

    def format_study_for_telegram(self, findings: dict) -> str:
        """Format study findings into a readable Telegram message."""
        tc   = findings.get("trade_count", 0)
        time = findings.get("time", {})
        coins= findings.get("coins", {})
        holds= findings.get("holds", {})
        fees = findings.get("fees",  {})
        rsi  = findings.get("rsi",   {})

        lines = [f"📚 Studied <b>{tc}</b> trades\n"]

        # Time patterns
        if time.get("best_hours"):
            lines.append(f"⏰ <b>Best trading hours:</b> {', '.join(time['best_hours'][:2])}")
        if time.get("best_day"):
            lines.append(f"📅 <b>Best day:</b> {time['best_day']}  "
                        f"<b>Worst day:</b> {time.get('worst_day','?')}")

        # Coin performance
        if coins.get("profitable"):
            lines.append(f"✅ <b>Profitable coins:</b> {', '.join(coins['profitable'][:5])}")
        if coins.get("consistently_losing"):
            lines.append(f"❌ <b>Consistently losing:</b> "
                        f"{', '.join(coins['consistently_losing'][:3])}")

        # Hold analysis
        lines.append(f"\n📊 <b>Exit Analysis:</b>")
        lines.append(f"  Take-profit hits: {holds.get('take_profit_rate',0):.0f}%")
        lines.append(f"  Stop-loss hits:   {holds.get('stop_loss_rate',0):.0f}%")
        lines.append(f"  Max-hold exits:   {holds.get('max_hold_rate',0):.0f}%")
        if holds.get("recommendation") != "Exit distribution is healthy":
            lines.append(f"  ⚠️ {holds.get('recommendation','')}")

        # Pool comparison
        n_wr = rsi.get("normal_pool",{}).get("win_rate", 0)
        a_wr = rsi.get("aggressive_pool",{}).get("win_rate", 0)
        if n_wr and a_wr:
            lines.append(f"\n💰 <b>Pool Performance:</b>")
            lines.append(f"  Normal (80%):     {n_wr:.0f}% win rate")
            lines.append(f"  Aggressive (20%): {a_wr:.0f}% win rate")

        # Fee drag
        lines.append(f"\n💸 <b>Fee drag:</b> {fees.get('fee_drag_pct',0):.1f}% of gross profits")
        if fees.get("recommendation") != "Fee drag is acceptable":
            lines.append(f"  ⚠️ {fees.get('recommendation','')}")

        return "\n".join(lines)

    def run_and_propose(self):
        """Full pipeline: record decay history → check for decay → study → propose."""
        findings  = self.run_study()

        # ── Strategy decay check — runs even on quiet weeks ─────────────────
        # This happens regardless of whether run_study() found enough trades
        # for a full deep-dive, since a quiet week (near-zero trades) is
        # itself sometimes a symptom of decay (engine confidence too high,
        # nothing clearing the bar) worth recording.
        with self.monthly_lock:
            week_trades = list(self.monthly_trades)
        week_wins  = sum(1 for t in week_trades if t.get("pnl_net", 0) > 0)
        week_wr    = round(week_wins / len(week_trades) * 100, 1) if week_trades else 0.0

        history    = self.record_weekly_performance(week_wr, len(week_trades))
        decay_info = self.check_strategy_decay(history)

        if decay_info["decayed"]:
            self.trigger_ultra_conservative_fallback(decay_info)
            # Still fall through to the normal study below — decay and the
            # weekly study are independent checks, not mutually exclusive.

        if not findings:
            return

        proposals, reasons = self.generate_proposals(findings)
        if not proposals or all(k.endswith("_NOTE") for k in proposals):
            log.info("[STUDY] No parameter changes needed this week")
            return

        # Build context for approval message
        import config as cfg
        current = {
            k: getattr(cfg, k, "?") for k in proposals
            if not k.endswith("_NOTE")
        }

        what_learned = self.format_study_for_telegram(findings)
        why_change   = "\n".join(f"• {r}" for r in reasons)

        # Estimate overall win rate for confidence
        trades = findings.get("trade_count", 0)
        fees   = findings.get("fees", {})
        total_net = fees.get("total_net", 0)
        confidence = min(90, 50 + trades // 5)

        log.info(f"[STUDY] Proposing {len(proposals)} changes via approval gate")

        from approval_gate import request_approval
        result = request_approval(
            change_type  = "weekly_study",
            title        = "Weekly Market Study Results",
            what_learned = what_learned,
            why_change   = why_change,
            proposed     = {k:v for k,v in proposals.items() if not k.endswith("_NOTE")},
            current      = current,
            confidence   = confidence,
            config       = cfg,
            timeout_hours= 48,
        )

        if result == "approved":
            self._apply_proposals(proposals)
            log.info("[STUDY] ✅ Study proposals applied")
        else:
            log.info(f"[STUDY] Proposals {result} — no changes made")

    def _apply_proposals(self, proposals: dict):
        """Apply approved proposals to config.py."""
        import re
        try:
            with open("config.py", "r", encoding="utf-8") as f:
                content = f.read()

            for key, val in proposals.items():
                if key.endswith("_NOTE"):
                    continue
                pattern = rf"^({re.escape(key)}\s*=\s*)(.+?)(\s*(?:#.*)?)$"
                content = re.sub(pattern, rf"\g<1>{val}\g<3>",
                                 content, flags=re.MULTILINE)

            content += f"\n# Study update applied {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            with open("config.py", "w", encoding="utf-8") as f:
                f.write(content)

            from config_live import reload_config
            if reload_config():
                log.info(f"[STUDY] Config updated AND live-reloaded: {list(proposals.keys())}")
            else:
                log.error(f"[STUDY] Config written to disk but reload FAILED — "
                         f"bot still running on old settings: {list(proposals.keys())}")
        except Exception as e:
            log.error(f"[STUDY] Apply failed: {e}")

    def run(self, stop_event: threading.Event):
        """Background thread — runs deep study weekly."""
        log.info("[STUDY] Deep market study engine started — weekly analysis")

        while not stop_event.wait(timeout=7 * 24 * 3600):
            try:
                self.run_and_propose()
            except Exception as e:
                log.error(f"[STUDY] Weekly study failed: {e}")

        log.info("[STUDY] Deep market study engine stopped")

"""
Monte Carlo Simulation
=======================
Runs thousands of randomised simulations based on your backtested
trade distribution to estimate realistic outcome ranges.

Instead of a single backtest result, Monte Carlo shows:
  - Best case (top 10% of runs)
  - Worst case (bottom 10% of runs)
  - Most likely outcome (median)
  - Probability of profit
  - Probability of ruin (pool dropping below minimum)
  - 95% confidence interval for final pool value

Usage:
    from monte_carlo import MonteCarlo
    mc = MonteCarlo(trades=backtest_result["trades"], starting_pool=100)
    mc.run(simulations=1000)
    mc.print_summary()
    mc.plot()  # optional — requires matplotlib
"""

import random
import logging
import statistics
from collections import defaultdict

log = logging.getLogger(__name__)


class MonteCarlo:

    def __init__(self, trades: list, starting_pool: float = 100.0,
                 trade_size: float = 10.0, fee_rate: float = 0.001,
                 ruin_threshold: float = 0.50):
        """
        Args:
            trades:          List of trade dicts from Backtester.run()["trades"]
            starting_pool:   Starting USDT balance
            trade_size:      USDT per trade
            fee_rate:        Exchange fee rate (0.001 = 0.1%)
            ruin_threshold:  Pool drops below this fraction = "ruin" (default 50%)
        """
        if not trades:
            raise ValueError("No trades provided — run backtester first")

        self.trades          = trades
        self.starting_pool   = starting_pool
        self.trade_size      = trade_size
        self.fee_rate        = fee_rate
        self.ruin_level      = starting_pool * ruin_threshold
        self.results         = []

        # Extract trade outcomes as percentage returns
        self.trade_returns = [t["pct_change"] / 100 for t in trades]
        self.win_rate      = len([t for t in trades if t["pnl_net"] >= 0]) / len(trades)

        log.info(f"[MC] Loaded {len(trades)} trades | Win rate: {self.win_rate*100:.1f}%")

    def _run_single(self, trades_per_run: int) -> dict:
        """Simulate one path by sampling from historical trade distribution."""
        pool       = self.starting_pool
        equity     = [pool]
        peak       = pool
        max_dd     = 0.0

        for _ in range(trades_per_run):
            if pool < self.ruin_level:
                break

            # Sample a random historical trade return
            ret     = random.choice(self.trade_returns)
            size    = min(self.trade_size, pool * 0.10)
            fees    = size * self.fee_rate * 2
            pnl     = size * ret - fees
            pool   += pnl
            pool    = max(pool, 0.0)

            equity.append(pool)
            peak   = max(peak, pool)
            dd     = (peak - pool) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return {
            "final_pool": pool,
            "roi_pct":    (pool - self.starting_pool) / self.starting_pool * 100,
            "max_dd":     max_dd * 100,
            "ruined":     pool < self.ruin_level,
            "equity":     equity,
        }

    def run(self, simulations: int = 1000,
            trades_per_sim: int = None) -> list:
        """
        Run N Monte Carlo simulations.

        Args:
            simulations:    Number of random paths to simulate
            trades_per_sim: Trades per simulation (default: same as backtest)
        """
        n_trades = trades_per_sim or len(self.trades)
        log.info(f"[MC] Running {simulations:,} simulations "
                 f"({n_trades} trades each)...")

        self.results = []
        for i in range(simulations):
            r = self._run_single(n_trades)
            self.results.append(r)

        log.info(f"[MC] Complete. Median final pool: "
                 f"${statistics.median(r['final_pool'] for r in self.results):.2f}")
        return self.results

    def summary(self) -> dict:
        """Calculate summary statistics from simulation results."""
        if not self.results:
            raise RuntimeError("Run .run() first")

        final_pools = sorted(r["final_pool"] for r in self.results)
        rois        = [r["roi_pct"]   for r in self.results]
        drawdowns   = [r["max_dd"]    for r in self.results]
        n           = len(self.results)

        profit_runs = sum(1 for r in self.results if r["final_pool"] > self.starting_pool)
        ruin_runs   = sum(1 for r in self.results if r["ruined"])

        p5  = final_pools[int(n * 0.05)]
        p25 = final_pools[int(n * 0.25)]
        p50 = final_pools[int(n * 0.50)]
        p75 = final_pools[int(n * 0.75)]
        p95 = final_pools[int(n * 0.95)]

        return {
            "simulations":       n,
            "starting_pool":     self.starting_pool,
            "win_rate_pct":      round(self.win_rate * 100, 1),
            "prob_profit_pct":   round(profit_runs / n * 100, 1),
            "prob_ruin_pct":     round(ruin_runs   / n * 100, 1),
            "worst_case_p5":     round(p5,  2),
            "lower_quartile":    round(p25, 2),
            "median":            round(p50, 2),
            "upper_quartile":    round(p75, 2),
            "best_case_p95":     round(p95, 2),
            "avg_roi_pct":       round(statistics.mean(rois), 2),
            "median_roi_pct":    round(statistics.median(rois), 2),
            "avg_max_drawdown":  round(statistics.mean(drawdowns), 2),
            "worst_drawdown":    round(max(drawdowns), 2),
        }

    def print_summary(self):
        s = self.summary()
        print(f"\n{'═'*60}")
        print(f"  MONTE CARLO RESULTS  ({s['simulations']:,} simulations)")
        print(f"  Starting pool: ${s['starting_pool']:.2f} | "
              f"Win rate: {s['win_rate_pct']}%")
        print(f"{'═'*60}")
        print(f"  Probability of profit:    {s['prob_profit_pct']}%")
        print(f"  Probability of ruin:      {s['prob_ruin_pct']}%")
        print(f"  {'─'*56}")
        print(f"  Worst case  (5th pct):    ${s['worst_case_p5']:.2f}  "
              f"({(s['worst_case_p5']/s['starting_pool']-1)*100:+.1f}%)")
        print(f"  Lower quartile (25th):    ${s['lower_quartile']:.2f}")
        print(f"  Median outcome (50th):    ${s['median']:.2f}  "
              f"({(s['median']/s['starting_pool']-1)*100:+.1f}%)")
        print(f"  Upper quartile (75th):    ${s['upper_quartile']:.2f}")
        print(f"  Best case   (95th pct):   ${s['best_case_p95']:.2f}  "
              f"({(s['best_case_p95']/s['starting_pool']-1)*100:+.1f}%)")
        print(f"  {'─'*56}")
        print(f"  Avg ROI:                  {s['avg_roi_pct']:+.2f}%")
        print(f"  Avg max drawdown:         {s['avg_max_drawdown']:.1f}%")
        print(f"  Worst drawdown seen:      {s['worst_drawdown']:.1f}%")

        verdict = ("✅ Strategy shows positive edge" if s["prob_profit_pct"] >= 60
                   else "⚠️  Marginal — monitor closely" if s["prob_profit_pct"] >= 50
                   else "❌ Strategy likely unprofitable")
        print(f"  {'─'*56}")
        print(f"  Verdict: {verdict}")
        print(f"{'═'*60}")

    def plot(self, max_paths: int = 200):
        """Plot simulation paths and distribution."""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
            import numpy as np

            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

            # Left: equity paths
            sample = random.sample(self.results, min(max_paths, len(self.results)))
            for r in sample:
                color = "#1D9E75" if r["final_pool"] >= self.starting_pool else "#D85A30"
                ax1.plot(r["equity"], alpha=0.1, color=color, linewidth=0.8)

            # Median line
            max_len  = max(len(r["equity"]) for r in self.results)
            medians  = []
            for i in range(max_len):
                vals = [r["equity"][i] for r in self.results if i < len(r["equity"])]
                medians.append(statistics.median(vals))
            ax1.plot(medians, color="navy", linewidth=2, label="Median", zorder=5)
            ax1.axhline(self.starting_pool, color="gray", linestyle="--",
                       alpha=0.7, label="Start")
            ax1.set_title("Simulation Paths")
            ax1.set_xlabel("Trades")
            ax1.set_ylabel("Pool (USDT)")
            ax1.legend(fontsize=9)

            # Right: final pool distribution
            finals = [r["final_pool"] for r in self.results]
            ax2.hist(finals, bins=50, color="#185FA5", alpha=0.7, edgecolor="white")
            ax2.axvline(self.starting_pool, color="red", linestyle="--",
                       linewidth=2, label=f"Start ${self.starting_pool:.0f}")
            ax2.axvline(statistics.median(finals), color="green", linestyle="-",
                       linewidth=2, label=f"Median ${statistics.median(finals):.0f}")
            ax2.set_title("Final Pool Distribution")
            ax2.set_xlabel("Final Pool (USDT)")
            ax2.set_ylabel("Frequency")
            ax2.legend(fontsize=9)

            plt.suptitle(f"Monte Carlo — {len(self.results):,} simulations | "
                        f"{len(self.trades)} trades sampled", fontsize=11)
            plt.tight_layout()

            fname = "monte_carlo.png"
            plt.savefig(fname, dpi=150)
            plt.close()
            print(f"  Chart saved: {fname}")
        except ImportError:
            print("  (pip install matplotlib for MC chart)")
        except Exception as e:
            print(f"  Chart error: {e}")

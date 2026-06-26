"""
Portfolio Correlation Checker
===============================
Checks how correlated your active trading coins are.

Why it matters:
  If BTC, ETH, and SOL all move together (correlation > 0.8),
  trading all three is essentially the same bet made 3 times.
  High correlation = concentrated risk even with many coins.

  Ideally you want low-correlation coins so that when one drops,
  another might hold or rise — true diversification.

Usage:
    from portfolio_correlation import CorrelationChecker
    cc = CorrelationChecker()
    cc.check(["BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT"])
    cc.print_report()
    cc.suggest_diversification(max_coins=10)
"""

import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


class CorrelationChecker:

    def __init__(self, lookback_days: int = 30):
        self.lookback_days = lookback_days
        self.prices        = {}     # { symbol: pd.Series of close prices }
        self.corr_matrix   = None

    def _fetch_prices(self, symbol: str) -> pd.Series:
        """Fetch daily close prices for correlation calculation."""
        try:
            end_time   = int(datetime.now().timestamp())
            start_time = int((datetime.now() - timedelta(days=self.lookback_days)).timestamp())
            resp = requests.get(
                "https://api.kucoin.com/api/v1/market/candles",
                params={"symbol": symbol, "type": "1day",
                        "startAt": start_time, "endAt": end_time},
                timeout=10,
            )
            data = resp.json().get("data", [])
            if not data:
                return pd.Series(dtype=float)
            data = list(reversed(data))
            closes = [float(c[2]) for c in data]   # index 2 = close
            times  = [int(c[0])   for c in data]
            return pd.Series(closes,
                            index=pd.to_datetime(times, unit="s"),
                            name=symbol.split("-")[0])
        except Exception as e:
            log.warning(f"[CORR] Failed to fetch {symbol}: {e}")
            return pd.Series(dtype=float)

    def check(self, symbols: list) -> pd.DataFrame:
        """Fetch prices and compute correlation matrix."""
        log.info(f"[CORR] Checking correlation for {len(symbols)} coins "
                f"over {self.lookback_days} days...")

        price_data = {}
        for sym in symbols:
            s = self._fetch_prices(sym)
            if not s.empty:
                price_data[sym.split("-")[0]] = s

        if len(price_data) < 2:
            log.warning("[CORR] Need at least 2 coins to check correlation")
            return pd.DataFrame()

        df              = pd.DataFrame(price_data)
        df              = df.dropna()
        returns         = df.pct_change().dropna()
        self.corr_matrix = returns.corr()
        self.returns_df  = returns

        return self.corr_matrix

    def print_report(self):
        if self.corr_matrix is None:
            print("Run .check() first")
            return

        coins = list(self.corr_matrix.columns)
        print(f"\n{'═'*65}")
        print(f"  PORTFOLIO CORRELATION REPORT  ({self.lookback_days} day lookback)")
        print(f"{'═'*65}")
        print(f"  Scale: +1.0 = perfect correlation (same moves)")
        print(f"         0.0  = no correlation (independent)")
        print(f"        -1.0  = inverse correlation (opposite moves)")
        print(f"  Target: keep pairs below 0.75 for real diversification")
        print()

        high_corr_pairs = []
        low_corr_pairs  = []

        for i, c1 in enumerate(coins):
            for j, c2 in enumerate(coins):
                if j <= i:
                    continue
                val = self.corr_matrix.loc[c1, c2]
                pair = f"{c1}/{c2}"
                if val >= 0.80:
                    high_corr_pairs.append((pair, val))
                elif val <= 0.40:
                    low_corr_pairs.append((pair, val))

        high_corr_pairs.sort(key=lambda x: x[1], reverse=True)
        low_corr_pairs.sort(key=lambda x: x[1])

        if high_corr_pairs:
            print("  ⚠️  HIGH CORRELATION pairs (>0.80 — concentrated risk):")
            for pair, val in high_corr_pairs[:10]:
                bar = "█" * int(val * 10)
                print(f"    {pair:<20} {val:.2f}  {bar}")

        if low_corr_pairs:
            print(f"\n  ✅ LOW CORRELATION pairs (<0.40 — good diversification):")
            for pair, val in low_corr_pairs[:10]:
                bar = "░" * int(abs(val) * 10)
                print(f"    {pair:<20} {val:.2f}  {bar}")

        # Overall portfolio diversity score
        n     = len(coins)
        pairs = [(i, j) for i in range(n) for j in range(n) if i < j]
        if pairs:
            avg_corr = np.mean([abs(self.corr_matrix.iloc[i, j]) for i, j in pairs])
            score    = max(0, 100 - int(avg_corr * 100))
            verdict  = ("✅ Well diversified" if avg_corr < 0.5
                       else "⚠️  Moderately correlated" if avg_corr < 0.75
                       else "❌ Highly correlated — add uncorrelated coins")
            print(f"\n  Average correlation: {avg_corr:.2f}")
            print(f"  Diversity score:     {score}/100")
            print(f"  Verdict:             {verdict}")

        print(f"{'═'*65}")

    def suggest_diversification(self, candidate_symbols: list = None,
                                 max_coins: int = 10) -> list:
        """
        Given current portfolio, suggest which coins to add/remove
        to maximise diversification.
        Returns suggested portfolio of max_coins symbols.
        """
        if self.corr_matrix is None:
            print("Run .check() first")
            return []

        current_coins = list(self.corr_matrix.columns)

        # Find coins that are least correlated with the existing portfolio
        if not candidate_symbols:
            return current_coins

        suggestions = []
        for sym in candidate_symbols:
            coin = sym.split("-")[0]
            if coin in current_coins:
                continue
            # Fetch price series for candidate
            s = self._fetch_prices(sym)
            if s.empty:
                continue
            # Calculate average correlation with current portfolio
            returns = s.pct_change().dropna()
            corrs   = []
            for existing in current_coins:
                if existing in self.returns_df.columns:
                    aligned = pd.concat(
                        [returns, self.returns_df[existing]], axis=1
                    ).dropna()
                    if len(aligned) > 5:
                        c = aligned.corr().iloc[0, 1]
                        corrs.append(c)
            if corrs:
                avg = np.mean([abs(c) for c in corrs])
                suggestions.append((sym, avg))

        suggestions.sort(key=lambda x: x[1])   # lowest correlation first

        print(f"\n  DIVERSIFICATION SUGGESTIONS (lowest correlation to current portfolio):")
        for sym, avg_c in suggestions[:5]:
            print(f"    + {sym:<15} avg corr = {avg_c:.2f}")

        return [s for s, _ in suggestions[:max(0, max_coins - len(current_coins))]]

    def plot(self):
        """Plot correlation heatmap."""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.colors as mcolors

            if self.corr_matrix is None:
                return

            fig, ax = plt.subplots(figsize=(10, 8))
            coins   = list(self.corr_matrix.columns)
            matrix  = self.corr_matrix.values
            n       = len(coins)

            # Custom red-white-green colormap
            cmap = mcolors.LinearSegmentedColormap.from_list(
                "rwg", ["#D85A30", "white", "#1D9E75"])
            im   = ax.imshow(matrix, cmap=cmap, vmin=-1, vmax=1)

            ax.set_xticks(range(n)); ax.set_yticks(range(n))
            ax.set_xticklabels(coins, rotation=45, ha="right", fontsize=9)
            ax.set_yticklabels(coins, fontsize=9)

            for i in range(n):
                for j in range(n):
                    val   = matrix[i, j]
                    color = "white" if abs(val) > 0.5 else "black"
                    ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                           fontsize=8, color=color)

            plt.colorbar(im, ax=ax, label="Correlation")
            ax.set_title(f"Portfolio Correlation Matrix ({self.lookback_days}d)")
            plt.tight_layout()
            plt.savefig("correlation_matrix.png", dpi=150)
            plt.close()
            print("  Chart saved: correlation_matrix.png")
        except ImportError:
            print("  (pip install matplotlib for correlation chart)")
        except Exception as e:
            print(f"  Chart error: {e}")

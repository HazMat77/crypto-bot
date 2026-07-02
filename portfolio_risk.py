"""
Portfolio-Level Risk — VaR & Stress Testing
=============================================
Builds on portfolio_correlation.py's CorrelationChecker (which already
fetches daily price history and computes a returns/correlation matrix for
the active coin list) to answer two questions the correlation report
alone doesn't:

  1. Value at Risk — "how much could this whole portfolio plausibly lose
     in a single day?" (both historical-simulation and parametric methods)
  2. Stress test — "what if BTC drops 30% tomorrow — what does that do
     to the REST of the portfolio, not just BTC itself?" (via each coin's
     historical beta to the shock coin)

Usage:
    from portfolio_correlation import CorrelationChecker
    from portfolio_risk import PortfolioRiskAnalyzer

    cc = CorrelationChecker()
    cc.check(["BTC-USDT", "ETH-USDT", "SOL-USDT"])

    risk = PortfolioRiskAnalyzer(cc)
    risk.value_at_risk(weights={"BTC": 40, "ETH": 35, "SOL": 25}, portfolio_value=100)
    risk.stress_test(weights={"BTC": 40, "ETH": 35, "SOL": 25}, portfolio_value=100,
                     shock_coin="BTC", shock_pct=-0.30)
"""

import logging
import numpy as np

log = logging.getLogger(__name__)

# z-scores for common one-tailed confidence levels, used by the parametric
# VaR method below (normal-distribution assumption).
_Z_SCORES = {0.90: 1.2816, 0.95: 1.6449, 0.99: 2.3263}


class PortfolioRiskAnalyzer:

    def __init__(self, checker):
        """
        Args:
            checker: a portfolio_correlation.CorrelationChecker that has
                     already had .check(symbols) called on it — this reuses
                     its fetched daily-returns data rather than re-fetching.
        """
        if checker.corr_matrix is None or not hasattr(checker, "returns_df"):
            raise RuntimeError(
                "PortfolioRiskAnalyzer needs a CorrelationChecker that's "
                "already had .check(symbols) run on it."
            )
        self.returns_df  = checker.returns_df
        self.corr_matrix = checker.corr_matrix

    def _normalised_weights(self, weights: dict) -> tuple:
        """Returns (coin_list, weight_array) restricted to coins that
        actually have price history, normalised to fractions summing to 1."""
        coins = [c for c in weights if c in self.returns_df.columns]
        if not coins:
            raise ValueError(
                "None of the given coins have price history loaded — "
                "run CorrelationChecker.check() with these symbols first."
            )
        raw = np.array([weights[c] for c in coins], dtype=float)
        total = raw.sum()
        if total <= 0:
            raise ValueError("Portfolio weights must sum to a positive number")
        return coins, raw / total

    def value_at_risk(self, weights: dict, portfolio_value: float,
                      confidence: float = 0.95, method: str = "historical") -> dict:
        """
        Estimates 1-day Value at Risk for the portfolio.

        method="historical": resamples the ACTUAL historical daily return
            of the weighted portfolio and takes the relevant percentile —
            makes no assumption about the shape of the return distribution,
            but is only as good as how much history was fetched.

        method="parametric": assumes returns are roughly normally
            distributed and uses the covariance matrix directly
            (portfolio variance = w^T · Σ · w) — smoother/faster, but can
            understate tail risk for genuinely fat-tailed crypto moves.
            Useful as a cross-check against the historical method rather
            than a replacement for it.

        Returns a dict with both the % and $ VaR — "with `confidence`
        probability, this portfolio should not lose more than $X over the
        next day" (the flip side: there's a (1-confidence) chance it does).
        """
        coins, w = self._normalised_weights(weights)

        if method == "parametric":
            cov       = self.returns_df[coins].cov().values
            port_var  = float(w @ cov @ w.T)
            port_std  = port_var ** 0.5
            z         = _Z_SCORES.get(confidence, 1.6449)
            var_pct   = z * port_std
        else:
            port_returns = (self.returns_df[coins] * w).sum(axis=1)
            if len(port_returns) < 10:
                log.warning("[VAR] Fewer than 10 days of history — historical VaR "
                           "estimate will be noisy; consider method='parametric' "
                           "or a longer CorrelationChecker(lookback_days=...) window.")
            var_pct = -float(np.percentile(port_returns, (1 - confidence) * 100))

        return {
            "method":           method,
            "confidence":       confidence,
            "coins":            coins,
            "var_pct":          round(var_pct * 100, 2),
            "var_usdt":         round(var_pct * portfolio_value, 2),
            "portfolio_value":  portfolio_value,
            "interpretation": (
                f"With {confidence:.0%} confidence, this portfolio's 1-day loss "
                f"should not exceed ${round(var_pct * portfolio_value, 2):.2f} "
                f"({var_pct*100:.1f}%) — i.e. a loss beyond that is a "
                f"{(1-confidence)*100:.0f}-in-100 event on this history."
            ),
        }

    def stress_test(self, weights: dict, portfolio_value: float,
                    shock_coin: str = "BTC", shock_pct: float = -0.30) -> dict:
        """
        "What if `shock_coin` moves by `shock_pct` — what happens to the
        REST of the portfolio?" Each other coin's expected move is
        estimated via its historical beta to the shock coin (beta =
        cov(coin, shock) / var(shock) — the standard regression-slope
        definition), not just its raw correlation, so a coin that's
        highly correlated but much less volatile than the shock coin
        correctly shows a smaller expected move, not a 1:1 one.
        """
        coins, w = self._normalised_weights(weights)
        if shock_coin not in self.returns_df.columns:
            raise ValueError(
                f"{shock_coin} has no price history loaded — include it in "
                f"the CorrelationChecker.check(symbols) call first."
            )

        shock_returns = self.returns_df[shock_coin]
        shock_var     = float(np.var(shock_returns))

        per_coin_impact = {}
        weighted_total  = 0.0
        for coin, weight_frac in zip(coins, w):
            if coin == shock_coin:
                beta = 1.0
            elif shock_var > 0:
                cov  = float(np.cov(self.returns_df[coin], shock_returns)[0, 1])
                beta = cov / shock_var
            else:
                beta = 0.0

            estimated_move = beta * shock_pct
            per_coin_impact[coin] = {
                "beta":               round(beta, 2),
                "estimated_move_pct": round(estimated_move * 100, 2),
            }
            weighted_total += weight_frac * estimated_move

        return {
            "shock_coin":              shock_coin,
            "shock_pct":               round(shock_pct * 100, 1),
            "per_coin_impact":         per_coin_impact,
            "portfolio_impact_pct":    round(weighted_total * 100, 2),
            "portfolio_impact_usdt":   round(weighted_total * portfolio_value, 2),
            "portfolio_value":         portfolio_value,
            "interpretation": (
                f"If {shock_coin} moved {shock_pct*100:+.0f}%, this portfolio's "
                f"estimated impact (beta-weighted across all holdings) is "
                f"{weighted_total*100:+.2f}% (${weighted_total*portfolio_value:+.2f})."
            ),
        }

    def print_var_report(self, weights: dict, portfolio_value: float):
        hist = self.value_at_risk(weights, portfolio_value, 0.95, "historical")
        para = self.value_at_risk(weights, portfolio_value, 0.95, "parametric")
        print(f"\n{'═'*65}")
        print(f"  PORTFOLIO VALUE AT RISK  (95% confidence, 1-day)")
        print(f"{'═'*65}")
        print(f"  Portfolio value:  ${portfolio_value:,.2f}")
        print(f"  Historical VaR:   ${hist['var_usdt']:,.2f}  ({hist['var_pct']:.2f}%)")
        print(f"  Parametric VaR:   ${para['var_usdt']:,.2f}  ({para['var_pct']:.2f}%)")
        print(f"{'─'*65}")
        print(f"  {hist['interpretation']}")
        print(f"{'═'*65}")

    def print_stress_report(self, weights: dict, portfolio_value: float,
                            shock_coin: str = "BTC", shock_pct: float = -0.30):
        r = self.stress_test(weights, portfolio_value, shock_coin, shock_pct)
        print(f"\n{'═'*65}")
        print(f"  STRESS TEST — {shock_coin} {shock_pct*100:+.0f}%")
        print(f"{'═'*65}")
        for coin, impact in r["per_coin_impact"].items():
            print(f"  {coin:<8} beta={impact['beta']:>5.2f}  "
                 f"estimated move: {impact['estimated_move_pct']:+.2f}%")
        print(f"{'─'*65}")
        print(f"  Portfolio impact: {r['portfolio_impact_pct']:+.2f}%  "
             f"(${r['portfolio_impact_usdt']:+,.2f})")
        print(f"{'═'*65}")

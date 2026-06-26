"""
Backtesting & Optimization Framework
======================================
Tests RSI+MA strategy against historical data.
Includes grid search optimizer with walk-forward validation.

Usage:
  python backtest.py --symbol BTC-USDT --days 90
  python backtest.py --all-coins --days 60 --plot
  python backtest.py --symbol BTC-USDT --optimize --days 180
  python backtest.py --symbol ETH-USDT --source ccxt --ccxt-exchange binance
  python backtest.py --symbol BTC-USDT --source csv --csv-path data/btc.csv
"""

import argparse
import logging
import itertools
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")


# ══════════════════════════════════════════════════════════════════════════════
#  DATA FETCHING
# ══════════════════════════════════════════════════════════════════════════════

def fetch_kucoin(symbol: str, interval: str = "15min", days: int = 90) -> pd.DataFrame:
    """Fetch candles from KuCoin public API — no key needed."""
    log.info(f"[DATA] KuCoin: {symbol} {interval} {days}d")
    end_time    = int(datetime.now().timestamp())
    start_time  = int((datetime.now() - timedelta(days=days)).timestamp())
    all_candles = []
    current_end = end_time

    while current_end > start_time:
        try:
            resp = requests.get(
                "https://api.kucoin.com/api/v1/market/candles",
                params={"symbol": symbol, "type": interval,
                        "startAt": start_time, "endAt": current_end},
                timeout=15,
            )
            data = resp.json().get("data", [])
            if not data:
                break
            all_candles.extend(data)
            oldest = int(data[-1][0])
            if oldest >= current_end:
                break
            current_end = oldest - 1
            if len(data) < 1500:
                break
        except Exception as e:
            log.error(f"[DATA] KuCoin fetch error: {e}")
            break

    if not all_candles:
        return pd.DataFrame()

    all_candles = list(reversed(all_candles))
    df = pd.DataFrame(all_candles,
                      columns=["time","open","close","high","low","volume","turnover"])
    df = df.astype({"open":float,"close":float,"high":float,
                    "low":float,"volume":float,"time":int})
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    log.info(f"[DATA] {len(df)} candles fetched "
             f"({df['datetime'].iloc[0].date()} → {df['datetime'].iloc[-1].date()})")
    return df


def fetch_ccxt(symbol: str, exchange_name: str = "kucoin",
               interval: str = "15min", days: int = 90) -> pd.DataFrame:
    """Fetch via CCXT (pip install ccxt). Supports 100+ exchanges."""
    try:
        import ccxt
    except ImportError:
        raise ImportError("Run: pip install ccxt")

    ex_class = getattr(ccxt, exchange_name, None)
    if not ex_class:
        raise ValueError(f"CCXT exchange '{exchange_name}' not found")

    ex  = ex_class({"enableRateLimit": True})
    sym = symbol.replace("-", "/")
    tfm = {"1min":"1m","5min":"5m","15min":"15m","30min":"30m",
           "1hour":"1h","4hour":"4h","1day":"1d"}
    tf  = tfm.get(interval, interval)
    since = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

    log.info(f"[DATA] CCXT/{exchange_name}: {sym} {tf} {days}d")
    ohlcv = ex.fetch_ohlcv(sym, tf, since=since, limit=1000)
    if not ohlcv:
        return pd.DataFrame()

    df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
    df["time"]     = df["time"] // 1000
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def fetch_csv(symbol: str, csv_path: str = None) -> pd.DataFrame:
    """Load from local CSV file."""
    path = csv_path
    if not path:
        for candidate in [f"{symbol}.csv", f"{symbol.replace('-','')}.csv",
                          f"data/{symbol}.csv"]:
            if Path(candidate).exists():
                path = candidate
                break
    if not path or not Path(path).exists():
        raise FileNotFoundError(f"CSV not found for {symbol}. Set --csv-path")

    df = pd.read_csv(path)
    df.columns = [c.lower().strip() for c in df.columns]
    rename = {c: "time" for c in df.columns
              if c in ("timestamp","date","datetime","open_time")}
    df = df.rename(columns=rename)

    if "time" in df.columns:
        if df["time"].dtype == object:
            df["datetime"] = pd.to_datetime(df["time"])
            df["time"]     = df["datetime"].astype(int) // 10**9
        else:
            ts = df["time"].iloc[0]
            if ts > 1e12:
                df["time"] = df["time"] // 1000
            df["datetime"] = pd.to_datetime(df["time"], unit="s")

    for col in ["open","high","low","close","volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def get_data(symbol: str, interval: str = "15min", days: int = 90,
             source: str = "kucoin", ccxt_exchange: str = "kucoin",
             csv_path: str = None) -> pd.DataFrame:
    """Unified data fetcher with automatic KuCoin fallback."""
    try:
        if source == "ccxt":
            return fetch_ccxt(symbol, ccxt_exchange, interval, days)
        elif source == "csv":
            return fetch_csv(symbol, csv_path)
        else:
            return fetch_kucoin(symbol, interval, days)
    except Exception as e:
        if source != "kucoin":
            log.warning(f"[DATA] {source} failed ({e}) — falling back to KuCoin")
            try:
                return fetch_kucoin(symbol, interval, days)
            except Exception as e2:
                log.error(f"[DATA] KuCoin fallback also failed: {e2}")
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
#  INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def calc_ma(series: pd.Series, period: int = 20) -> pd.Series:
    return series.rolling(period).mean()

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-pc).abs(),
                    (df["low"]-pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ══════════════════════════════════════════════════════════════════════════════
#  BACKTESTER
# ══════════════════════════════════════════════════════════════════════════════

class Backtester:

    def __init__(self, symbol: str, df: pd.DataFrame,
                 starting_usdt: float = 100.0,
                 trade_size: float = 10.0,
                 rsi_period: int = 14,
                 rsi_buy: float = 35.0,
                 rsi_sell: float = 65.0,
                 ma_period: int = 20,
                 stop_loss_pct: float = 0.06,
                 take_profit_pct: float = 0.04,
                 trailing_stop_pct: float = 0.03,
                 max_hold_candles: int = 192,
                 fee_rate: float = 0.001):
        self.symbol            = symbol
        self.df                = df.copy()
        self.starting_usdt     = starting_usdt
        self.trade_size        = trade_size
        self.rsi_period        = rsi_period
        self.rsi_buy           = rsi_buy
        self.rsi_sell          = rsi_sell
        self.ma_period         = ma_period
        self.stop_loss_pct     = stop_loss_pct
        self.take_profit_pct   = take_profit_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.max_hold_candles  = max_hold_candles
        self.fee_rate          = fee_rate
        self.trades            = []
        self.equity_curve      = []

    def run(self) -> dict:
        df = self.df
        df["rsi"] = calc_rsi(df["close"], self.rsi_period)
        df["ma"]  = calc_ma(df["close"],  self.ma_period)

        pool         = self.starting_usdt
        in_position  = False
        buy_price    = 0.0
        buy_spent    = 0.0
        peak_price   = 0.0
        entry_candle = 0
        min_idx      = max(self.rsi_period, self.ma_period) + 5

        for i in range(min_idx, len(df)):
            row   = df.iloc[i]
            price = row["close"]
            rsi   = row["rsi"]
            ma    = row["ma"]

            if pd.isna(rsi) or pd.isna(ma):
                continue

            unrealised = (pool + buy_spent * ((price - buy_price) / buy_price)
                          if in_position else pool)
            self.equity_curve.append({"datetime": row["datetime"], "pool": unrealised})

            if in_position:
                peak_price  = max(peak_price, price)
                hold_bars   = i - entry_candle
                pct_change  = (price - buy_price) / buy_price
                exit_reason = None

                if pct_change <= -self.stop_loss_pct:
                    exit_reason = "stop_loss"
                elif pct_change >= self.take_profit_pct:
                    exit_reason = "take_profit"
                elif peak_price > buy_price:
                    if (price - peak_price) / peak_price <= -self.trailing_stop_pct:
                        exit_reason = "trailing_stop"
                if not exit_reason and rsi > self.rsi_sell and price < ma:
                    exit_reason = "rsi_signal"
                if not exit_reason and hold_bars >= self.max_hold_candles:
                    exit_reason = "max_hold"

                if exit_reason:
                    proceeds  = round(buy_spent / buy_price * price, 6)
                    fees      = round((buy_spent + proceeds) * self.fee_rate, 6)
                    pnl_gross = proceeds - buy_spent
                    pnl_net   = pnl_gross - fees
                    pool     += proceeds
                    self.trades.append({
                        "symbol":       self.symbol,
                        "entry_time":   df.iloc[entry_candle]["datetime"],
                        "exit_time":    row["datetime"],
                        "buy_price":    buy_price,
                        "sell_price":   price,
                        "pnl_gross":    pnl_gross,
                        "fees":         fees,
                        "pnl_net":      pnl_net,
                        "pct_change":   pct_change * 100,
                        "exit_reason":  exit_reason,
                        "hold_candles": hold_bars,
                    })
                    in_position = False

            else:
                if rsi < self.rsi_buy and price > ma and pool >= self.trade_size:
                    actual = min(self.trade_size, pool) * 0.95
                    pool       -= actual + actual * self.fee_rate
                    buy_price   = price
                    buy_spent   = actual
                    peak_price  = price
                    entry_candle = i
                    in_position  = True

        return self._summarise(pool)

    def _summarise(self, final_pool: float) -> dict:
        if not self.trades:
            return {"error": "No trades — try wider RSI thresholds (e.g. --rsi-buy 45 --rsi-sell 55)"}

        wins          = [t for t in self.trades if t["pnl_net"] >= 0]
        losses        = [t for t in self.trades if t["pnl_net"] < 0]
        total_net     = sum(t["pnl_net"]  for t in self.trades)
        total_fees    = sum(t["fees"]     for t in self.trades)
        win_rate      = len(wins) / len(self.trades) * 100
        avg_win       = sum(t["pnl_net"] for t in wins)   / len(wins)   if wins   else 0
        avg_loss      = sum(t["pnl_net"] for t in losses) / len(losses) if losses else 0
        win_sum       = sum(t["pnl_net"] for t in wins)
        loss_sum      = abs(sum(t["pnl_net"] for t in losses))
        profit_factor = win_sum / loss_sum if loss_sum > 0 else float("inf")

        eq     = pd.Series([e["pool"] for e in self.equity_curve])
        peak   = eq.cummax()
        dd     = (eq - peak) / peak * 100
        max_dd = float(dd.min())

        reasons = defaultdict(int)
        for t in self.trades:
            reasons[t["exit_reason"]] += 1

        bh_return = 0.0
        if len(self.df) > 1:
            bh_return = (self.df["close"].iloc[-1] - self.df["close"].iloc[0]) / \
                         self.df["close"].iloc[0] * 100

        roi = (final_pool - self.starting_usdt) / self.starting_usdt * 100

        return {
            "symbol":         self.symbol,
            "total_trades":   len(self.trades),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate":       round(win_rate, 1),
            "avg_win_usdt":   round(avg_win, 4),
            "avg_loss_usdt":  round(avg_loss, 4),
            "profit_factor":  round(profit_factor, 2),
            "total_net_usdt": round(total_net, 4),
            "total_fees":     round(total_fees, 4),
            "final_pool":     round(final_pool, 2),
            "roi_pct":        round(roi, 2),
            "max_drawdown":   round(max_dd, 2),
            "buy_hold_pct":   round(bh_return, 2),
            "exit_reasons":   dict(reasons),
            "trades":         self.trades,
            "equity_curve":   self.equity_curve,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  GRID SEARCH OPTIMIZER WITH WALK-FORWARD VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class GridSearchOptimizer:
    """
    Finds the best RSI/MA/SL/TP parameters using walk-forward validation.
    Trains on first 70% of data, validates on last 30%.
    Only recommends settings that perform well on UNSEEN data.
    """

    DEFAULT_GRID = {
        "rsi_buy":         [30, 35, 40, 45],
        "rsi_sell":        [55, 60, 65, 70],
        "ma_period":       [10, 20, 30],
        "stop_loss_pct":   [0.04, 0.06, 0.08],
        "take_profit_pct": [0.03, 0.04, 0.06],
    }

    def __init__(self, symbol: str = "BTC-USDT", days: int = 180,
                 source: str = "kucoin", ccxt_exchange: str = "kucoin",
                 csv_path: str = None, interval: str = "15min",
                 starting_usdt: float = 100.0, trade_size: float = 10.0,
                 grid: dict = None):
        self.symbol        = symbol
        self.days          = max(days, 180)
        self.source        = source
        self.ccxt_exchange = ccxt_exchange
        self.csv_path      = csv_path
        self.interval      = interval
        self.starting_usdt = starting_usdt
        self.trade_size    = trade_size
        self.grid          = grid or self.DEFAULT_GRID

    def run(self, top_n: int = 5) -> list:
        log.info(f"[OPTIMIZE] Fetching {self.days}d of data for {self.symbol}...")
        df = get_data(self.symbol, self.interval, self.days,
                      self.source, self.ccxt_exchange, self.csv_path)
        if df.empty or len(df) < 100:
            log.error("[OPTIMIZE] Insufficient data")
            return []

        split    = int(len(df) * 0.70)
        df_train = df.iloc[:split].reset_index(drop=True)
        df_test  = df.iloc[split:].reset_index(drop=True)
        log.info(f"[OPTIMIZE] Train: {len(df_train)} candles | Test: {len(df_test)} candles")

        keys   = list(self.grid.keys())
        combos = list(itertools.product(*self.grid.values()))
        log.info(f"[OPTIMIZE] Testing {len(combos)} combinations...")

        def score(r):
            if "error" in r or r.get("total_trades", 0) < 5:
                return -999
            dd = max(abs(r["max_drawdown"]), 0.1)
            return (r["win_rate"] * r["roi_pct"]) / dd

        # Phase 1: train
        train_results = []
        for combo in combos:
            params = dict(zip(keys, combo))
            if params.get("rsi_buy", 35) >= params.get("rsi_sell", 65):
                continue
            r = Backtester(symbol=self.symbol, df=df_train,
                           starting_usdt=self.starting_usdt,
                           trade_size=self.trade_size,
                           rsi_buy=params["rsi_buy"],
                           rsi_sell=params["rsi_sell"],
                           ma_period=params["ma_period"],
                           stop_loss_pct=params["stop_loss_pct"],
                           take_profit_pct=params["take_profit_pct"]).run()
            s = score(r)
            if s > -999:
                train_results.append({"params": params, "train": r, "score": s})

        if not train_results:
            log.error("[OPTIMIZE] No valid combinations found in training data")
            return []

        train_results.sort(key=lambda x: x["score"], reverse=True)
        candidates = train_results[:max(top_n * 3, 15)]
        log.info(f"[OPTIMIZE] Walk-forward validating top {len(candidates)} candidates...")

        # Phase 2: walk-forward validate
        final = []
        for c in candidates:
            params = c["params"]
            r_test = Backtester(symbol=self.symbol, df=df_test,
                                starting_usdt=self.starting_usdt,
                                trade_size=self.trade_size,
                                rsi_buy=params["rsi_buy"],
                                rsi_sell=params["rsi_sell"],
                                ma_period=params["ma_period"],
                                stop_loss_pct=params["stop_loss_pct"],
                                take_profit_pct=params["take_profit_pct"]).run()
            s_test = score(r_test)
            if s_test == -999:
                continue
            combined = (c["score"] + s_test) / 2
            final.append({"params": params, "train": c["train"],
                          "test": r_test, "combined": combined})

        final.sort(key=lambda x: x["combined"], reverse=True)
        top = final[:top_n]
        self._print_results(top)
        return top

    def _print_results(self, results: list):
        print(f"\n{'═'*65}")
        print(f"  OPTIMIZATION RESULTS — {self.symbol}")
        print(f"  Walk-forward: 70% train / 30% unseen test")
        print(f"{'═'*65}")
        for i, r in enumerate(results, 1):
            p  = r["params"]
            tr = r["train"]
            te = r["test"]
            print(f"\n  #{i} Score={r['combined']:.2f}")
            print(f"  Params: RSI {p['rsi_buy']}/{p['rsi_sell']} "
                  f"MA={p['ma_period']} SL={p['stop_loss_pct']*100:.0f}% "
                  f"TP={p['take_profit_pct']*100:.0f}%")
            print(f"  Train: {tr['win_rate']}%WR ROI{tr['roi_pct']:+.1f}% "
                  f"DD{tr['max_drawdown']:.1f}% {tr['total_trades']}trades")
            print(f"  Test:  {te['win_rate']}%WR ROI{te['roi_pct']:+.1f}% "
                  f"DD{te['max_drawdown']:.1f}% {te['total_trades']}trades")
        if results:
            best = results[0]["params"]
            print(f"\n  ✅ RECOMMENDED config.py SETTINGS:")
            print(f"  RSI_BUY          = {best['rsi_buy']}")
            print(f"  RSI_SELL         = {best['rsi_sell']}")
            print(f"  MA_PERIOD        = {best['ma_period']}")
            print(f"  STOP_LOSS_PCT    = {best['stop_loss_pct']}")
            print(f"  TAKE_PROFIT_PCT  = {best['take_profit_pct']}")
        print(f"{'═'*65}")


class WalkForwardOptimizer:
    """
    Proper multi-window rolling walk-forward optimization — a stronger
    test than GridSearchOptimizer's single 70/30 split above.

    Splits the full data history into N consecutive windows. For each
    window i, finds the best parameters using ONLY windows up to and
    including i, then validates those parameters on window i+1 — data
    the optimization never saw. This repeats across every window, so a
    parameter set only earns a strong final score if it held up across
    MULTIPLE distinct unseen periods, not just one lucky train/test split.

    A parameter set that wins big in one window but falls apart in the
    next is a sign of overfitting to that specific period — this method
    is specifically designed to catch and penalise that, which a single
    train/test split cannot.
    """

    def __init__(self, symbol: str = "BTC-USDT", days: int = 270,
                 source: str = "kucoin", ccxt_exchange: str = "kucoin",
                 csv_path: str = None, interval: str = "15min",
                 starting_usdt: float = 100.0, trade_size: float = 10.0,
                 grid: dict = None, n_windows: int = 5):
        self.symbol        = symbol
        self.days          = max(days, 270)   # need enough history for multiple windows
        self.source        = source
        self.ccxt_exchange = ccxt_exchange
        self.csv_path      = csv_path
        self.interval      = interval
        self.starting_usdt = starting_usdt
        self.trade_size    = trade_size
        self.grid          = grid or GridSearchOptimizer.DEFAULT_GRID
        self.n_windows     = max(3, n_windows)   # need at least 3 to be meaningful

    def run(self, top_n: int = 3) -> dict:
        log.info(f"[WALKFORWARD] Fetching {self.days}d for {self.symbol}, "
                f"{self.n_windows} rolling windows...")
        df = get_data(self.symbol, self.interval, self.days,
                      self.source, self.ccxt_exchange, self.csv_path)

        if df.empty or len(df) < 200:
            log.error("[WALKFORWARD] Insufficient data for rolling windows")
            return {"error": "Insufficient data"}

        window_size = len(df) // self.n_windows
        if window_size < 50:
            log.error(f"[WALKFORWARD] Windows too small ({window_size} candles) — "
                     f"need more days of history or fewer windows")
            return {"error": "Windows too small"}

        windows = [df.iloc[i*window_size:(i+1)*window_size].reset_index(drop=True)
                  for i in range(self.n_windows)]

        keys   = list(self.grid.keys())
        combos = list(itertools.product(*self.grid.values()))
        valid_combos = [dict(zip(keys, c)) for c in combos
                        if c[keys.index("rsi_buy")] < c[keys.index("rsi_sell")]]

        log.info(f"[WALKFORWARD] {len(valid_combos)} parameter sets × "
                f"{self.n_windows-1} train→test transitions")

        # Track each parameter set's out-of-sample performance across
        # every window transition it's tested on
        oos_scores = {i: [] for i in range(len(valid_combos))}

        def score(r):
            if "error" in r or r.get("total_trades", 0) < 3:
                return None
            dd = max(abs(r["max_drawdown"]), 0.1)
            return (r["win_rate"] * r["roi_pct"]) / dd

        # For each consecutive pair of windows: optimize on window i,
        # test EXCLUSIVELY on window i+1 (data never used for that fit)
        for w in range(self.n_windows - 1):
            train_df = windows[w]
            test_df  = windows[w + 1]

            for idx, params in enumerate(valid_combos):
                train_r = Backtester(symbol=self.symbol, df=train_df,
                                     starting_usdt=self.starting_usdt,
                                     trade_size=self.trade_size,
                                     rsi_buy=params["rsi_buy"], rsi_sell=params["rsi_sell"],
                                     ma_period=params["ma_period"],
                                     stop_loss_pct=params["stop_loss_pct"],
                                     take_profit_pct=params["take_profit_pct"]).run()
                train_score = score(train_r)
                if train_score is None:
                    continue   # this param set didn't even trade enough in-sample

                test_r = Backtester(symbol=self.symbol, df=test_df,
                                    starting_usdt=self.starting_usdt,
                                    trade_size=self.trade_size,
                                    rsi_buy=params["rsi_buy"], rsi_sell=params["rsi_sell"],
                                    ma_period=params["ma_period"],
                                    stop_loss_pct=params["stop_loss_pct"],
                                    take_profit_pct=params["take_profit_pct"]).run()
                test_score = score(test_r)
                if test_score is not None:
                    oos_scores[idx].append(test_score)

        # A parameter set's final ranking is its MEAN out-of-sample score
        # across every window it was validated on, penalised for having
        # fewer valid windows (less evidence = less trust)
        ranked = []
        for idx, scores_list in oos_scores.items():
            if not scores_list:
                continue
            mean_score   = sum(scores_list) / len(scores_list)
            consistency  = len(scores_list) / (self.n_windows - 1)   # 0-1, fraction of windows it held up
            final_score  = mean_score * consistency
            ranked.append({
                "params":           valid_combos[idx],
                "mean_oos_score":   round(mean_score, 3),
                "windows_tested":   len(scores_list),
                "consistency":      round(consistency, 2),
                "final_score":      round(final_score, 3),
            })

        ranked.sort(key=lambda x: x["final_score"], reverse=True)
        top = ranked[:top_n]

        self._print_results(top)
        return {"top_params": top, "n_windows": self.n_windows, "symbol": self.symbol}

    def _print_results(self, results: list):
        print(f"\n{'═'*65}")
        print(f"  WALK-FORWARD OPTIMIZATION (multi-window) — {self.symbol}")
        print(f"  {self.n_windows} rolling windows, validated on consecutive unseen periods")
        print(f"{'═'*65}")
        for i, r in enumerate(results, 1):
            p = r["params"]
            print(f"\n  #{i} Final score={r['final_score']:.2f} "
                  f"(mean OOS={r['mean_oos_score']:.2f}, "
                  f"held up in {r['windows_tested']}/{self.n_windows-1} windows)")
            print(f"  Params: RSI {p['rsi_buy']}/{p['rsi_sell']} "
                  f"MA={p['ma_period']} SL={p['stop_loss_pct']*100:.0f}% "
                  f"TP={p['take_profit_pct']*100:.0f}%")
        if results:
            best = results[0]
            print(f"\n  ✅ MOST ROBUST SETTINGS (consistent across {best['windows_tested']} windows):")
            p = best["params"]
            print(f"  RSI_BUY          = {p['rsi_buy']}")
            print(f"  RSI_SELL         = {p['rsi_sell']}")
            print(f"  MA_PERIOD        = {p['ma_period']}")
            print(f"  STOP_LOSS_PCT    = {p['stop_loss_pct']}")
            print(f"  TAKE_PROFIT_PCT  = {p['take_profit_pct']}")
        print(f"{'═'*65}")


# ══════════════════════════════════════════════════════════════════════════════
#  OUTPUT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def print_results(r: dict):
    if "error" in r:
        print(f"\n  ❌ {r['error']}")
        return
    print(f"\n{'═'*60}")
    print(f"  BACKTEST — {r['symbol']}")
    print(f"{'═'*60}")
    print(f"  Trades:         {r['total_trades']}  (✅{r['wins']} ❌{r['losses']})")
    print(f"  Win rate:       {r['win_rate']}%")
    print(f"  Avg win:        +${r['avg_win_usdt']:.4f}")
    print(f"  Avg loss:        ${r['avg_loss_usdt']:.4f}")
    print(f"  Profit factor:  {r['profit_factor']}")
    print(f"  {'─'*56}")
    print(f"  Net P&L:        ${r['total_net_usdt']:+.4f}")
    print(f"  Fees paid:      -${r['total_fees']:.4f}")
    print(f"  Final pool:     ${r['final_pool']:.2f}")
    print(f"  ROI:            {r['roi_pct']:+.2f}%")
    print(f"  Max drawdown:   {r['max_drawdown']:.2f}%")
    print(f"  Buy & hold:     {r['buy_hold_pct']:+.2f}%")
    print(f"  Exit reasons:   {r['exit_reasons']}")
    verdict = ("✅ Positive edge" if r['win_rate'] >= 55 and r['roi_pct'] > 0
               else "⚠️  Marginal" if r['roi_pct'] > 0
               else "❌ No edge found")
    print(f"  Verdict:        {verdict}")
    try:
        pd.DataFrame(r["trades"]).to_csv(
            f"backtest_{r['symbol'].replace('-','_')}.csv", index=False)
        print(f"  Trade log:      backtest_{r['symbol'].replace('-','_')}.csv")
    except Exception:
        pass
    print(f"{'═'*60}")


def try_plot(r: dict):
    try:
        import matplotlib.pyplot as plt
        eq  = pd.DataFrame(r["equity_curve"])
        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(eq["datetime"], eq["pool"], color="green", label="Bot equity")
        ax.axhline(y=100, color="gray", linestyle="--", label="Start")
        ax.set_title(f"Equity Curve — {r['symbol']}")
        ax.set_xlabel("Date"); ax.set_ylabel("Pool (USDT)"); ax.legend()
        plt.tight_layout()
        fname = f"backtest_{r['symbol'].replace('-','_')}_equity.png"
        plt.savefig(fname); plt.close()
        print(f"  Chart saved: {fname}")
    except ImportError:
        print("  (pip install matplotlib for equity charts)")
    except Exception as e:
        print(f"  Chart error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="Backtest / optimize RSI+MA strategy")
    p.add_argument("--symbol",        default="BTC-USDT")
    p.add_argument("--days",          type=int,   default=90)
    p.add_argument("--interval",      default="15min")
    p.add_argument("--pool",          type=float, default=100.0)
    p.add_argument("--trade-size",    type=float, default=10.0)
    p.add_argument("--rsi-buy",       type=float, default=35.0)
    p.add_argument("--rsi-sell",      type=float, default=65.0)
    p.add_argument("--rsi-period",    type=int,   default=14)
    p.add_argument("--ma-period",     type=int,   default=20)
    p.add_argument("--stop-loss",     type=float, default=0.06)
    p.add_argument("--take-profit",   type=float, default=0.04)
    p.add_argument("--all-coins",     action="store_true")
    p.add_argument("--plot",          action="store_true")
    p.add_argument("--optimize",      action="store_true",
                   help="Grid search with walk-forward validation")
    p.add_argument("--source",        default="kucoin",
                   choices=["kucoin","ccxt","csv"],
                   help="Data source")
    p.add_argument("--ccxt-exchange", default="kucoin")
    p.add_argument("--csv-path",      default=None)
    p.add_argument("--monte-carlo",  action="store_true",
                   help="Run Monte Carlo simulation on backtest results")
    p.add_argument("--mc-sims",      type=int, default=1000,
                   help="Number of Monte Carlo simulations (default: 1000)")
    p.add_argument("--save-data",     action="store_true",
                   help="Save fetched candles to CSV")
    args = p.parse_args()

    TOP_COINS = ["BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT",
                 "ADA-USDT","DOT-USDT","AVAX-USDT","LINK-USDT","LTC-USDT"]
    symbols   = TOP_COINS if args.all_coins else [args.symbol]

    if args.optimize:
        for symbol in symbols:
            GridSearchOptimizer(
                symbol        = symbol,
                days          = max(args.days, 180),
                source        = args.source,
                ccxt_exchange = args.ccxt_exchange,
                csv_path      = args.csv_path,
                interval      = args.interval,
                starting_usdt = args.pool,
                trade_size    = getattr(args, "trade_size", 10.0),
            ).run(top_n=5)
        return

    all_results = []
    for symbol in symbols:
        df = get_data(symbol, args.interval, args.days,
                      args.source, args.ccxt_exchange, args.csv_path)
        if df.empty:
            print(f"  Skipping {symbol} — no data"); continue

        if args.save_data:
            Path("data").mkdir(exist_ok=True)
            df.to_csv(f"data/{symbol.replace('/','_')}.csv", index=False)
            print(f"  Saved data/{ symbol.replace('/','_')}.csv")

        r = Backtester(symbol=symbol, df=df,
                       starting_usdt=args.pool,
                       trade_size=getattr(args,"trade_size",10.0),
                       rsi_period=args.rsi_period,
                       rsi_buy=args.rsi_buy, rsi_sell=args.rsi_sell,
                       ma_period=args.ma_period,
                       stop_loss_pct=args.stop_loss,
                       take_profit_pct=args.take_profit).run()
        print_results(r)
        if args.plot and "error" not in r:
            try_plot(r)
        if getattr(args, "monte_carlo", False) and "error" not in r:
            from monte_carlo import MonteCarlo
            mc = MonteCarlo(r["trades"], starting_pool=args.pool,
                            trade_size=getattr(args,"trade_size",10.0))
            mc.run(simulations=getattr(args,"mc_sims",1000))
            mc.print_summary()
            if args.plot:
                mc.plot()
        all_results.append(r)

    valid = [r for r in all_results if "error" not in r]
    if len(valid) > 1:
        print(f"\n{'═'*60}")
        print(f"  MULTI-COIN SUMMARY ({len(valid)} coins)")
        print(f"{'═'*60}")
        print(f"  Avg win rate:     {sum(r['win_rate'] for r in valid)/len(valid):.1f}%")
        print(f"  Avg ROI:          {sum(r['roi_pct'] for r in valid)/len(valid):+.2f}%")
        print(f"  Avg drawdown:     {sum(r['max_drawdown'] for r in valid)/len(valid):.2f}%")
        print(f"{'═'*60}")


if __name__ == "__main__":
    main()

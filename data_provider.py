"""
Data Provider
==============
Unified data source for the backtester.
Supports multiple data sources with automatic fallback:

  1. KuCoin public API (built-in, no dependencies)
  2. CCXT library (100+ exchanges, optional: pip install ccxt)
  3. Offline CSV files (for air-gapped backtesting)
  4. CoinGecko historical (free, limited resolution)

Usage:
    from data_provider import DataProvider

    dp = DataProvider(source="kucoin")     # default
    dp = DataProvider(source="ccxt", exchange="binance")
    dp = DataProvider(source="csv", csv_path="btc_data.csv")

    df = dp.fetch("BTC-USDT", interval="15min", days=90)
"""

import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# Standard column names used throughout the bot
COLS = ["time", "open", "high", "low", "close", "volume"]


class DataProvider:

    def __init__(self, source: str = "kucoin",
                 exchange: str = "kucoin",
                 csv_path: str = None):
        """
        Args:
            source:    "kucoin" | "ccxt" | "csv" | "coingecko"
            exchange:  Exchange name for CCXT (e.g. "binance", "bybit")
            csv_path:  Path to CSV file when source="csv"
        """
        self.source    = source.lower()
        self.exchange  = exchange.lower()
        self.csv_path  = csv_path
        self._ccxt_ex  = None   # lazy-loaded CCXT exchange instance

    # ══════════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════════

    def fetch(self, symbol: str, interval: str = "15min",
              days: int = 90) -> pd.DataFrame:
        """
        Fetch OHLCV candles. Tries primary source, falls back to KuCoin.
        Returns DataFrame with columns: time, open, high, low, close, volume
        """
        log.info(f"[DATA] Fetching {symbol} ({interval}, {days}d) via {self.source}")

        try:
            if self.source == "ccxt":
                df = self._fetch_ccxt(symbol, interval, days)
            elif self.source == "csv":
                df = self._fetch_csv(symbol)
            elif self.source == "coingecko":
                df = self._fetch_coingecko(symbol, days)
            else:
                df = self._fetch_kucoin(symbol, interval, days)

            if df is not None and not df.empty:
                return self._normalise(df)
        except Exception as e:
            log.warning(f"[DATA] {self.source} failed: {e} — falling back to KuCoin")

        # Fallback
        try:
            df = self._fetch_kucoin(symbol, interval, days)
            return self._normalise(df)
        except Exception as e:
            log.error(f"[DATA] All sources failed for {symbol}: {e}")
            return pd.DataFrame()

    # ══════════════════════════════════════════════════════════════════════
    #  KUCOIN (built-in, always available)
    # ══════════════════════════════════════════════════════════════════════

    def _fetch_kucoin(self, symbol: str, interval: str, days: int) -> pd.DataFrame:
        end_time   = int(datetime.now().timestamp())
        start_time = int((datetime.now() - timedelta(days=days)).timestamp())
        all_candles = []
        current_end = end_time

        while current_end > start_time:
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

        if not all_candles:
            return pd.DataFrame()

        all_candles = list(reversed(all_candles))
        df = pd.DataFrame(all_candles,
                          columns=["time","open","close","high","low","volume","turnover"])
        df = df.astype({"open":float,"close":float,"high":float,
                        "low":float,"volume":float,"time":int})
        df["datetime"] = pd.to_datetime(df["time"], unit="s")
        return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)

    # ══════════════════════════════════════════════════════════════════════
    #  CCXT (optional — pip install ccxt)
    # ══════════════════════════════════════════════════════════════════════

    def _get_ccxt_exchange(self):
        if self._ccxt_ex is not None:
            return self._ccxt_ex
        try:
            import ccxt
        except ImportError:
            raise ImportError(
                "CCXT not installed. Run: pip install ccxt\n"
                "Then retry with source='ccxt'"
            )
        exchange_class = getattr(ccxt, self.exchange, None)
        if not exchange_class:
            raise ValueError(f"CCXT exchange '{self.exchange}' not found. "
                           f"Available: {', '.join(ccxt.exchanges[:10])}...")
        self._ccxt_ex = exchange_class({"enableRateLimit": True})
        return self._ccxt_ex

    def _fetch_ccxt(self, symbol: str, interval: str, days: int) -> pd.DataFrame:
        ex = self._get_ccxt_exchange()

        # Convert interval format: "15min" → "15m"
        interval_map = {
            "1min":"1m", "5min":"5m", "15min":"15m", "30min":"30m",
            "1hour":"1h", "4hour":"4h", "1day":"1d",
        }
        tf = interval_map.get(interval, interval)

        # Convert symbol: "BTC-USDT" → "BTC/USDT"
        ccxt_symbol = symbol.replace("-", "/")

        since = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        ohlcv = ex.fetch_ohlcv(ccxt_symbol, tf, since=since, limit=1000)

        if not ohlcv:
            return pd.DataFrame()

        df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        df["time"]     = df["time"] // 1000   # ms → s
        df["datetime"] = pd.to_datetime(df["time"], unit="s")
        return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)

    # ══════════════════════════════════════════════════════════════════════
    #  CSV (offline / pre-downloaded data)
    # ══════════════════════════════════════════════════════════════════════

    def _fetch_csv(self, symbol: str) -> pd.DataFrame:
        """
        Load from CSV file. Expected columns (flexible):
          time/timestamp/date, open, high, low, close, volume
        """
        path = self.csv_path
        if not path:
            # Try auto-discovery: BTC-USDT.csv or BTCUSDT.csv
            for candidate in [
                f"{symbol}.csv",
                f"{symbol.replace('-','')}.csv",
                f"data/{symbol}.csv",
            ]:
                if Path(candidate).exists():
                    path = candidate
                    break

        if not path or not Path(path).exists():
            raise FileNotFoundError(
                f"CSV not found for {symbol}. "
                f"Expected: {symbol}.csv or set csv_path= in DataProvider"
            )

        df = pd.read_csv(path)

        # Normalise column names
        df.columns = [c.lower().strip() for c in df.columns]
        rename = {}
        for col in df.columns:
            if col in ("timestamp", "date", "datetime", "open_time"):
                rename[col] = "time"
        df = df.rename(columns=rename)

        # Parse time column
        if "time" in df.columns:
            if df["time"].dtype == object:
                df["datetime"] = pd.to_datetime(df["time"])
                df["time"]     = df["datetime"].astype(int) // 10**9
            else:
                ts = df["time"].iloc[0]
                if ts > 1e12:   # milliseconds
                    df["time"] = df["time"] // 1000
                df["datetime"] = pd.to_datetime(df["time"], unit="s")

        required = ["open","high","low","close"]
        missing  = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"CSV missing columns: {missing}")

        df = df.astype({c: float for c in required if c in df.columns})
        return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)

    # ══════════════════════════════════════════════════════════════════════
    #  COINGECKO (free historical, daily only)
    # ══════════════════════════════════════════════════════════════════════

    _CG_IDS = {
        "BTC":"bitcoin","ETH":"ethereum","SOL":"solana","XRP":"ripple",
        "DOGE":"dogecoin","ADA":"cardano","DOT":"polkadot","AVAX":"avalanche-2",
        "LINK":"chainlink","LTC":"litecoin","BCH":"bitcoin-cash",
        "UNI":"uniswap","ATOM":"cosmos","XLM":"stellar","RVN":"ravencoin",
    }

    def _fetch_coingecko(self, symbol: str, days: int) -> pd.DataFrame:
        coin = symbol.split("-")[0].upper()
        cg_id = self._CG_IDS.get(coin)
        if not cg_id:
            raise ValueError(f"CoinGecko ID not known for {coin}")

        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
            params={"vs_currency":"usd","days":min(days,365)},
            timeout=15,
        )
        data   = resp.json()
        prices = data.get("prices", [])
        if not prices:
            return pd.DataFrame()

        df = pd.DataFrame(prices, columns=["time","close"])
        df["time"] = df["time"] // 1000
        # CoinGecko only provides close — set OHLV to close
        df["open"]   = df["close"]
        df["high"]   = df["close"]
        df["low"]    = df["close"]
        df["volume"] = 0.0
        df["datetime"] = pd.to_datetime(df["time"], unit="s")
        log.warning("[DATA] CoinGecko only provides daily close prices — OHLC accuracy limited")
        return df

    # ══════════════════════════════════════════════════════════════════════
    #  NORMALISE
    # ══════════════════════════════════════════════════════════════════════

    def _normalise(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure consistent column types and names."""
        if df is None or df.empty:
            return pd.DataFrame()
        for col in ["open","high","low","close","volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "datetime" not in df.columns and "time" in df.columns:
            df["datetime"] = pd.to_datetime(df["time"], unit="s")
        return df.dropna(subset=["close"]).reset_index(drop=True)

    # ══════════════════════════════════════════════════════════════════════
    #  SAVE TO CSV (for offline use later)
    # ══════════════════════════════════════════════════════════════════════

    def save_csv(self, df: pd.DataFrame, symbol: str, folder: str = "data"):
        """Save fetched data to CSV for offline backtesting later."""
        Path(folder).mkdir(exist_ok=True)
        path = f"{folder}/{symbol.replace('/', '-')}.csv"
        df.to_csv(path, index=False)
        log.info(f"[DATA] Saved {len(df)} candles to {path}")
        return path

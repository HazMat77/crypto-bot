"""
Exchange Adapters
==================
Unified interface for all supported exchanges.
Each adapter implements: get_price(), get_candles(), place_buy(), place_sell(), get_balance()

Supported: KuCoin, Binance, Kraken, Coinbase, Bybit, OKX, Gate.io, MEXC, Bitget, Huobi/HTX

Symbol format is normalized — config uses "BTC-USDT", each adapter converts
to its exchange's native format internally (e.g. Binance uses "BTCUSDT").
"""

import time
import logging
import requests
from retry_utils import retry
import hmac
import hashlib
import base64
import urllib.parse
from datetime import datetime
from ws_price_feed import build_ws_feed

log = logging.getLogger(__name__)

# ── Fee rates per exchange (maker/taker average) ───────────────────────────
EXCHANGE_FEES = {
    "kucoin":   0.001,    # 0.10%
    "binance":  0.001,    # 0.10%
    "kraken":   0.002,    # 0.20% (taker)
    "coinbase": 0.006,    # 0.60% (basic — Advanced Trade lower)
    "bybit":    0.001,    # 0.10%
    "okx":      0.001,    # 0.10%
    "gateio":   0.002,    # 0.20%
    "mexc":     0.002,    # 0.20%
    "bitget":   0.001,    # 0.10%
    "huobi":    0.002,    # 0.20%
    "webull":   0.001,    # 0.10% — matches standard Webull app crypto rate
    "virgocx":  0.002,    # 0.20% — VirgoCX standard tier taker fee (CAD pairs)
}


# ══════════════════════════════════════════════════════════════════════════════
#  BASE ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class BaseExchange:
    def __init__(self, name, credentials):
        self.name        = name
        self.credentials = credentials
        self.fee_rate    = EXCHANGE_FEES.get(name, 0.001)
        self._ws_feed    = None   # set by attach_ws_feed() after construction

    def attach_ws_feed(self, symbols: list):
        """
        Start a WebSocket price feed for this exchange.
        Call this after construction, passing the list of symbols the bot will trade.
        Exchanges without WS support (MEXC, Webull, VirgoCX) silently stay on REST.
        """
        self._ws_feed = build_ws_feed(self.name, symbols, self._rest_get_price)
        if self._ws_feed:
            log.info(f"[{self.name.upper()}] WebSocket price feed active")

    def resubscribe_ws_feed(self, symbols: list):
        """
        Call whenever active_coins for this exchange changes after startup
        (tier change, hourly news re-rank) so the live feed's subscription
        stays in sync with what the bot is actually trading. A no-op if
        this exchange never had a WS feed attached (MEXC/Webull/VirgoCX,
        or any exchange where attach_ws_feed() was never called) — those
        always use REST anyway, so there's nothing to keep in sync.
        """
        if self._ws_feed:
            self._ws_feed.update_symbols(symbols)

    def _rest_get_price(self, symbol: str) -> float:
        """Subclass REST implementation — called by WS feed as fallback."""
        raise NotImplementedError

    def normalize_symbol(self, symbol: str) -> str:
        """Override in subclass to convert BTC-USDT to exchange format."""
        return symbol

    def get_price(self, symbol: str) -> float:
        """Return price from WS cache if fresh, else fall back to REST."""
        if self._ws_feed:
            return self._ws_feed.get_price(symbol)
        return self._rest_get_price(symbol)

    def get_candles(self, symbol: str, interval: str, limit: int = 100):
        raise NotImplementedError

    def place_market_buy(self, symbol: str, usdt_amount: float) -> dict:
        raise NotImplementedError

    def place_market_sell(self, symbol: str, qty: float) -> dict:
        raise NotImplementedError

    def get_usdt_balance(self) -> float:
        raise NotImplementedError

    def get_coin_balance(self, coin: str) -> float:
        raise NotImplementedError

    def get_all_balances(self) -> dict:
        """
        Returns {coin: available_balance} for every asset with a nonzero
        balance on this exchange — not just the coins the bot happens to
        be actively tracking right now. This is the actual mechanism the
        startup self-checker (see startup_balance_check() in bot.py) uses
        to find positions the bot might otherwise lose track of after a
        crash or power failure: a coin the exchange shows a balance for,
        but that isn't in the bot's in-memory tracking dicts, would
        otherwise sit there un-managed indefinitely — no stop-loss, no
        take-profit, nothing watching it — until someone happens to
        notice manually.

        Override in each exchange subclass using whatever bulk-balance
        endpoint that exchange's API exposes (almost always far cheaper,
        rate-limit-wise, than calling get_coin_balance() once per coin
        across an entire account's worth of assets).

        Default raises NotImplementedError rather than returning an empty
        dict, specifically so a missing implementation fails LOUDLY at
        the one call site that matters (the startup self-checker) instead
        of silently reporting "no positions found" — which on a live
        account would be actively dangerous: it would tell you nothing's
        unwatched when the truth is just that nobody checked.
        """
        raise NotImplementedError(
            f"get_all_balances() is not implemented for the '{self.name}' adapter. "
            f"The startup self-checker (see bot.py) cannot verify this exchange "
            f"isn't holding an orphaned position until this is implemented."
        )


# ══════════════════════════════════════════════════════════════════════════════
#  KUCOIN
# ══════════════════════════════════════════════════════════════════════════════

class KuCoinExchange(BaseExchange):
    def __init__(self, credentials):
        super().__init__("kucoin", credentials)
        self._init_client()

    def _init_client(self):
        try:
            from kucoin.client import Market, Trade, User
            # KuCoin's SDK doesn't expose a per-call timeout, but most versions
            # accept a connect/read timeout via the underlying requests session.
            # We pass what's supported; the retry wrapper below is the real
            # safety net for "Read timed out" / "Connection aborted" errors.
            self.market = Market(url="https://api.kucoin.com")
            self.trade  = Trade(
                key=self.credentials["api_key"],
                secret=self.credentials["api_secret"],
                passphrase=self.credentials["passphrase"],
            )
            self.user   = User(
                key=self.credentials["api_key"],
                secret=self.credentials["api_secret"],
                passphrase=self.credentials["passphrase"],
            )
            self._version = "new"
        except ImportError:
            from kucoin.client import Client
            self._client  = Client(
                self.credentials["api_key"],
                self.credentials["api_secret"],
                self.credentials["passphrase"],
            )
            self.market = self._client
            self.trade  = self._client
            self.user   = self._client
            self._version = "old"

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def _rest_get_price(self, symbol):
        return float(self.market.get_ticker(symbol)["price"])

    # Maps KuCoin interval strings to seconds, used to compute an explicit
    # startAt so we never depend on whatever default window the installed
    # SDK version happens to use when startAt/endAt are omitted -- that
    # default has been observed to sometimes return only the most recent
    # 1-2 candles instead of real history, which silently starves every
    # RSI/MA-based decision in the bot (see get_candles below).
    _INTERVAL_SECONDS = {
        "1min": 60, "3min": 180, "5min": 300, "15min": 900, "30min": 1800,
        "1hour": 3600, "2hour": 7200, "4hour": 14400, "6hour": 21600,
        "8hour": 28800, "12hour": 43200, "1day": 86400, "1week": 604800,
    }

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_candles(self, symbol, interval="15min", limit=100):
        import pandas as pd
        import time as _time

        # Explicit time window -- never rely on the SDK's default startAt/
        # endAt, since that default varies by SDK version and has been
        # observed to return as little as a single candle. Request a
        # generous buffer (2x what's needed) so thin/illiquid pairs that
        # genuinely have gaps still come back with enough real candles.
        interval_secs = self._INTERVAL_SECONDS.get(interval, 900)
        now           = int(_time.time())
        start_at      = now - (interval_secs * limit * 2)

        if self._version == "new":
            raw = self.market.get_kline(symbol, interval, startAt=start_at, endAt=now)
        else:
            raw = self.market.get_kline_data(symbol, interval, start_at, now)

        # Defensive: some SDK versions return {"code": ..., "data": []} for
        # an empty result instead of a plain list (see
        # github.com/Kucoin/kucoin-python-sdk/issues/78). Normalise so the
        # rest of this function always sees a plain list either way.
        if isinstance(raw, dict):
            raw = raw.get("data", [])

        # A handful of rows can be genuine (a newly-listed or very thin
        # pair may not have much history yet) -- but a single row back
        # from a 50+-hour window request is not a real market condition,
        # it's a sign the request didn't actually apply (e.g. an SDK
        # version ignoring startAt/endAt). Raise so the @retry decorator
        # gets a chance to try again, instead of silently handing bot.py
        # a dataframe that's far too thin for any RSI/MA calculation.
        MIN_SANE_ROWS = 5
        if not raw or len(raw) < MIN_SANE_ROWS:
            raise ValueError(
                f"KuCoin returned only {len(raw)} candle(s) for {symbol} "
                f"({interval}) over a {limit * 2}-candle window — expected far "
                f"more; likely a thin/incomplete API response, retrying"
            )

        raw = list(reversed(raw))[-limit:]
        df  = pd.DataFrame(raw, columns=["time","open","close","high","low","volume","turnover"])
        return df.astype({"open":float,"close":float,"high":float,"low":float,"volume":float})

    def place_market_buy(self, symbol, usdt_amount):
        if self._version == "new":
            return self.trade.create_market_order(symbol, "buy", funds=str(round(usdt_amount, 2)))
        else:
            return self.trade.create_market_order(symbol, self.trade.SIDE_BUY, funds=str(round(usdt_amount, 2)))

    def place_market_sell(self, symbol, qty):
        if self._version == "new":
            return self.trade.create_market_order(symbol, "sell", size=str(qty))
        else:
            return self.trade.create_market_order(symbol, self.trade.SIDE_SELL, size=str(qty))

    def get_usdt_balance(self):
        if self._version == "new":
            accounts = self.user.get_account_list(currency="USDT", account_type="trade")
        else:
            accounts = self.user.get_accounts("USDT")
        return float(accounts[0]["available"]) if accounts else 0.0

    def get_coin_balance(self, coin):
        if self._version == "new":
            accounts = self.user.get_account_list(currency=coin, account_type="trade")
        else:
            accounts = self.user.get_accounts(coin)
        return float(accounts[0]["available"]) if accounts else 0.0

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_all_balances(self):
        """
        One call returns every trade-account balance on KuCoin, instead of
        one get_coin_balance() call per coin — both cheaper on API rate
        limits and the only way to discover a coin the bot doesn't already
        know to ask about (e.g. a position opened before a crash, with no
        in-memory record left to check it under).
        """
        if self._version == "new":
            accounts = self.user.get_account_list(account_type="trade")
        else:
            accounts = self.user.get_accounts()

        balances = {}
        for acct in accounts:
            try:
                bal = float(acct.get("balance", acct.get("available", 0)))
            except (TypeError, ValueError):
                continue
            if bal > 0:
                currency = acct.get("currency")
                if currency:
                    # An account can have multiple sub-balances per currency
                    # (e.g. trade + margin) — sum them under one key so the
                    # self-checker sees one true total per coin, not several
                    # partial entries that each look smaller than reality.
                    balances[currency] = balances.get(currency, 0.0) + bal
        return balances


# ══════════════════════════════════════════════════════════════════════════════
#  BINANCE
# ══════════════════════════════════════════════════════════════════════════════

class BinanceExchange(BaseExchange):
    BASE = "https://api.binance.com"

    def __init__(self, credentials):
        super().__init__("binance", credentials)

    def normalize_symbol(self, symbol):
        return symbol.replace("-", "")   # BTC-USDT → BTCUSDT

    def _sign(self, params):
        query = urllib.parse.urlencode(params)
        sig   = hmac.new(
            self.credentials["api_secret"].encode(),
            query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = sig
        return params

    def _headers(self):
        return {"X-MBX-APIKEY": self.credentials["api_key"]}

    def _rest_get_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/api/v3/ticker/price", params={"symbol": sym}, timeout=10)
        return float(resp.json()["price"])

    def get_candles(self, symbol, interval="15m", limit=100):
        import pandas as pd
        interval_map = {"1min":"1m","5min":"5m","15min":"15m","30min":"30m",
                        "1hour":"1h","4hour":"4h","1day":"1d"}
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/api/v3/klines", timeout=10,
                            params={"symbol":sym,"interval":interval_map.get(interval,interval),"limit":limit})
        raw  = resp.json()
        df   = pd.DataFrame(raw, columns=["time","open","high","low","close","volume",
                                           "close_time","qav","trades","tbbav","tbqav","ignore"])
        return df.astype({"open":float,"close":float,"high":float,"low":float,"volume":float})

    def place_market_buy(self, symbol, usdt_amount):
        sym    = self.normalize_symbol(symbol)
        params = self._sign({"symbol":sym,"side":"BUY","type":"MARKET",
                              "quoteOrderQty":round(usdt_amount,2),"timestamp":int(time.time()*1000)})
        resp   = requests.post(f"{self.BASE}/api/v3/order", params=params,
                               headers=self._headers(), timeout=10)
        return resp.json()

    def place_market_sell(self, symbol, qty):
        sym    = self.normalize_symbol(symbol)
        params = self._sign({"symbol":sym,"side":"SELL","type":"MARKET",
                              "quantity":qty,"timestamp":int(time.time()*1000)})
        resp   = requests.post(f"{self.BASE}/api/v3/order", params=params,
                               headers=self._headers(), timeout=10)
        return resp.json()

    def get_usdt_balance(self):
        params = self._sign({"timestamp": int(time.time()*1000)})
        resp   = requests.get(f"{self.BASE}/api/v3/account", params=params,
                              headers=self._headers(), timeout=10)
        bals   = resp.json().get("balances", [])
        usdt   = next((b for b in bals if b["asset"] == "USDT"), None)
        return float(usdt["free"]) if usdt else 0.0

    def get_coin_balance(self, coin):
        params = self._sign({"timestamp": int(time.time()*1000)})
        resp   = requests.get(f"{self.BASE}/api/v3/account", params=params,
                              headers=self._headers(), timeout=10)
        bals   = resp.json().get("balances", [])
        asset  = next((b for b in bals if b["asset"] == coin), None)
        return float(asset["free"]) if asset else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  KRAKEN
# ══════════════════════════════════════════════════════════════════════════════

class KrakenExchange(BaseExchange):
    BASE = "https://api.kraken.com"

    # Reverse of coin_discovery.py's KRAKEN_LEGACY_ASSET_MAP — converts a
    # normal coin ticker INTO Kraken's pair-code form when placing orders
    # or querying prices. Must stay the exact inverse of the discovery
    # mapping, or a coin discovered correctly (e.g. DOGE-USDT) would fail
    # to resolve back to a valid Kraken pair when the bot tries to
    # actually trade it (DOGE has no direct Kraken pair — it must be
    # converted back to XDGUSDT first).
    #
    # NOTE: deliberately does NOT use a blind substring .replace() the
    # way the old buggy version did — that approach corrupted any ticker
    # that happened to contain "BTC" as a substring anywhere, and only
    # handled Bitcoin, leaving every other legacy-prefixed asset (DOGE,
    # XMR, XRP, XLM, ETC, LTC, MLN, REP, ZEC) broken. This only rewrites
    # an EXACT, whole-ticker match against the known legacy list; every
    # other coin (AVAX, MATIC, SOL, etc.) passes through unmodified,
    # exactly mirroring how Kraken's own newer listings carry no prefix.
    LEGACY_COIN_TO_KRAKEN = {
        "BTC":  "XBT",
        "DOGE": "XDG",
        "ETC":  "XETC",
        "ETH":  "XETH",
        "LTC":  "XLTC",
        "MLN":  "XMLN",
        "REP":  "XREP",
        "XLM":  "XXLM",
        "XMR":  "XXMR",
        "XRP":  "XXRP",
        "ZEC":  "XZEC",
    }

    def __init__(self, credentials):
        super().__init__("kraken", credentials)

    def normalize_symbol(self, symbol):
        """
        Converts a normal "COIN-USDT" symbol into the exact pair code
        Kraken's API expects.

        IMPORTANT, hard-won finding: Kraken's legacy X-prefix convention
        (XETH, XXRP, XXMR, XLTC, etc.) applies to USD/EUR/native pairs,
        but its USDT pairs use the PLAIN ticker with no prefix at all —
        confirmed by direct inspection of Kraken's live AssetPairs data,
        which shows keys like "ADAUSDT", "ALGOUSDT", "ALEOUSDT" with zero
        prefix, for assets that DO carry an X-prefix on their USD pairs.
        An earlier version of this fix applied LEGACY_COIN_TO_KRAKEN
        unconditionally to all quote currencies, which produced
        "XETHUSDT", "XXRPUSDT", etc. — pair codes Kraken's API doesn't
        actually recognize for USDT markets, causing real "Invalid Pair"
        failures even though the coin names themselves were correct.
        Only Bitcoin (XBT) and Dogecoin (XDG) are confirmed to keep their
        prefix even on USDT pairs, per direct observation against the
        bot's actual production logs.
        """
        coin, _, quote = symbol.partition("-")
        if quote.upper() == "USDT":
            # USDT pairs: only BTC and DOGE need their prefix; everything
            # else (including XMR, XRP, LTC, ETH, etc.) uses the plain ticker.
            usdt_prefix_exceptions = {"BTC": "XBT", "DOGE": "XDG"}
            coin = usdt_prefix_exceptions.get(coin, coin)
        else:
            coin = self.LEGACY_COIN_TO_KRAKEN.get(coin, coin)
        return f"{coin}{quote}"

    def _sign(self, path, data):
        nonce    = str(int(time.time() * 1000))
        data["nonce"] = nonce
        post_data = urllib.parse.urlencode(data)
        encoded  = (nonce + post_data).encode()
        msg      = path.encode() + hashlib.sha256(encoded).digest()
        sig      = hmac.new(base64.b64decode(self.credentials["api_secret"]),
                             msg, hashlib.sha512)
        return base64.b64encode(sig.digest()).decode(), nonce

    def _rest_get_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/0/public/Ticker", params={"pair": sym}, timeout=10)
        data = resp.json()
        if data.get("error"):
            raise ValueError(f"Kraken: {data['error']}")
        pair_data = list(data["result"].values())[0]
        return float(pair_data["c"][0])

    def get_candles(self, symbol, interval="15", limit=100):
        import pandas as pd
        interval_map = {"1min":"1","5min":"5","15min":"15","30min":"30",
                        "1hour":"60","4hour":"240","1day":"1440"}
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/0/public/OHLC", timeout=10,
                            params={"pair":sym,"interval":interval_map.get(interval,"15")})
        data = resp.json()
        if data.get("error"):
            raise ValueError(f"Kraken candles: {data['error']}")
        raw = list(data["result"].values())[0][-limit:]
        df  = pd.DataFrame(raw, columns=["time","open","high","low","close","vwap","volume","count"])
        return df.astype({"open":float,"close":float,"high":float,"low":float,"volume":float})

    def place_market_buy(self, symbol, usdt_amount):
        price  = self.get_price(symbol)
        volume = round(usdt_amount / price, 6)
        path   = "/0/private/AddOrder"
        data   = {"pair": self.normalize_symbol(symbol), "type": "buy",
                  "ordertype": "market", "volume": str(volume)}
        sig, nonce = self._sign(path, data)
        resp = requests.post(f"{self.BASE}{path}", data=data, timeout=10,
                             headers={"API-Key": self.credentials["api_key"], "API-Sign": sig})
        return resp.json()

    def place_market_sell(self, symbol, qty):
        path = "/0/private/AddOrder"
        data = {"pair": self.normalize_symbol(symbol), "type": "sell",
                "ordertype": "market", "volume": str(qty)}
        sig, nonce = self._sign(path, data)
        resp = requests.post(f"{self.BASE}{path}", data=data, timeout=10,
                             headers={"API-Key": self.credentials["api_key"], "API-Sign": sig})
        return resp.json()

    def get_usdt_balance(self):
        path = "/0/private/Balance"
        sig, nonce = self._sign(path, {})
        resp = requests.post(f"{self.BASE}{path}", data={"nonce": nonce}, timeout=10,
                             headers={"API-Key": self.credentials["api_key"], "API-Sign": sig})
        bals = resp.json().get("result", {})
        return float(bals.get("USDT", 0))

    def get_coin_balance(self, coin):
        path = "/0/private/Balance"
        sig, nonce = self._sign(path, {})
        resp = requests.post(f"{self.BASE}{path}", data={"nonce": nonce}, timeout=10,
                             headers={"API-Key": self.credentials["api_key"], "API-Sign": sig})
        bals = resp.json().get("result", {})
        # Exact-match lookup against the legacy table, same as normalize_symbol —
        # NOT a substring .replace(), which previously mishandled every
        # legacy-prefixed coin except Bitcoin and corrupted modern tickers.
        kraken_coin = self.LEGACY_COIN_TO_KRAKEN.get(coin, coin)
        return float(bals.get(kraken_coin, bals.get(f"X{kraken_coin}", 0)))


# ══════════════════════════════════════════════════════════════════════════════
#  BYBIT
# ══════════════════════════════════════════════════════════════════════════════

class BybitExchange(BaseExchange):
    BASE = "https://api.bybit.com"

    def __init__(self, credentials):
        super().__init__("bybit", credentials)

    def normalize_symbol(self, symbol):
        return symbol.replace("-", "")   # BTC-USDT → BTCUSDT

    def _sign(self, params):
        ts     = str(int(time.time() * 1000))
        recv   = "5000"
        param_str = ts + self.credentials["api_key"] + recv + urllib.parse.urlencode(sorted(params.items()))
        sig    = hmac.new(self.credentials["api_secret"].encode(), param_str.encode(), hashlib.sha256).hexdigest()
        return sig, ts

    def _rest_get_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/v5/market/tickers",
                            params={"category":"spot","symbol":sym}, timeout=10)
        data = resp.json()
        return float(data["result"]["list"][0]["lastPrice"])

    def get_candles(self, symbol, interval="15", limit=100):
        import pandas as pd
        interval_map = {"1min":"1","5min":"5","15min":"15","30min":"30",
                        "1hour":"60","4hour":"240","1day":"D"}
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/v5/market/kline", timeout=10,
                            params={"category":"spot","symbol":sym,
                                    "interval":interval_map.get(interval,"15"),"limit":limit})
        raw  = resp.json()["result"]["list"]
        raw  = list(reversed(raw))
        df   = pd.DataFrame(raw, columns=["time","open","high","low","close","volume","turnover"])
        return df.astype({"open":float,"close":float,"high":float,"low":float,"volume":float})

    def place_market_buy(self, symbol, usdt_amount):
        sym    = self.normalize_symbol(symbol)
        params = {"category":"spot","symbol":sym,"side":"Buy",
                  "orderType":"Market","qty":str(round(usdt_amount,2)),"marketUnit":"quoteCoin"}
        sig, ts = self._sign(params)
        resp = requests.post(f"{self.BASE}/v5/order/create", json=params, timeout=10,
                             headers={"X-BAPI-API-KEY":self.credentials["api_key"],
                                      "X-BAPI-SIGN":sig,"X-BAPI-TIMESTAMP":ts,
                                      "X-BAPI-RECV-WINDOW":"5000"})
        return resp.json()

    def place_market_sell(self, symbol, qty):
        sym    = self.normalize_symbol(symbol)
        params = {"category":"spot","symbol":sym,"side":"Sell",
                  "orderType":"Market","qty":str(qty)}
        sig, ts = self._sign(params)
        resp = requests.post(f"{self.BASE}/v5/order/create", json=params, timeout=10,
                             headers={"X-BAPI-API-KEY":self.credentials["api_key"],
                                      "X-BAPI-SIGN":sig,"X-BAPI-TIMESTAMP":ts,
                                      "X-BAPI-RECV-WINDOW":"5000"})
        return resp.json()

    def get_usdt_balance(self):
        params = {"accountType": "UNIFIED"}
        sig, ts = self._sign(params)
        resp = requests.get(f"{self.BASE}/v5/account/wallet-balance", params=params, timeout=10,
                            headers={"X-BAPI-API-KEY":self.credentials["api_key"],
                                     "X-BAPI-SIGN":sig,"X-BAPI-TIMESTAMP":ts,
                                     "X-BAPI-RECV-WINDOW":"5000"})
        coins = resp.json().get("result",{}).get("list",[{}])[0].get("coin",[])
        usdt  = next((c for c in coins if c["coin"] == "USDT"), None)
        return float(usdt["availableToWithdraw"]) if usdt else 0.0

    def get_coin_balance(self, coin):
        params = {"accountType": "UNIFIED"}
        sig, ts = self._sign(params)
        resp = requests.get(f"{self.BASE}/v5/account/wallet-balance", params=params, timeout=10,
                            headers={"X-BAPI-API-KEY":self.credentials["api_key"],
                                     "X-BAPI-SIGN":sig,"X-BAPI-TIMESTAMP":ts,
                                     "X-BAPI-RECV-WINDOW":"5000"})
        coins = resp.json().get("result",{}).get("list",[{}])[0].get("coin",[])
        c     = next((x for x in coins if x["coin"] == coin), None)
        return float(c["availableToWithdraw"]) if c else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  OKX
# ══════════════════════════════════════════════════════════════════════════════

class OKXExchange(BaseExchange):
    BASE = "https://www.okx.com"

    def __init__(self, credentials):
        super().__init__("okx", credentials)

    def normalize_symbol(self, symbol):
        return symbol   # OKX uses BTC-USDT natively

    def _sign(self, method, path, body=""):
        ts  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        msg = ts + method.upper() + path + body
        sig = base64.b64encode(
            hmac.new(self.credentials["api_secret"].encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()
        return sig, ts

    def _headers(self, method, path, body=""):
        sig, ts = self._sign(method, path, body)
        return {
            "OK-ACCESS-KEY":        self.credentials["api_key"],
            "OK-ACCESS-SIGN":       sig,
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": self.credentials.get("passphrase",""),
            "Content-Type":         "application/json",
        }

    def _rest_get_price(self, symbol):
        resp = requests.get(f"{self.BASE}/api/v5/market/ticker",
                            params={"instId": symbol}, timeout=10)
        return float(resp.json()["data"][0]["last"])

    def get_candles(self, symbol, interval="15m", limit=100):
        import pandas as pd
        interval_map = {"1min":"1m","5min":"5m","15min":"15m","30min":"30m",
                        "1hour":"1H","4hour":"4H","1day":"1Dutc"}
        resp = requests.get(f"{self.BASE}/api/v5/market/candles", timeout=10,
                            params={"instId":symbol,
                                    "bar":interval_map.get(interval,"15m"),"limit":limit})
        raw = list(reversed(resp.json()["data"]))
        df  = pd.DataFrame(raw, columns=["time","open","high","low","close","vol","volCcy","volCcyQuote","confirm"])
        return df.astype({"open":float,"close":float,"high":float,"low":float,"vol":float})

    def place_market_buy(self, symbol, usdt_amount):
        import json
        body = json.dumps({"instId":symbol,"tdMode":"cash","side":"buy",
                           "ordType":"market","sz":str(round(usdt_amount,2)),"tgtCcy":"quote_ccy"})
        path = "/api/v5/trade/order"
        resp = requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                             headers=self._headers("POST", path, body))
        return resp.json()

    def place_market_sell(self, symbol, qty):
        import json
        body = json.dumps({"instId":symbol,"tdMode":"cash","side":"sell",
                           "ordType":"market","sz":str(qty)})
        path = "/api/v5/trade/order"
        resp = requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                             headers=self._headers("POST", path, body))
        return resp.json()

    def get_usdt_balance(self):
        path = "/api/v5/account/balance"
        resp = requests.get(f"{self.BASE}{path}", timeout=10,
                            headers=self._headers("GET", path))
        details = resp.json().get("data",[{}])[0].get("details",[])
        usdt = next((d for d in details if d["ccy"] == "USDT"), None)
        return float(usdt["availEq"]) if usdt else 0.0

    def get_coin_balance(self, coin):
        path = "/api/v5/account/balance"
        resp = requests.get(f"{self.BASE}{path}", timeout=10,
                            headers=self._headers("GET", path))
        details = resp.json().get("data",[{}])[0].get("details",[])
        c = next((d for d in details if d["ccy"] == coin), None)
        return float(c["availEq"]) if c else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  GATE.IO
# ══════════════════════════════════════════════════════════════════════════════

class GateIOExchange(BaseExchange):
    BASE = "https://api.gateio.ws/api/v4"

    def __init__(self, credentials):
        super().__init__("gateio", credentials)

    def normalize_symbol(self, symbol):
        return symbol.replace("-", "_")   # BTC-USDT → BTC_USDT

    def _sign(self, method, path, query="", body=""):
        ts        = str(int(time.time()))
        body_hash = hashlib.sha512(body.encode()).hexdigest()
        msg       = f"{method}\n{path}\n{query}\n{body_hash}\n{ts}"
        sig       = hmac.new(self.credentials["api_secret"].encode(), msg.encode(), hashlib.sha512).hexdigest()
        return {"KEY": self.credentials["api_key"], "Timestamp": ts, "SIGN": sig}

    def _rest_get_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/spot/tickers", params={"currency_pair": sym}, timeout=10)
        return float(resp.json()[0]["last"])

    def get_candles(self, symbol, interval="15m", limit=100):
        import pandas as pd
        interval_map = {"1min":"1m","5min":"5m","15min":"15m","30min":"30m",
                        "1hour":"1h","4hour":"4h","1day":"1d"}
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/spot/candlesticks", timeout=10,
                            params={"currency_pair":sym,
                                    "interval":interval_map.get(interval,"15m"),"limit":limit})
        raw = resp.json()
        df  = pd.DataFrame(raw, columns=["time","volume","close","high","low","open","turnover","confirm"])
        return df.astype({"open":float,"close":float,"high":float,"low":float,"volume":float})

    def place_market_buy(self, symbol, usdt_amount):
        import json
        sym  = self.normalize_symbol(symbol)
        body = json.dumps({"currency_pair":sym,"side":"buy","type":"market",
                           "amount":str(round(usdt_amount,2))})
        path = "/spot/orders"
        resp = requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                             headers={**self._sign("POST", path, body=body), "Content-Type":"application/json"})
        return resp.json()

    def place_market_sell(self, symbol, qty):
        import json
        sym  = self.normalize_symbol(symbol)
        body = json.dumps({"currency_pair":sym,"side":"sell","type":"market","amount":str(qty)})
        path = "/spot/orders"
        resp = requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                             headers={**self._sign("POST", path, body=body), "Content-Type":"application/json"})
        return resp.json()

    def get_usdt_balance(self):
        path = "/spot/accounts"
        resp = requests.get(f"{self.BASE}{path}", timeout=10,
                            headers=self._sign("GET", path))
        accs = resp.json()
        usdt = next((a for a in accs if a["currency"] == "USDT"), None)
        return float(usdt["available"]) if usdt else 0.0

    def get_coin_balance(self, coin):
        path = "/spot/accounts"
        resp = requests.get(f"{self.BASE}{path}", timeout=10,
                            headers=self._sign("GET", path))
        accs = resp.json()
        c    = next((a for a in accs if a["currency"] == coin), None)
        return float(c["available"]) if c else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  MEXC
# ══════════════════════════════════════════════════════════════════════════════

class MEXCExchange(BaseExchange):
    BASE = "https://api.mexc.com"

    def __init__(self, credentials):
        super().__init__("mexc", credentials)

    def normalize_symbol(self, symbol):
        return symbol.replace("-", "")   # BTC-USDT → BTCUSDT

    def _sign(self, params):
        params["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(sorted(params.items()))
        sig   = hmac.new(self.credentials["api_secret"].encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _rest_get_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/api/v3/ticker/price", params={"symbol":sym}, timeout=10)
        return float(resp.json()["price"])

    def get_candles(self, symbol, interval="15m", limit=100):
        import pandas as pd
        interval_map = {"1min":"1m","5min":"5m","15min":"15m","30min":"30m",
                        "1hour":"1h","4hour":"4h","1day":"1d"}
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/api/v3/klines", timeout=10,
                            params={"symbol":sym,"interval":interval_map.get(interval,"15m"),"limit":limit})
        raw  = resp.json()
        df   = pd.DataFrame(raw, columns=["time","open","high","low","close","volume",
                                           "close_time","quote_vol","trades","taker_buy","taker_sell","ignore"])
        return df.astype({"open":float,"close":float,"high":float,"low":float,"volume":float})

    def place_market_buy(self, symbol, usdt_amount):
        sym    = self.normalize_symbol(symbol)
        params = self._sign({"symbol":sym,"side":"BUY","type":"MARKET","quoteOrderQty":round(usdt_amount,2)})
        resp   = requests.post(f"{self.BASE}/api/v3/order", params=params,
                               headers={"X-MEXC-APIKEY": self.credentials["api_key"]}, timeout=10)
        return resp.json()

    def place_market_sell(self, symbol, qty):
        sym    = self.normalize_symbol(symbol)
        params = self._sign({"symbol":sym,"side":"SELL","type":"MARKET","quantity":qty})
        resp   = requests.post(f"{self.BASE}/api/v3/order", params=params,
                               headers={"X-MEXC-APIKEY": self.credentials["api_key"]}, timeout=10)
        return resp.json()

    def get_usdt_balance(self):
        params = self._sign({})
        resp   = requests.get(f"{self.BASE}/api/v3/account", params=params,
                              headers={"X-MEXC-APIKEY": self.credentials["api_key"]}, timeout=10)
        bals   = resp.json().get("balances", [])
        usdt   = next((b for b in bals if b["asset"] == "USDT"), None)
        return float(usdt["free"]) if usdt else 0.0

    def get_coin_balance(self, coin):
        params = self._sign({})
        resp   = requests.get(f"{self.BASE}/api/v3/account", params=params,
                              headers={"X-MEXC-APIKEY": self.credentials["api_key"]}, timeout=10)
        bals   = resp.json().get("balances", [])
        c      = next((b for b in bals if b["asset"] == coin), None)
        return float(c["free"]) if c else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  WEBULL (crypto only — wraps the official webull-openapi-python-sdk)
# ══════════════════════════════════════════════════════════════════════════════
#
#  IMPORTANT — read before enabling:
#
#  1. Requires an approved Webull OpenAPI application (App Key + App Secret).
#     Apply at developer.webull.com under "OpenAPI Management" — approval
#     typically takes 1-2 business days. Until approved, you can only use
#     the shared sandbox test credentials, not your real account.
#
#  2. Install the official SDK first:
#       pip install webull-openapi-python-sdk
#
#  3. This adapter deliberately wraps Webull's OFFICIAL SDK rather than
#     hand-rolling their HMAC-SHA1 request signing. Webull's own docs warn
#     that signature mismatches are the most common integration error, and
#     the official SDK handles signing automatically — reimplementing that
#     by hand for a real-money trading bot isn't worth the risk.
#
#  4. Webull's crypto trading session requires a ONE-TIME interactive
#     approval the first time the SDK authenticates: it polls for up to
#     5 minutes waiting for you to approve a push notification inside the
#     Webull mobile app. This happens once per credential set, not on every
#     bot restart, but the FIRST run after entering new Webull credentials
#     will pause here — check your phone if get_account_list() hangs.
#
#  5. Set "sandbox": True in config.py while testing — this points at
#     Webull's UAT environment instead of production, so no real trades
#     happen until you explicitly flip it to False.
# ══════════════════════════════════════════════════════════════════════════════

class WebullExchange(BaseExchange):

    PROD_ENDPOINT = "api.webull.com"
    UAT_ENDPOINT  = "us-openapi-alb.uat.webullbroker.com"

    # Webull crypto trades against USD, not USDT — pairs are like "BTCUSD"
    def __init__(self, credentials):
        super().__init__("webull", credentials)
        self._account_id = None
        self._init_client()

    def _init_client(self):
        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient
            from webull.market.market_client import MarketClient
        except ImportError:
            raise ImportError(
                "Webull SDK not installed. Run: pip install webull-openapi-python-sdk\n"
                "See https://developer.webull.com/apis/docs/sdk/ for details."
            )

        sandbox  = self.credentials.get("sandbox", True)
        endpoint = self.UAT_ENDPOINT if sandbox else self.PROD_ENDPOINT
        region   = self.credentials.get("region", "us")

        self.api_client = ApiClient(
            self.credentials["api_key"],
            self.credentials["api_secret"],
            region,
        )
        self.api_client.add_endpoint(region, endpoint)

        self.trade  = TradeClient(self.api_client)
        self.market = MarketClient(self.api_client)

        if sandbox:
            log.warning("[WEBULL] Running in SANDBOX mode — no real trades will execute. "
                       "Set credentials['sandbox']=False in config.py when ready for live trading.")

        # Resolve account_id once — every trade call needs it
        try:
            accounts = self.trade.account_v2.get_account_list().json()
            self._account_id = accounts["accounts"][0]["account_id"]
        except Exception as e:
            log.error(f"[WEBULL] Could not fetch account list — auth may require "
                     f"mobile app approval on first run: {e}")
            raise

    def normalize_symbol(self, symbol):
        # config uses "BTC-USDT" — Webull crypto pairs are quoted in USD, no dash
        coin = symbol.split("-")[0]
        return f"{coin}USD"

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def _rest_get_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = self.market.crypto.get_snapshot(symbols=[sym]).json()
        return float(resp["data"][0]["close"])

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_candles(self, symbol, interval="15min", limit=100):
        import pandas as pd
        sym = self.normalize_symbol(symbol)
        # Webull bar intervals: m1, m5, m15, m30, h1, h4, d1
        interval_map = {"1min":"m1","5min":"m5","15min":"m15","30min":"m30",
                        "1hour":"h1","4hour":"h4","1day":"d1"}
        resp = self.market.crypto.get_bars(
            symbol=sym, interval=interval_map.get(interval, "m15"), count=limit
        ).json()
        raw = resp.get("data", [])
        df  = pd.DataFrame(raw)
        df  = df.rename(columns={
            "timestamp": "time", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        })
        return df.astype({"open": float, "close": float, "high": float,
                          "low": float, "volume": float})

    def place_market_buy(self, symbol, usdt_amount):
        sym = self.normalize_symbol(symbol)
        order = {
            "client_order_id":  f"bot_{int(time.time()*1000)}",
            "combo_type":       "NORMAL",
            "symbol":           sym,
            "instrument_type":  "CRYPTO",
            "order_type":       "MARKET",
            "side":             "BUY",
            "entrust_type":     "CASH",     # dollar-amount buy, not coin quantity
            "quantity":         str(round(usdt_amount, 2)),
            "time_in_force":    "DAY",
        }
        return self.trade.order_v2.place_orders(
            account_id=self._account_id, new_orders=[order]
        ).json()

    def place_market_sell(self, symbol, qty):
        sym = self.normalize_symbol(symbol)
        order = {
            "client_order_id":  f"bot_{int(time.time()*1000)}",
            "combo_type":       "NORMAL",
            "symbol":           sym,
            "instrument_type":  "CRYPTO",
            "order_type":       "MARKET",
            "side":             "SELL",
            "entrust_type":     "QTY",      # coin-quantity sell
            "quantity":         str(qty),
            "time_in_force":    "DAY",
        }
        return self.trade.order_v2.place_orders(
            account_id=self._account_id, new_orders=[order]
        ).json()

    def get_usdt_balance(self):
        # Webull settles in USD cash, not USDT — treated as equivalent for bot purposes
        resp = self.trade.account_v2.get_account_balance(account_id=self._account_id).json()
        return float(resp.get("cash_balance", {}).get("settled_cash", 0.0))

    def get_coin_balance(self, coin):
        resp = self.trade.account_v2.get_positions(account_id=self._account_id).json()
        positions = resp.get("positions", [])
        match = next((p for p in positions
                     if p.get("symbol", "").upper().startswith(coin.upper())), None)
        return float(match["quantity"]) if match else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  VIRGOCX (Canadian exchange, CAD-quoted pairs — wraps the vcx-py client)
# ══════════════════════════════════════════════════════════════════════════════
#
#  IMPORTANT — read before enabling:
#
#  1. Requires a VirgoCX account with API access enabled. Generate your
#     API key and secret at https://virgocx.ca/en-virgocx-api
#     Your machine's IP address must be WHITELISTED in your VirgoCX API
#     settings before any request will succeed — orders will fail with
#     an auth error otherwise.
#
#  2. Install the community Python client first:
#       pip install vcx-py
#
#  3. This adapter wraps vcx-py rather than hand-rolling VirgoCX's MD5
#     signature scheme. VirgoCX's own published documentation contains a
#     worked signature example that does not reproduce to its own stated
#     hash when computed directly — meaning the exact byte-level parameter
#     formatting (decimal precision, type coercion) isn't fully verifiable
#     from the docs alone. Rather than guess and ship unverified signing
#     logic for a tool that places real trades, this wraps the actively
#     maintained client library instead.
#
#  4. ALL VirgoCX pairs are quoted in CAD, not USDT (e.g. "BTC/CAD", not
#     "BTC-USDT"). This adapter converts symbols automatically, but it
#     means your "USDT pool" concept doesn't apply here — VirgoCX trades
#     use CAD as the base currency. get_usdt_balance() returns your CAD
#     balance for compatibility with the rest of the bot's pool logic.
#
#  5. Known issue from the client library's own documentation: VirgoCX
#     has no endpoint to check if trading is paused exchange-wide. If
#     trading is paused, calls may raise KeyError rather than a clean
#     error message. The retry wrapper will NOT help in that case since
#     it's a data-shape problem, not a transient failure — if you see
#     repeated KeyErrors from this adapter, check virgocx.ca directly for
#     a maintenance notice before assuming the bot is broken.
# ══════════════════════════════════════════════════════════════════════════════

class VirgoCXExchange(BaseExchange):

    def __init__(self, credentials):
        super().__init__("virgocx", credentials)
        self._init_client()

    def _init_client(self):
        try:
            import vcx_py as vcx
        except ImportError:
            raise ImportError(
                "VirgoCX client not installed. Run: pip install vcx-py\n"
                "See https://github.com/aarjaneiro/vcx-py for details."
            )
        self._vcx = vcx
        self.client = vcx.VirgoCXClient(
            api_key=self.credentials["api_key"],
            api_secret=self.credentials["api_secret"],
        )

    def normalize_symbol(self, symbol):
        # config uses "BTC-USDT" — VirgoCX pairs are CAD-quoted, e.g. "BTC/CAD"
        coin = symbol.split("-")[0]
        return f"{coin}/CAD"

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def _rest_get_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        data = self.client.get_ticker_data(symbol=sym)
        return float(data["last"])

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_candles(self, symbol, interval="15min", limit=100):
        import pandas as pd
        sym = self.normalize_symbol(symbol)
        # VirgoCX K-line periods (minutes): 1,5,10,30,60,240,1440,7200,10080,43200
        interval_map = {"1min":1, "5min":5, "15min":10, "30min":30,
                        "1hour":60, "4hour":240, "1day":1440}
        period = interval_map.get(interval, 10)   # 15min has no exact match — use 10min
        raw = self.client.get_kline_data(symbol=sym, period=period)
        df  = pd.DataFrame(raw)
        df  = df.rename(columns={"createTime": "time"})
        # VirgoCX kline has no volume field in the documented response —
        # fill with zeros so downstream volume-based filters don't crash,
        # but they will have no real signal from VirgoCX candles.
        if "volume" not in df.columns:
            df["volume"] = 0.0
        return df.astype({"open": float, "high": float,
                          "low": float, "close": float, "volume": float}).tail(limit)

    def place_market_buy(self, symbol, usdt_amount):
        # "usdt_amount" here is actually a CAD amount — VirgoCX has no USDT pairs.
        sym = self.normalize_symbol(symbol)
        return self.client.place_order(
            sym, self._vcx.Enums.OrderType.MARKET,
            self._vcx.Enums.OrderDirection.BUY, total=round(usdt_amount, 2)
        )

    def place_market_sell(self, symbol, qty):
        sym = self.normalize_symbol(symbol)
        return self.client.place_order(
            sym, self._vcx.Enums.OrderType.MARKET,
            self._vcx.Enums.OrderDirection.SELL, qty=qty
        )

    def get_usdt_balance(self):
        # No USDT on VirgoCX — returns CAD balance instead so the bot's
        # pool-sizing logic still works. Be aware this is CAD, not USD/USDT.
        accounts = self.client.account_info()
        cad = next((a for a in accounts if a.get("coinName") == "CAD"), None)
        return float(cad["balance"]) if cad else 0.0

    def get_coin_balance(self, coin):
        accounts = self.client.account_info()
        match = next((a for a in accounts if a.get("coinName") == coin.upper()), None)
        return float(match["balance"]) if match else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  COINBASE (Advanced Trade API v3 — JWT / EC-key auth)
# ══════════════════════════════════════════════════════════════════════════════
#
#  Credentials needed (generate at https://www.coinbase.com/settings/api):
#    api_key    — API key name, format: organizations/{org_id}/apiKeys/{key_id}
#    api_secret — EC private key in PEM format
#                 (full -----BEGIN EC PRIVATE KEY----- block, newlines preserved)
#
#  Requires: pip install cryptography
#
#  Symbol format: Coinbase uses BTC-USDT natively — no conversion needed.
#  Note: Coinbase primarily lists USD pairs (BTC-USD). USDT pairs exist but
#  tend to be thinner than on Binance/KuCoin — check liquidity before trading.
# ══════════════════════════════════════════════════════════════════════════════

class CoinbaseExchange(BaseExchange):
    BASE = "https://api.coinbase.com"

    def __init__(self, credentials):
        super().__init__("coinbase", credentials)

    def normalize_symbol(self, symbol):
        return symbol   # Coinbase uses BTC-USDT natively

    def _jwt(self, method, path):
        """Build a per-request JWT for Coinbase Advanced Trade API auth."""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            raise ImportError(
                "Coinbase adapter requires the 'cryptography' package. "
                "Run: pip install cryptography"
            )
        import json as _json

        key_name = self.credentials["api_key"]
        pem      = self.credentials["api_secret"]
        if isinstance(pem, str):
            pem = pem.encode()

        private_key = serialization.load_pem_private_key(
            pem, password=None, backend=default_backend()
        )

        now  = int(time.time())
        hdr  = {"alg": "ES256", "kid": key_name}
        body = {
            "sub": key_name,
            "iss": "coinbase-cloud",
            "nbf": now,
            "exp": now + 120,
            "aud": ["retail_rest_api_proxy"],
            "uri": f"{method} api.coinbase.com{path}",
        }

        def _b64url(obj):
            return base64.urlsafe_b64encode(
                _json.dumps(obj, separators=(",", ":")).encode()
            ).rstrip(b"=").decode()

        signing_input = f"{_b64url(hdr)}.{_b64url(body)}".encode()
        der_sig = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s    = decode_dss_signature(der_sig)
        raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        sig_b64 = base64.urlsafe_b64encode(raw_sig).rstrip(b"=").decode()

        return f"{_b64url(hdr)}.{_b64url(body)}.{sig_b64}"

    def _auth_headers(self, method, path):
        return {
            "Authorization": f"Bearer {self._jwt(method, path)}",
            "Content-Type":  "application/json",
        }

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def _rest_get_price(self, symbol):
        path = "/api/v3/brokerage/best_bid_ask"
        resp = requests.get(
            f"{self.BASE}{path}",
            params={"product_ids": symbol},
            headers=self._auth_headers("GET", path),
            timeout=10,
        )
        pricebooks = resp.json().get("pricebooks", [])
        if not pricebooks:
            raise ValueError(f"Coinbase: no pricebook for {symbol}")
        book = pricebooks[0]
        asks, bids = book.get("asks", []), book.get("bids", [])
        if asks and bids:
            return (float(asks[0]["price"]) + float(bids[0]["price"])) / 2
        elif asks:
            return float(asks[0]["price"])
        elif bids:
            return float(bids[0]["price"])
        raise ValueError(f"Coinbase: empty order book for {symbol}")

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_candles(self, symbol, interval="15min", limit=100):
        import pandas as pd
        granularity_map = {
            "1min":  "ONE_MINUTE",     "5min":  "FIVE_MINUTE",
            "15min": "FIFTEEN_MINUTE", "30min": "THIRTY_MINUTE",
            "1hour": "ONE_HOUR",       "2hour": "TWO_HOUR",
            "4hour": "TWO_HOUR",       "6hour": "SIX_HOUR",
            "1day":  "ONE_DAY",
        }
        secs_map = {
            "ONE_MINUTE": 60,  "FIVE_MINUTE": 300,  "FIFTEEN_MINUTE": 900,
            "THIRTY_MINUTE": 1800, "ONE_HOUR": 3600, "TWO_HOUR": 7200,
            "SIX_HOUR": 21600, "ONE_DAY": 86400,
        }
        gran  = granularity_map.get(interval, "FIFTEEN_MINUTE")
        end   = int(time.time())
        start = end - secs_map[gran] * limit
        path  = f"/api/v3/brokerage/products/{symbol}/candles"
        resp  = requests.get(
            f"{self.BASE}{path}",
            params={"start": str(start), "end": str(end),
                    "granularity": gran, "limit": min(limit, 300)},
            headers=self._auth_headers("GET", path),
            timeout=10,
        )
        candles = resp.json().get("candles", [])
        if not candles:
            raise ValueError(f"Coinbase: no candles for {symbol}")
        # Coinbase returns newest-first — reverse to chronological order
        df = pd.DataFrame(list(reversed(candles)))
        df = df.rename(columns={"start": "time"})
        return df.astype({"open": float, "close": float,
                          "high": float, "low": float, "volume": float})

    def place_market_buy(self, symbol, usdt_amount):
        import json as _json
        path = "/api/v3/brokerage/orders"
        body = _json.dumps({
            "client_order_id": f"bot_{int(time.time() * 1000)}",
            "product_id": symbol,
            "side": "BUY",
            "order_configuration": {
                "market_market_ioc": {"quote_size": str(round(usdt_amount, 2))}
            },
        })
        resp = requests.post(
            f"{self.BASE}{path}", data=body,
            headers=self._auth_headers("POST", path), timeout=10,
        )
        return resp.json()

    def place_market_sell(self, symbol, qty):
        import json as _json
        path = "/api/v3/brokerage/orders"
        body = _json.dumps({
            "client_order_id": f"bot_{int(time.time() * 1000)}",
            "product_id": symbol,
            "side": "SELL",
            "order_configuration": {
                "market_market_ioc": {"base_size": str(qty)}
            },
        })
        resp = requests.post(
            f"{self.BASE}{path}", data=body,
            headers=self._auth_headers("POST", path), timeout=10,
        )
        return resp.json()

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_usdt_balance(self):
        return self._fetch_balance("USDT")

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_coin_balance(self, coin):
        return self._fetch_balance(coin)

    def _fetch_balance(self, currency):
        path     = "/api/v3/brokerage/accounts"
        resp     = requests.get(f"{self.BASE}{path}",
                                headers=self._auth_headers("GET", path), timeout=10)
        accounts = resp.json().get("accounts", [])
        match    = next((a for a in accounts if a.get("currency") == currency), None)
        return float(match["available_balance"]["value"]) if match else 0.0

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_all_balances(self):
        path     = "/api/v3/brokerage/accounts"
        resp     = requests.get(f"{self.BASE}{path}",
                                headers=self._auth_headers("GET", path), timeout=10)
        accounts = resp.json().get("accounts", [])
        balances = {}
        for acct in accounts:
            currency = acct.get("currency")
            try:
                val = float(acct["available_balance"]["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if val > 0 and currency:
                balances[currency] = balances.get(currency, 0.0) + val
        return balances


# ══════════════════════════════════════════════════════════════════════════════
#  FACTORY — returns the right adapter for each exchange name
# ══════════════════════════════════════════════════════════════════════════════

def build_exchanges(exchange_config: dict) -> dict:
    """
    Reads config.EXCHANGES, builds and returns only the enabled ones.
    Returns dict: { "kucoin": KuCoinExchange, "binance": BinanceExchange, ... }
    """
    adapters = {
        "kucoin":    KuCoinExchange,
        "binance":   BinanceExchange,
        "kraken":    KrakenExchange,
        "bybit":     BybitExchange,
        "okx":       OKXExchange,
        "gateio":    GateIOExchange,
        "mexc":      MEXCExchange,
        "webull":    WebullExchange,
        "virgocx":   VirgoCXExchange,
        "coinbase":  CoinbaseExchange,
    }

    # Exchanges not yet fully implemented — log clearly
    not_implemented = ["bitget", "huobi"]

    result = {}
    for name, cfg in exchange_config.items():
        if not cfg.get("enabled", False):
            continue
        if not cfg.get("api_key", "").strip() or cfg["api_key"].startswith("YOUR_"):
            log.warning(f"[{name.upper()}] Skipped — API key not filled in")
            continue
        if name in not_implemented:
            log.warning(f"[{name.upper()}] Not yet implemented — coming soon")
            continue
        if name not in adapters:
            log.warning(f"[{name.upper()}] Unknown exchange — skipping")
            continue
        try:
            result[name] = adapters[name](cfg)
            log.info(f"[{name.upper()}] ✅ Exchange loaded")
        except Exception as e:
            log.error(f"[{name.upper()}] Failed to load: {e}")

    return result

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

    # ── FUTURES / PERPETUALS (opt-in — see config.FUTURES_ENABLED) ─────────
    #
    # Every method below defaults to "not supported" so that an exchange
    # adapter which hasn't implemented futures fails LOUDLY and immediately
    # (futures_manager.py checks futures_supported() before ever calling
    # these) rather than silently no-opping on what would be a real-money
    # short/long order. Leverage is intentionally hard-capped at 1x across
    # this entire bot (see config.MAX_LEVERAGE) — these methods exist to
    # let the bot go SHORT (something spot can never do) and to hedge,
    # not to amplify position size. No adapter here should ever be called
    # with a leverage value other than 1.
    def futures_supported(self) -> bool:
        return False

    def set_leverage(self, symbol: str, leverage: int = 1) -> None:
        raise NotImplementedError(f"Futures not implemented for '{self.name}'")

    def get_futures_price(self, symbol: str) -> float:
        """Defaults to the spot price. Override if the exchange quotes
        futures/perp prices separately from spot (they usually differ
        slightly due to funding-driven basis)."""
        return self.get_price(symbol)

    def open_futures_long(self, symbol: str, usdt_amount: float) -> dict:
        raise NotImplementedError(f"Futures not implemented for '{self.name}'")

    def open_futures_short(self, symbol: str, usdt_amount: float) -> dict:
        raise NotImplementedError(f"Futures not implemented for '{self.name}'")

    def close_futures_position(self, symbol: str) -> dict:
        """Closes whatever open futures position exists on this symbol
        (long or short), fully, at market."""
        raise NotImplementedError(f"Futures not implemented for '{self.name}'")

    def get_futures_position(self, symbol: str) -> dict:
        """Returns {"side": "long"|"short"|"none", "size": float (base
        units), "entry_price": float, "unrealized_pnl": float}."""
        raise NotImplementedError(f"Futures not implemented for '{self.name}'")

    def get_funding_rate(self, symbol: str) -> float:
        """Returns the current/most recent funding rate as a fraction
        (e.g. 0.0001 = 0.01%). Positive = longs pay shorts."""
        raise NotImplementedError(f"Futures not implemented for '{self.name}'")

    # ── STAKING / FLEXIBLE EARN (opt-in — see config.STAKING_ENABLED) ──────
    #
    # Scoped deliberately narrow: FLEXIBLE products only (no fixed-term
    # lockups). A trading bot needs to be able to unstake on short notice
    # when a signal wants that capital back — a locked staking term would
    # silently turn "the bot decided to trade" into "the bot can't,
    # because the money is locked for 30/60/90 days." Every implementation
    # below must stay flexible-only for that reason.
    def staking_supported(self) -> bool:
        return False

    def get_staking_apr(self, coin: str) -> float:
        """Returns the current flexible-staking APR for `coin` as a
        fraction (e.g. 0.05 = 5% APR). Returns 0.0 if no flexible
        product exists for that coin on this exchange."""
        raise NotImplementedError(f"Staking not implemented for '{self.name}'")

    def stake_flexible(self, coin: str, amount: float) -> dict:
        raise NotImplementedError(f"Staking not implemented for '{self.name}'")

    def unstake_flexible(self, coin: str, amount: float) -> dict:
        raise NotImplementedError(f"Staking not implemented for '{self.name}'")

    def get_staked_balance(self, coin: str) -> float:
        raise NotImplementedError(f"Staking not implemented for '{self.name}'")


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

    # ── FUTURES (KuCoin Futures — separate API base, same KC-API-* signing
    #   scheme as spot) ────────────────────────────────────────────────────
    #
    # IMPORTANT: KuCoin Futures contracts are quoted in fixed-size LOTS,
    # not raw coin quantity (e.g. one XBTUSDTM contract ≈ $1 notional of
    # BTC, not 1 BTC) — the multiplier varies per contract and is returned
    # by GET /api/v1/contracts/{symbol}. This adapter fetches it live for
    # every order rather than hardcoding per-symbol multipliers, since
    # KuCoin does periodically change them. Verify a contract's multiplier
    # against KuCoin's live "Detail" endpoint before trading it for real —
    # an incorrect assumed multiplier would size the position wrong.
    FUTURES_BASE = "https://api-futures.kucoin.com"

    def futures_supported(self) -> bool:
        return True

    def _futures_symbol(self, symbol: str) -> str:
        # config uses "BTC-USDT" -> KuCoin USDT-margined perpetual "XBTUSDTM"
        coin = symbol.split("-")[0]
        coin = "XBT" if coin == "BTC" else coin
        return f"{coin}USDTM"

    def _futures_sign(self, method, path, body=""):
        ts  = str(int(time.time() * 1000))
        msg = (ts + method.upper() + path + body).encode()
        sig = base64.b64encode(
            hmac.new(self.credentials["api_secret"].encode(), msg, hashlib.sha256).digest()
        ).decode()
        passphrase = base64.b64encode(
            hmac.new(self.credentials["api_secret"].encode(),
                     self.credentials["passphrase"].encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "KC-API-KEY":        self.credentials["api_key"],
            "KC-API-SIGN":       sig,
            "KC-API-TIMESTAMP":  ts,
            "KC-API-PASSPHRASE": passphrase,
            "KC-API-KEY-VERSION": "2",
            "Content-Type":      "application/json",
        }

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def _contract_detail(self, fsym: str) -> dict:
        resp = requests.get(f"{self.FUTURES_BASE}/api/v1/contracts/{fsym}", timeout=10)
        data = resp.json()
        if data.get("code") != "200000":
            raise ValueError(f"KuCoin Futures: contract detail failed for {fsym}: {data}")
        return data["data"]

    def set_leverage(self, symbol, leverage=1):
        # KuCoin Futures sets leverage per-order (see open_futures_long/short
        # below) rather than via a standalone endpoint — nothing to do here,
        # kept as a no-op so callers can treat every adapter uniformly.
        pass

    def get_futures_price(self, symbol):
        fsym = self._futures_symbol(symbol)
        resp = requests.get(f"{self.FUTURES_BASE}/api/v1/ticker",
                            params={"symbol": fsym}, timeout=10)
        return float(resp.json()["data"]["price"])

    def _futures_market_order(self, symbol, usdt_amount, side):
        import json
        fsym    = self._futures_symbol(symbol)
        detail  = self._contract_detail(fsym)
        multiplier = float(detail["multiplier"])
        price   = self.get_futures_price(symbol)
        lots    = max(1, round(usdt_amount / (price * multiplier)))
        path    = "/api/v1/orders"
        body    = json.dumps({
            "clientOid": f"bot_{int(time.time()*1000)}",
            "side": side, "symbol": fsym, "type": "market",
            "size": lots, "leverage": "1",
        })
        resp = requests.post(f"{self.FUTURES_BASE}{path}", data=body, timeout=10,
                             headers=self._futures_sign("POST", path, body))
        return resp.json()

    def open_futures_long(self, symbol, usdt_amount):
        return self._futures_market_order(symbol, usdt_amount, "buy")

    def open_futures_short(self, symbol, usdt_amount):
        return self._futures_market_order(symbol, usdt_amount, "sell")

    def close_futures_position(self, symbol):
        import json
        pos = self.get_futures_position(symbol)
        if pos["side"] == "none" or pos["size"] == 0:
            return {"msg": "no open position"}
        fsym = self._futures_symbol(symbol)
        side = "sell" if pos["side"] == "long" else "buy"
        path = "/api/v1/orders"
        body = json.dumps({
            "clientOid": f"bot_{int(time.time()*1000)}",
            "side": side, "symbol": fsym, "type": "market",
            "size": abs(int(pos["size"])), "closeOrder": True,
        })
        resp = requests.post(f"{self.FUTURES_BASE}{path}", data=body, timeout=10,
                             headers=self._futures_sign("POST", path, body))
        return resp.json()

    def get_futures_position(self, symbol):
        fsym = self._futures_symbol(symbol)
        path = f"/api/v1/position?symbol={fsym}"
        resp = requests.get(f"{self.FUTURES_BASE}{path}", timeout=10,
                            headers=self._futures_sign("GET", path))
        data = resp.json().get("data") or {}
        size = float(data.get("currentQty", 0) or 0)
        if size == 0:
            return {"side": "none", "size": 0.0, "entry_price": 0.0, "unrealized_pnl": 0.0}
        return {
            "side": "long" if size > 0 else "short",
            "size": abs(size),
            "entry_price": float(data.get("avgEntryPrice", 0) or 0),
            "unrealized_pnl": float(data.get("unrealisedPnl", 0) or 0),
        }

    def get_funding_rate(self, symbol):
        fsym = self._futures_symbol(symbol)
        resp = requests.get(f"{self.FUTURES_BASE}/api/v1/funding-rate/{fsym}/current", timeout=10)
        data = resp.json().get("data") or {}
        return float(data.get("value", 0) or 0)

    # ── STAKING (KuCoin Earn v3 — flexible products only) ──────────────────
    def staking_supported(self) -> bool:
        return True

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_staking_apr(self, coin):
        path = f"/api/v3/earn/promotion/products?currency={coin}"
        resp = requests.get(f"https://api.kucoin.com{path}", timeout=10,
                            headers=self._futures_sign("GET", path))
        items = resp.json().get("data", {}).get("items", [])
        # Only flexible (non-fixed-term) products are eligible — a bot must
        # be able to redeem on short notice when a trade signal wants the
        # capital back.
        flexible = [i for i in items if str(i.get("redeemPeriod", 0)) in ("0", "")]
        if not flexible:
            return 0.0
        return max(float(i.get("apr", 0) or 0) for i in flexible) / 100.0

    def _flexible_product_id(self, coin):
        path = f"/api/v3/earn/promotion/products?currency={coin}"
        resp = requests.get(f"https://api.kucoin.com{path}", timeout=10,
                            headers=self._futures_sign("GET", path))
        items = resp.json().get("data", {}).get("items", [])
        flexible = [i for i in items if str(i.get("redeemPeriod", 0)) in ("0", "")]
        if not flexible:
            raise ValueError(f"KuCoin: no flexible earn product for {coin}")
        return max(flexible, key=lambda i: float(i.get("apr", 0) or 0))["productId"]

    def stake_flexible(self, coin, amount):
        import json
        product_id = self._flexible_product_id(coin)
        path = "/api/v3/earn/orders"
        body = json.dumps({"productId": product_id, "amount": str(amount)})
        resp = requests.post(f"https://api.kucoin.com{path}", data=body, timeout=10,
                             headers=self._futures_sign("POST", path, body))
        return resp.json()

    def unstake_flexible(self, coin, amount):
        import json
        product_id = self._flexible_product_id(coin)
        path = "/api/v3/earn/redeem"
        body = json.dumps({"productId": product_id, "amount": str(amount), "fromType": "MAIN"})
        resp = requests.post(f"https://api.kucoin.com{path}", data=body, timeout=10,
                             headers=self._futures_sign("POST", path, body))
        return resp.json()

    def get_staked_balance(self, coin):
        path = f"/api/v3/earn/hold-assets?currency={coin}"
        resp = requests.get(f"https://api.kucoin.com{path}", timeout=10,
                            headers=self._futures_sign("GET", path))
        items = resp.json().get("data", {}).get("items", [])
        return sum(float(i.get("holdAmount", 0) or 0) for i in items)


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

    # ── FUTURES (Binance USDT-M — fapi.binance.com, one-way position mode
    #   assumed; same HMAC-SHA256 signing as spot) ───────────────────────────
    FAPI_BASE = "https://fapi.binance.com"

    def futures_supported(self) -> bool:
        return True

    def set_leverage(self, symbol, leverage=1):
        sym    = self.normalize_symbol(symbol)
        params = self._sign({"symbol": sym, "leverage": leverage, "timestamp": int(time.time()*1000)})
        requests.post(f"{self.FAPI_BASE}/fapi/v1/leverage", params=params,
                      headers=self._headers(), timeout=10)

    def get_futures_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.FAPI_BASE}/fapi/v1/ticker/price", params={"symbol": sym}, timeout=10)
        return float(resp.json()["price"])

    def _futures_market_order(self, symbol, usdt_amount, side, reduce_only=False):
        sym   = self.normalize_symbol(symbol)
        price = self.get_futures_price(symbol)
        qty   = round(usdt_amount / price, 3)
        params = {"symbol": sym, "side": side, "type": "MARKET", "quantity": qty,
                  "timestamp": int(time.time()*1000)}
        if reduce_only:
            params["reduceOnly"] = "true"
        params = self._sign(params)
        resp = requests.post(f"{self.FAPI_BASE}/fapi/v1/order", params=params,
                             headers=self._headers(), timeout=10)
        return resp.json()

    def open_futures_long(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "BUY")

    def open_futures_short(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "SELL")

    def close_futures_position(self, symbol):
        pos = self.get_futures_position(symbol)
        if pos["side"] == "none":
            return {"msg": "no open position"}
        sym   = self.normalize_symbol(symbol)
        side  = "SELL" if pos["side"] == "long" else "BUY"
        params = self._sign({"symbol": sym, "side": side, "type": "MARKET",
                             "quantity": pos["size"], "reduceOnly": "true",
                             "timestamp": int(time.time()*1000)})
        resp = requests.post(f"{self.FAPI_BASE}/fapi/v1/order", params=params,
                             headers=self._headers(), timeout=10)
        return resp.json()

    def get_futures_position(self, symbol):
        sym    = self.normalize_symbol(symbol)
        params = self._sign({"symbol": sym, "timestamp": int(time.time()*1000)})
        resp   = requests.get(f"{self.FAPI_BASE}/fapi/v2/positionRisk", params=params,
                              headers=self._headers(), timeout=10)
        rows = resp.json()
        row  = rows[0] if isinstance(rows, list) and rows else {}
        amt  = float(row.get("positionAmt", 0) or 0)
        if amt == 0:
            return {"side": "none", "size": 0.0, "entry_price": 0.0, "unrealized_pnl": 0.0}
        return {
            "side": "long" if amt > 0 else "short",
            "size": abs(amt),
            "entry_price": float(row.get("entryPrice", 0) or 0),
            "unrealized_pnl": float(row.get("unRealizedProfit", 0) or 0),
        }

    def get_funding_rate(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.FAPI_BASE}/fapi/v1/premiumIndex", params={"symbol": sym}, timeout=10)
        return float(resp.json().get("lastFundingRate", 0) or 0)

    # ── STAKING (Binance Simple Earn — flexible products only) ─────────────
    def staking_supported(self) -> bool:
        return True

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_staking_apr(self, coin):
        params = self._sign({"asset": coin, "timestamp": int(time.time()*1000)})
        resp   = requests.get(f"{self.BASE}/sapi/v1/simple-earn/flexible/list", params=params,
                              headers=self._headers(), timeout=10)
        rows = resp.json().get("rows", [])
        if not rows:
            return 0.0
        return float(rows[0].get("latestAnnualPercentageRate", 0) or 0)

    def _flexible_product_id(self, coin):
        params = self._sign({"asset": coin, "timestamp": int(time.time()*1000)})
        resp   = requests.get(f"{self.BASE}/sapi/v1/simple-earn/flexible/list", params=params,
                              headers=self._headers(), timeout=10)
        rows = resp.json().get("rows", [])
        if not rows:
            raise ValueError(f"Binance: no flexible earn product for {coin}")
        return rows[0]["productId"]

    def stake_flexible(self, coin, amount):
        product_id = self._flexible_product_id(coin)
        params = self._sign({"productId": product_id, "amount": round(amount, 8),
                             "timestamp": int(time.time()*1000)})
        resp = requests.post(f"{self.BASE}/sapi/v1/simple-earn/flexible/subscribe", params=params,
                             headers=self._headers(), timeout=10)
        return resp.json()

    def unstake_flexible(self, coin, amount):
        product_id = self._flexible_product_id(coin)
        params = self._sign({"productId": product_id, "amount": round(amount, 8),
                             "redeemType": "FAST", "timestamp": int(time.time()*1000)})
        resp = requests.post(f"{self.BASE}/sapi/v1/simple-earn/flexible/redeem", params=params,
                             headers=self._headers(), timeout=10)
        return resp.json()

    def get_staked_balance(self, coin):
        params = self._sign({"asset": coin, "timestamp": int(time.time()*1000)})
        resp   = requests.get(f"{self.BASE}/sapi/v1/simple-earn/flexible/position", params=params,
                              headers=self._headers(), timeout=10)
        rows = resp.json().get("rows", [])
        return sum(float(r.get("totalAmount", 0) or 0) for r in rows)


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

    # ── FUTURES (Kraken Futures) ─────────────────────────────────────────
    #
    # IMPORTANT — read before enabling: Kraken Futures is a COMPLETELY
    # SEPARATE product from Kraken spot, on a different domain
    # (futures.kraken.com) with its own API key system generated at
    # https://futures.kraken.com/settings/api — your regular Kraken spot
    # API key/secret will NOT authenticate here. This adapter looks for
    # optional "futures_api_key" / "futures_api_secret" entries in this
    # exchange's config.py credentials dict; if they're missing, futures
    # calls raise clearly rather than silently trying (and failing) with
    # the wrong credentials. The signing scheme also differs from spot
    # Kraken's (HMAC-SHA512 over sha256(postData+nonce+path), no /0/
    # prefix) — implemented here from Kraken's published Futures API docs
    # but NOT verified against a live account in this session. Test with
    # a trivial order on Kraken Futures' free demo environment
    # (demo-futures.kraken.com) before trusting this with real funds.
    FUTURES_BASE = "https://futures.kraken.com"

    def futures_supported(self) -> bool:
        # Checks for actual non-empty values, not just key presence — config.py
        # always adds these two keys (via getattr(..., "")) even when
        # bot_secrets.py has nothing filled in, so a presence-only check would
        # incorrectly report "supported" with blank credentials and let
        # futures_manager.py attempt real, doomed-to-fail API calls in live
        # mode instead of cleanly staying off.
        return bool(self.credentials.get("futures_api_key")) and bool(self.credentials.get("futures_api_secret"))

    def _require_futures_creds(self):
        if not self.futures_supported():
            raise NotImplementedError(
                "Kraken Futures requires separate credentials — add "
                "'futures_api_key' and 'futures_api_secret' (from "
                "https://futures.kraken.com/settings/api) to this exchange's "
                "entry in config.EXCHANGES['kraken']."
            )

    def _futures_symbol(self, symbol: str) -> str:
        coin, _, quote = symbol.partition("-")
        coin = self.LEGACY_COIN_TO_KRAKEN.get(coin, coin).lstrip("X")
        return f"pf_{coin.lower()}{quote.lower()}"

    def _futures_sign(self, path, post_data, nonce):
        import base64 as _b64
        msg    = (post_data + nonce + path).encode()
        sha256 = hashlib.sha256(msg).digest()
        sig    = hmac.new(_b64.b64decode(self.credentials["futures_api_secret"]),
                          sha256, hashlib.sha512)
        return _b64.b64encode(sig.digest()).decode()

    def _futures_request(self, method, path, data=None):
        self._require_futures_creds()
        data = data or {}
        nonce = str(int(time.time() * 1000))
        post_data = urllib.parse.urlencode(data)
        headers = {
            "APIKey":   self.credentials["futures_api_key"],
            "Nonce":    nonce,
            "Authent":  self._futures_sign(path, post_data, nonce),
        }
        url = f"{self.FUTURES_BASE}{path}"
        if method == "GET":
            resp = requests.get(url, params=data, headers=headers, timeout=10)
        else:
            resp = requests.post(url, data=data, headers=headers, timeout=10)
        return resp.json()

    def set_leverage(self, symbol, leverage=1):
        fsym = self._futures_symbol(symbol)
        self._futures_request("PUT", f"/derivatives/api/v3/leveragepreferences",
                              {"symbol": fsym, "maxLeverage": leverage})

    def get_futures_price(self, symbol):
        fsym = self._futures_symbol(symbol)
        resp = requests.get(f"{self.FUTURES_BASE}/derivatives/api/v3/tickers", timeout=10)
        for t in resp.json().get("tickers", []):
            if t.get("symbol", "").lower() == fsym:
                return float(t.get("markPrice", t.get("last", 0)))
        raise ValueError(f"Kraken Futures: no ticker for {fsym}")

    def _futures_market_order(self, symbol, usdt_amount, side):
        fsym  = self._futures_symbol(symbol)
        price = self.get_futures_price(symbol)
        size  = round(usdt_amount / price, 6)
        return self._futures_request("POST", "/derivatives/api/v3/sendorder", {
            "orderType": "mkt", "symbol": fsym, "side": side, "size": size,
        })

    def open_futures_long(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "buy")

    def open_futures_short(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "sell")

    def close_futures_position(self, symbol):
        pos = self.get_futures_position(symbol)
        if pos["side"] == "none":
            return {"msg": "no open position"}
        fsym = self._futures_symbol(symbol)
        side = "sell" if pos["side"] == "long" else "buy"
        return self._futures_request("POST", "/derivatives/api/v3/sendorder", {
            "orderType": "mkt", "symbol": fsym, "side": side,
            "size": pos["size"], "reduceOnly": "true",
        })

    def get_futures_position(self, symbol):
        fsym = self._futures_symbol(symbol)
        data = self._futures_request("GET", "/derivatives/api/v3/openpositions")
        for p in data.get("openPositions", []):
            if p.get("symbol", "").lower() == fsym:
                size = float(p.get("size", 0) or 0)
                side = p.get("side", "long")
                return {
                    "side": side if size != 0 else "none",
                    "size": abs(size),
                    "entry_price": float(p.get("price", 0) or 0),
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0) or 0),
                }
        return {"side": "none", "size": 0.0, "entry_price": 0.0, "unrealized_pnl": 0.0}

    def get_funding_rate(self, symbol):
        fsym = self._futures_symbol(symbol)
        resp = requests.get(f"{self.FUTURES_BASE}/derivatives/api/v3/tickers", timeout=10)
        for t in resp.json().get("tickers", []):
            if t.get("symbol", "").lower() == fsym:
                return float(t.get("fundingRate", 0) or 0)
        return 0.0

    # ── STAKING (Kraken Earn — uses the SAME spot credentials/domain,
    #   flexible ("opt_out"/no-bonding) strategies only) ────────────────────
    def staking_supported(self) -> bool:
        return True

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def _earn_strategies(self, coin):
        path = "/0/private/Earn/Strategies"
        sig, nonce = self._sign(path, {"asset": coin})
        resp = requests.post(f"{self.BASE}{path}", data={"nonce": nonce, "asset": coin}, timeout=10,
                             headers={"API-Key": self.credentials["api_key"], "API-Sign": sig})
        data = resp.json()
        if data.get("error"):
            raise ValueError(f"Kraken Earn: {data['error']}")
        return data.get("result", {}).get("items", [])

    def get_staking_apr(self, coin):
        strategies = self._earn_strategies(coin)
        # Flexible = no lock/bonding period ("lock_type.type" == "flex")
        flexible = [s for s in strategies if s.get("lock_type", {}).get("type") == "flex"]
        if not flexible:
            return 0.0
        def _apr(s):
            rate = s.get("apr_estimate", {}).get("high", "0")
            return float(str(rate).rstrip("%") or 0)
        return max(_apr(s) for s in flexible) / 100.0

    def _flexible_strategy_id(self, coin):
        strategies = self._earn_strategies(coin)
        flexible = [s for s in strategies if s.get("lock_type", {}).get("type") == "flex"]
        if not flexible:
            raise ValueError(f"Kraken: no flexible earn strategy for {coin}")
        return max(flexible, key=lambda s: float(
            str(s.get("apr_estimate", {}).get("high", "0")).rstrip("%") or 0))["id"]

    def stake_flexible(self, coin, amount):
        strategy_id = self._flexible_strategy_id(coin)
        path = "/0/private/Earn/Allocate"
        data = {"strategy_id": strategy_id, "amount": str(amount)}
        sig, nonce = self._sign(path, data)
        resp = requests.post(f"{self.BASE}{path}", data={**data, "nonce": nonce}, timeout=10,
                             headers={"API-Key": self.credentials["api_key"], "API-Sign": sig})
        return resp.json()

    def unstake_flexible(self, coin, amount):
        strategy_id = self._flexible_strategy_id(coin)
        path = "/0/private/Earn/Deallocate"
        data = {"strategy_id": strategy_id, "amount": str(amount)}
        sig, nonce = self._sign(path, data)
        resp = requests.post(f"{self.BASE}{path}", data={**data, "nonce": nonce}, timeout=10,
                             headers={"API-Key": self.credentials["api_key"], "API-Sign": sig})
        return resp.json()

    def get_staked_balance(self, coin):
        path = "/0/private/Earn/Allocations"
        sig, nonce = self._sign(path, {})
        resp = requests.post(f"{self.BASE}{path}", data={"nonce": nonce}, timeout=10,
                             headers={"API-Key": self.credentials["api_key"], "API-Sign": sig})
        data = resp.json().get("result", {}).get("items", [])
        total = 0.0
        for item in data:
            if item.get("native_asset", "").upper() == coin.upper():
                total += float(item.get("amount_allocated", {}).get("total", {}).get("native", 0) or 0)
        return total


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

    # ── FUTURES (Bybit linear perpetuals — same v5 API/signing as spot,
    #   category="linear" instead of "spot") ────────────────────────────────
    def futures_supported(self) -> bool:
        return True

    def _v5_signed(self, method, path, params=None, body=None):
        params = params or {}
        ts, recv = str(int(time.time() * 1000)), "5000"
        if method == "GET":
            qs  = urllib.parse.urlencode(sorted(params.items()))
            sig = hmac.new(self.credentials["api_secret"].encode(),
                           (ts + self.credentials["api_key"] + recv + qs).encode(),
                           hashlib.sha256).hexdigest()
            resp = requests.get(f"{self.BASE}{path}", params=params, timeout=10,
                                headers={"X-BAPI-API-KEY": self.credentials["api_key"],
                                         "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts,
                                         "X-BAPI-RECV-WINDOW": recv})
        else:
            import json
            body_str = json.dumps(body or {})
            sig = hmac.new(self.credentials["api_secret"].encode(),
                           (ts + self.credentials["api_key"] + recv + body_str).encode(),
                           hashlib.sha256).hexdigest()
            resp = requests.post(f"{self.BASE}{path}", data=body_str, timeout=10,
                                 headers={"X-BAPI-API-KEY": self.credentials["api_key"],
                                          "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts,
                                          "X-BAPI-RECV-WINDOW": recv,
                                          "Content-Type": "application/json"})
        return resp.json()

    def set_leverage(self, symbol, leverage=1):
        sym = self.normalize_symbol(symbol)
        self._v5_signed("POST", "/v5/position/set-leverage", body={
            "category": "linear", "symbol": sym,
            "buyLeverage": str(leverage), "sellLeverage": str(leverage),
        })

    def get_futures_price(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/v5/market/tickers",
                            params={"category": "linear", "symbol": sym}, timeout=10)
        return float(resp.json()["result"]["list"][0]["lastPrice"])

    def _futures_market_order(self, symbol, usdt_amount, side, reduce_only=False):
        sym   = self.normalize_symbol(symbol)
        price = self.get_futures_price(symbol)
        qty   = round(usdt_amount / price, 3)
        body  = {"category": "linear", "symbol": sym, "side": side,
                "orderType": "Market", "qty": str(qty)}
        if reduce_only:
            body["reduceOnly"] = True
        return self._v5_signed("POST", "/v5/order/create", body=body)

    def open_futures_long(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "Buy")

    def open_futures_short(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "Sell")

    def close_futures_position(self, symbol):
        pos = self.get_futures_position(symbol)
        if pos["side"] == "none":
            return {"msg": "no open position"}
        sym  = self.normalize_symbol(symbol)
        side = "Sell" if pos["side"] == "long" else "Buy"
        return self._v5_signed("POST", "/v5/order/create", body={
            "category": "linear", "symbol": sym, "side": side,
            "orderType": "Market", "qty": str(pos["size"]), "reduceOnly": True,
        })

    def get_futures_position(self, symbol):
        sym  = self.normalize_symbol(symbol)
        data = self._v5_signed("GET", "/v5/position/list", params={"category": "linear", "symbol": sym})
        rows = data.get("result", {}).get("list", [])
        row  = rows[0] if rows else {}
        size = float(row.get("size", 0) or 0)
        if size == 0:
            return {"side": "none", "size": 0.0, "entry_price": 0.0, "unrealized_pnl": 0.0}
        return {
            "side": row.get("side", "Buy").lower() == "buy" and "long" or "short",
            "size": size,
            "entry_price": float(row.get("avgPrice", 0) or 0),
            "unrealized_pnl": float(row.get("unrealisedPnl", 0) or 0),
        }

    def get_funding_rate(self, symbol):
        sym  = self.normalize_symbol(symbol)
        resp = requests.get(f"{self.BASE}/v5/market/funding/history",
                            params={"category": "linear", "symbol": sym, "limit": 1}, timeout=10)
        rows = resp.json().get("result", {}).get("list", [])
        return float(rows[0]["fundingRate"]) if rows else 0.0

    # ── STAKING (Bybit Earn — flexible savings only) ────────────────────────
    def staking_supported(self) -> bool:
        return True

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_staking_apr(self, coin):
        data = self._v5_signed("GET", "/v5/earn/product",
                               params={"category": "FlexibleSaving", "coin": coin})
        rows = data.get("result", {}).get("list", [])
        if not rows:
            return 0.0
        return max(float(r.get("estimateApr", "0").rstrip("%") or 0) for r in rows) / 100.0

    def _flexible_product_id(self, coin):
        data = self._v5_signed("GET", "/v5/earn/product",
                               params={"category": "FlexibleSaving", "coin": coin})
        rows = data.get("result", {}).get("list", [])
        if not rows:
            raise ValueError(f"Bybit: no flexible earn product for {coin}")
        return max(rows, key=lambda r: float(r.get("estimateApr", "0").rstrip("%") or 0))["productId"]

    def stake_flexible(self, coin, amount):
        product_id = self._flexible_product_id(coin)
        return self._v5_signed("POST", "/v5/earn/place-order", body={
            "category": "FlexibleSaving", "productId": product_id,
            "orderType": "Subscribe", "amount": str(amount), "accountType": "UNIFIED",
        })

    def unstake_flexible(self, coin, amount):
        product_id = self._flexible_product_id(coin)
        return self._v5_signed("POST", "/v5/earn/place-order", body={
            "category": "FlexibleSaving", "productId": product_id,
            "orderType": "Redeem", "amount": str(amount), "accountType": "UNIFIED",
        })

    def get_staked_balance(self, coin):
        data = self._v5_signed("GET", "/v5/earn/position",
                               params={"category": "FlexibleSaving", "coin": coin})
        rows = data.get("result", {}).get("list", [])
        return sum(float(r.get("amount", 0) or 0) for r in rows)


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

    # ── FUTURES (OKX SWAP instruments — same JWT-less HMAC signing as spot,
    #   net position mode assumed — i.e. account NOT in hedge/long-short
    #   mode. If your OKX account has hedge mode enabled, orders here will
    #   be rejected for missing "posSide" — switch the account to net mode
    #   at OKX: Trade Settings, or add posSide handling before enabling.) ──
    def futures_supported(self) -> bool:
        return True

    def _futures_inst(self, symbol: str) -> str:
        return f"{symbol}-SWAP"   # BTC-USDT -> BTC-USDT-SWAP

    def set_leverage(self, symbol, leverage=1):
        import json
        inst = self._futures_inst(symbol)
        path = "/api/v5/account/set-leverage"
        body = json.dumps({"instId": inst, "lever": str(leverage), "mgnMode": "isolated"})
        requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                      headers=self._headers("POST", path, body))

    def get_futures_price(self, symbol):
        inst = self._futures_inst(symbol)
        resp = requests.get(f"{self.BASE}/api/v5/market/ticker", params={"instId": inst}, timeout=10)
        return float(resp.json()["data"][0]["last"])

    def _futures_market_order(self, symbol, usdt_amount, side, reduce_only=False):
        import json
        inst  = self._futures_inst(symbol)
        price = self.get_futures_price(symbol)
        sz    = str(round(usdt_amount / price, 6))
        path  = "/api/v5/trade/order"
        body  = json.dumps({
            "instId": inst, "tdMode": "isolated", "side": side,
            "ordType": "market", "sz": sz, "reduceOnly": reduce_only,
        })
        resp = requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                             headers=self._headers("POST", path, body))
        return resp.json()

    def open_futures_long(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "buy")

    def open_futures_short(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "sell")

    def close_futures_position(self, symbol):
        import json
        inst = self._futures_inst(symbol)
        path = "/api/v5/trade/close-position"
        body = json.dumps({"instId": inst, "mgnMode": "isolated"})
        resp = requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                             headers=self._headers("POST", path, body))
        return resp.json()

    def get_futures_position(self, symbol):
        inst = self._futures_inst(symbol)
        path = f"/api/v5/account/positions?instId={inst}"
        resp = requests.get(f"{self.BASE}{path}", timeout=10, headers=self._headers("GET", path))
        rows = resp.json().get("data", [])
        row  = rows[0] if rows else {}
        pos  = float(row.get("pos", 0) or 0)
        if pos == 0:
            return {"side": "none", "size": 0.0, "entry_price": 0.0, "unrealized_pnl": 0.0}
        return {
            "side": "long" if pos > 0 else "short",
            "size": abs(pos),
            "entry_price": float(row.get("avgPx", 0) or 0),
            "unrealized_pnl": float(row.get("upl", 0) or 0),
        }

    def get_funding_rate(self, symbol):
        inst = self._futures_inst(symbol)
        resp = requests.get(f"{self.BASE}/api/v5/public/funding-rate", params={"instId": inst}, timeout=10)
        data = resp.json().get("data", [])
        return float(data[0].get("fundingRate", 0) or 0) if data else 0.0

    # ── STAKING (OKX Savings — flexible/"demand" products only) ─────────────
    def staking_supported(self) -> bool:
        return True

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_staking_apr(self, coin):
        resp = requests.get(f"{self.BASE}/api/v5/finance/savings/lending-rate-summary",
                            params={"ccy": coin}, timeout=10)
        data = resp.json().get("data", [])
        return float(data[0].get("avgRate", 0) or 0) if data else 0.0

    def stake_flexible(self, coin, amount):
        import json
        path = "/api/v5/finance/savings/purchase-redempt"
        body = json.dumps({"ccy": coin, "amt": str(amount), "side": "purchase", "rate": "0.01"})
        resp = requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                             headers=self._headers("POST", path, body))
        return resp.json()

    def unstake_flexible(self, coin, amount):
        import json
        path = "/api/v5/finance/savings/purchase-redempt"
        body = json.dumps({"ccy": coin, "amt": str(amount), "side": "redempt"})
        resp = requests.post(f"{self.BASE}{path}", data=body, timeout=10,
                             headers=self._headers("POST", path, body))
        return resp.json()

    def get_staked_balance(self, coin):
        path = "/api/v5/finance/savings/balance"
        resp = requests.get(f"{self.BASE}{path}?ccy={coin}", timeout=10,
                            headers=self._headers("GET", f"{path}?ccy={coin}"))
        data = resp.json().get("data", [])
        return sum(float(d.get("amt", 0) or 0) for d in data)


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

    # ── FUTURES (Gate.io USDT-margined perpetuals — same HMAC-SHA512
    #   signing as spot, /futures/usdt base path) ────────────────────────────
    FUTURES_PATH_BASE = "/api/v4/futures/usdt"

    def futures_supported(self) -> bool:
        return True

    def _futures_contract(self, symbol: str) -> str:
        return symbol.replace("-", "_")   # BTC-USDT -> BTC_USDT

    def _futures_signed(self, method, path, query="", body=""):
        headers = self._sign(method, path, query, body)
        headers["Content-Type"] = "application/json"
        return headers

    def set_leverage(self, symbol, leverage=1):
        contract = self._futures_contract(symbol)
        path = f"{self.FUTURES_PATH_BASE}/positions/{contract}/leverage"
        query = f"leverage={leverage}"
        resp = requests.post(f"https://api.gateio.ws{path}?{query}", timeout=10,
                             headers=self._futures_signed("POST", path, query))
        return resp.json()

    def _contract_multiplier(self, contract: str) -> float:
        resp = requests.get(f"https://api.gateio.ws{self.FUTURES_PATH_BASE}/contracts/{contract}", timeout=10)
        return float(resp.json().get("quanto_multiplier", 1))

    def get_futures_price(self, symbol):
        contract = self._futures_contract(symbol)
        resp = requests.get(f"https://api.gateio.ws{self.FUTURES_PATH_BASE}/tickers",
                            params={"contract": contract}, timeout=10)
        rows = resp.json()
        return float(rows[0]["last"]) if rows else 0.0

    def _futures_market_order(self, symbol, usdt_amount, direction, reduce_only=False):
        import json
        contract   = self._futures_contract(symbol)
        multiplier = self._contract_multiplier(contract)
        price      = self.get_futures_price(symbol)
        contracts  = max(1, round(usdt_amount / (price * multiplier)))
        size       = contracts if direction == "buy" else -contracts
        path = f"{self.FUTURES_PATH_BASE}/orders"
        body = json.dumps({"contract": contract, "size": size, "price": "0",
                           "tif": "ioc", "reduce_only": reduce_only})
        resp = requests.post(f"https://api.gateio.ws{path}", data=body, timeout=10,
                             headers=self._futures_signed("POST", path, body=body))
        return resp.json()

    def open_futures_long(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "buy")

    def open_futures_short(self, symbol, usdt_amount):
        self.set_leverage(symbol, 1)
        return self._futures_market_order(symbol, usdt_amount, "sell")

    def close_futures_position(self, symbol):
        import json
        pos = self.get_futures_position(symbol)
        if pos["side"] == "none":
            return {"msg": "no open position"}
        contract = self._futures_contract(symbol)
        size = -int(pos["size"]) if pos["side"] == "long" else int(pos["size"])
        path = f"{self.FUTURES_PATH_BASE}/orders"
        body = json.dumps({"contract": contract, "size": size, "price": "0",
                           "tif": "ioc", "reduce_only": True})
        resp = requests.post(f"https://api.gateio.ws{path}", data=body, timeout=10,
                             headers=self._futures_signed("POST", path, body=body))
        return resp.json()

    def get_futures_position(self, symbol):
        contract = self._futures_contract(symbol)
        path = f"{self.FUTURES_PATH_BASE}/positions/{contract}"
        resp = requests.get(f"https://api.gateio.ws{path}", timeout=10,
                            headers=self._futures_signed("GET", path))
        data = resp.json()
        size = float(data.get("size", 0) or 0) if isinstance(data, dict) else 0
        if size == 0:
            return {"side": "none", "size": 0.0, "entry_price": 0.0, "unrealized_pnl": 0.0}
        return {
            "side": "long" if size > 0 else "short",
            "size": abs(size),
            "entry_price": float(data.get("entry_price", 0) or 0),
            "unrealized_pnl": float(data.get("unrealised_pnl", 0) or 0),
        }

    def get_funding_rate(self, symbol):
        contract = self._futures_contract(symbol)
        resp = requests.get(f"https://api.gateio.ws{self.FUTURES_PATH_BASE}/funding_rate",
                            params={"contract": contract, "limit": 1}, timeout=10)
        rows = resp.json()
        return float(rows[0]["r"]) if rows else 0.0

    # ── STAKING (Gate.io Earn Uni-Lend — flexible only) ─────────────────────
    def staking_supported(self) -> bool:
        return True

    @retry(max_attempts=3, base_delay=2.0, max_delay=20.0)
    def get_staking_apr(self, coin):
        resp = requests.get("https://api.gateio.ws/api/v4/earn/uni/currencies/" + coin, timeout=10)
        data = resp.json()
        return float(data.get("current_rate", 0) or 0) if isinstance(data, dict) else 0.0

    def stake_flexible(self, coin, amount):
        import json
        path = "/api/v4/earn/uni/lends"
        body = json.dumps({"currency": coin, "amount": str(amount)})
        resp = requests.post(f"https://api.gateio.ws{path}", data=body, timeout=10,
                             headers=self._futures_signed("POST", path, body=body))
        return resp.json()

    def unstake_flexible(self, coin, amount):
        import json
        path = "/api/v4/earn/uni/lends"
        body = json.dumps({"currency": coin, "amount": str(amount)})
        resp = requests.patch(f"https://api.gateio.ws{path}", data=body, timeout=10,
                              headers=self._futures_signed("PATCH", path, body=body))
        return resp.json()

    def get_staked_balance(self, coin):
        path = "/api/v4/earn/uni/lends"
        resp = requests.get(f"https://api.gateio.ws{path}", params={"currency": coin}, timeout=10,
                            headers=self._futures_signed("GET", path, f"currency={coin}"))
        rows = resp.json()
        return sum(float(r.get("amount", 0) or 0) for r in rows) if isinstance(rows, list) else 0.0


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

    # ── FUTURES (MEXC Contract API — contract.mexc.com) ─────────────────────
    #
    # CAUTION: MEXC's contract (futures) API lives on a separate domain
    # from spot with a different, less consistently documented signing
    # scheme than MEXC's spot API (which itself mirrors Binance's). This
    # is implemented from MEXC's published contract API reference but,
    # unlike the Binance/Bybit/OKX/KuCoin/Gate.io adapters above, was NOT
    # cross-checked against multiple independent working examples — MEXC's
    # contract docs have historically had more inconsistencies than the
    # majors. Test with a $1-2 order on a low-value pair before trusting
    # this with real size, exactly as you would test any new integration
    # against a live account for the first time.
    CONTRACT_BASE = "https://contract.mexc.com"

    def futures_supported(self) -> bool:
        return True

    def _contract_symbol(self, symbol: str) -> str:
        return symbol   # MEXC contract API already uses "BTC_USDT"-style... see below

    def _contract_sign(self, params_str: str):
        ts  = str(int(time.time() * 1000))
        sig = hmac.new(self.credentials["api_secret"].encode(),
                       (self.credentials["api_key"] + ts + params_str).encode(),
                       hashlib.sha256).hexdigest()
        return {"ApiKey": self.credentials["api_key"], "Request-Time": ts,
                "Signature": sig, "Content-Type": "application/json"}

    def _futures_symbol(self, symbol: str) -> str:
        return symbol.replace("-", "_")   # BTC-USDT -> BTC_USDT

    def set_leverage(self, symbol, leverage=1):
        # MEXC contract leverage is set per-order via the "leverage" field
        # on order submission (see _futures_market_order) — no standalone
        # endpoint call needed.
        pass

    def get_futures_price(self, symbol):
        fsym = self._futures_symbol(symbol)
        resp = requests.get(f"{self.CONTRACT_BASE}/api/v1/contract/ticker",
                            params={"symbol": fsym}, timeout=10)
        return float(resp.json()["data"]["lastPrice"])

    def _futures_market_order(self, symbol, usdt_amount, side, reduce_only=False):
        import json
        fsym  = self._futures_symbol(symbol)
        price = self.get_futures_price(symbol)
        vol   = max(1, round(usdt_amount / price))
        # side: 1=open long, 2=close short, 3=open short, 4=close long
        body  = json.dumps({
            "symbol": fsym, "price": price, "vol": vol, "leverage": 1,
            "side": side, "type": "5",  # type 5 = market order
            "openType": 2,               # 2 = isolated margin
        })
        headers = self._contract_sign(body)
        resp = requests.post(f"{self.CONTRACT_BASE}/api/v1/private/order/submit",
                             data=body, headers=headers, timeout=10)
        return resp.json()

    def open_futures_long(self, symbol, usdt_amount):
        return self._futures_market_order(symbol, usdt_amount, side=1)

    def open_futures_short(self, symbol, usdt_amount):
        return self._futures_market_order(symbol, usdt_amount, side=3)

    def close_futures_position(self, symbol):
        pos = self.get_futures_position(symbol)
        if pos["side"] == "none":
            return {"msg": "no open position"}
        side = 4 if pos["side"] == "long" else 2
        return self._futures_market_order(symbol, pos["size"] * pos["entry_price"], side=side, reduce_only=True)

    def get_futures_position(self, symbol):
        fsym = self._futures_symbol(symbol)
        headers = self._contract_sign(f"symbol={fsym}")
        resp = requests.get(f"{self.CONTRACT_BASE}/api/v1/private/position/open_positions",
                            params={"symbol": fsym}, headers=headers, timeout=10)
        rows = resp.json().get("data", [])
        if not rows:
            return {"side": "none", "size": 0.0, "entry_price": 0.0, "unrealized_pnl": 0.0}
        row = rows[0]
        # positionType: 1 = long, 2 = short
        return {
            "side": "long" if row.get("positionType") == 1 else "short",
            "size": float(row.get("holdVol", 0) or 0),
            "entry_price": float(row.get("holdAvgPrice", 0) or 0),
            "unrealized_pnl": float(row.get("unrealized", row.get("realized", 0)) or 0),
        }

    def get_funding_rate(self, symbol):
        fsym = self._futures_symbol(symbol)
        resp = requests.get(f"{self.CONTRACT_BASE}/api/v1/contract/funding_rate/{fsym}", timeout=10)
        return float(resp.json().get("data", {}).get("fundingRate", 0) or 0)

    # ── STAKING ───────────────────────────────────────────────────────────
    #
    # MEXC does not expose a documented, stable public REST API for its
    # flexible Earn/staking product the way Binance/Bybit/OKX/KuCoin/
    # Gate.io/Kraken do — MEXC's own API reference does not list Earn
    # endpoints as of this writing. Rather than guess at undocumented
    # endpoints for a feature that moves real funds, staking is left
    # unimplemented here; staking_manager.py skips this exchange.
    # (staking_supported() defaults to False via BaseExchange.)


# ══════════════════════════════════════════════════════════════════════════════
#  WEBULL (crypto only — wraps the official webull-openapi-python-sdk)
# ══════════════════════════════════════════════════════════════════════════════
#
#  NO FUTURES / NO STAKING: Webull's crypto OpenAPI is spot-only — it has
#  no perpetuals/futures product and no flexible-earn/staking product for
#  crypto. futures_supported()/staking_supported() are left at the
#  BaseExchange default (False) rather than faked. If Webull adds either
#  product to its public API in the future, implement here then.
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
#  NO FUTURES / NO STAKING: VirgoCX is a small CAD spot-only exchange with
#  no derivatives or earn/staking product at all. futures_supported()/
#  staking_supported() are left at the BaseExchange default (False).
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
#  NO FUTURES / NO STAKING (via this API): Coinbase does offer perpetual
#  futures (via "INTX"/Coinbase Financial Markets), but it requires a
#  separate, eligibility-gated account type most retail Coinbase users
#  don't have enabled, on top of different order/margin parameters than
#  the spot orders this adapter places — confirmed with the account owner
#  that INTX isn't enabled here, so it's intentionally not implemented
#  rather than shipped unverified. If you DO have INTX access, this is
#  the place to add it (same pattern as Kraken Futures above — separate
#  credentials/endpoint, gated behind its own capability check).
#  Coinbase also does not offer flexible crypto staking through this API
#  (Coinbase's on-chain staking is a different product with lockup and
#  validator-exit mechanics unlike the other exchanges' flexible-earn
#  products, and isn't a fit for the "unstake on short notice" model
#  staking_manager.py relies on) — this one is a hard no regardless of
#  account type. futures_supported()/staking_supported() are left at the
#  BaseExchange default (False) rather than guessed at.
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

    def _is_legacy_key(self):
        """UUID-format keys (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx) are legacy HMAC keys."""
        import re
        key = self.credentials.get("api_key", "")
        return bool(re.match(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
            key, re.I
        ))

    def _hmac_headers(self, method, path, body=""):
        """Legacy API key auth: CB-ACCESS-KEY + HMAC-SHA256 signature."""
        import hmac as _hmac, hashlib
        ts  = str(int(time.time()))
        msg = (ts + method.upper() + path + body).encode()
        sig = _hmac.new(
            base64.b64decode(self.credentials["api_secret"]),
            msg, hashlib.sha256
        ).hexdigest()
        return {
            "CB-ACCESS-KEY":       self.credentials["api_key"],
            "CB-ACCESS-SIGN":      sig,
            "CB-ACCESS-TIMESTAMP": ts,
            "Content-Type":        "application/json",
        }

    def _auth_headers(self, method, path, body=""):
        if self._is_legacy_key():
            return self._hmac_headers(method, path, body)
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
        if not resp.ok or not resp.text.strip():
            raise ValueError(f"Coinbase candles HTTP {resp.status_code}: {resp.text[:200]}")
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
            headers=self._auth_headers("POST", path, body), timeout=10,
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
            headers=self._auth_headers("POST", path, body), timeout=10,
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

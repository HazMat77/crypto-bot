"""
WebSocket Price Feeds
======================
Live streaming price feeds for 6 exchanges, replacing per-call REST polling.
MEXC, Webull, and VirgoCX have no usable public WebSocket and stay on REST.

Design:
  - Each feed subclass runs in a daemon thread, reconnecting automatically.
  - Prices land in a thread-safe dict keyed by normalized symbol ("BTC-USDT").
  - Exchange adapters call ws_feed.get_price(symbol); if the cached value is
    older than STALE_AFTER seconds the feed falls back to the exchange's own
    REST get_price() and refreshes the cache entry.
  - Symbols are passed in on construction so each feed subscribes to exactly
    the coins the bot is actually trading — no wasted subscriptions.

Usage (in exchanges.py adapters):
    self._ws = KuCoinWsFeed(symbols, rest_fallback=super().get_price)
    self._ws.start()

    def get_price(self, symbol):
        return self._ws.get_price(symbol)
"""

import json
import logging
import threading
import time
import requests
import websocket

log = logging.getLogger(__name__)

STALE_AFTER = 10   # seconds before treating a WS price as too old


# ══════════════════════════════════════════════════════════════════════════════
#  BASE CLASS
# ══════════════════════════════════════════════════════════════════════════════

class _WsFeed:
    """Thread-safe WebSocket price feed with auto-reconnect and REST fallback."""

    RECONNECT_DELAY = 5   # seconds between reconnect attempts

    def __init__(self, name: str, symbols: list, rest_fn):
        """
        name      — exchange label for log messages
        symbols   — list of "BTC-USDT" style symbols to subscribe to
        rest_fn   — callable(symbol) -> float, used when WS cache is stale
        """
        self._name    = name
        self._symbols = list(symbols)
        self._rest_fn = rest_fn
        self._cache   = {}        # {symbol: (price, timestamp)}
        self._lock    = threading.Lock()
        self._stop    = threading.Event()
        self._ws      = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        t = threading.Thread(target=self._loop, name=f"ws-{self._name}", daemon=True)
        t.start()
        log.info(f"[WS/{self._name}] Feed started for {len(self._symbols)} symbol(s)")

    def stop(self):
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def get_price(self, symbol: str) -> float:
        with self._lock:
            entry = self._cache.get(symbol)
        if entry:
            price, ts = entry
            if time.time() - ts < STALE_AFTER:
                return price
            log.debug(f"[WS/{self._name}] {symbol} cache stale ({time.time()-ts:.1f}s) — REST fallback")
        # WS stale or never received — use REST and update cache
        price = self._rest_fn(symbol)
        with self._lock:
            self._cache[symbol] = (price, time.time())
        return price

    def _update(self, symbol: str, price: float):
        with self._lock:
            self._cache[symbol] = (price, time.time())

    # ── Reconnect loop ────────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._connect()
            except Exception as e:
                log.warning(f"[WS/{self._name}] Connection error: {e}")
            if not self._stop.is_set():
                log.info(f"[WS/{self._name}] Reconnecting in {self.RECONNECT_DELAY}s")
                self._stop.wait(self.RECONNECT_DELAY)

    def _connect(self):
        """Override: build and run a websocket.WebSocketApp until it closes."""
        raise NotImplementedError

    def _on_error(self, ws, error):
        log.warning(f"[WS/{self._name}] {error}")

    def _on_close(self, ws, *args):
        log.info(f"[WS/{self._name}] Disconnected")


# ══════════════════════════════════════════════════════════════════════════════
#  KUCOIN
# ══════════════════════════════════════════════════════════════════════════════

class KuCoinWsFeed(_WsFeed):
    """
    KuCoin requires fetching a signed token via REST before connecting.
    Multi-symbol topic: /market/ticker:BTC-USDT,ETH-USDT,...
    Price field: msg["data"]["price"]
    """

    def __init__(self, symbols: list, rest_fn):
        super().__init__("kucoin", symbols, rest_fn)

    def _get_token_url(self):
        resp = requests.post("https://api.kucoin.com/api/v1/bullet-public", timeout=10)
        data = resp.json()["data"]
        token    = data["token"]
        endpoint = data["instanceServers"][0]["endpoint"]
        ping_s   = data["instanceServers"][0]["pingInterval"] // 1000
        return f"{endpoint}?token={token}", ping_s

    def _connect(self):
        url, ping_s = self._get_token_url()
        topic = ",".join(self._symbols)

        def on_open(ws):
            sub = {"id": "1", "type": "subscribe",
                   "topic": f"/market/ticker:{topic}", "response": True}
            ws.send(json.dumps(sub))
            log.info(f"[WS/kucoin] Subscribed to {len(self._symbols)} symbol(s)")

        def on_message(ws, message):
            msg = json.loads(message)
            if msg.get("type") == "message" and "data" in msg:
                symbol = msg.get("subject")     # e.g. "BTC-USDT"
                price  = msg["data"].get("price")
                if symbol and price:
                    self._update(symbol, float(price))

        self._ws = websocket.WebSocketApp(
            url, on_open=on_open, on_message=on_message,
            on_error=self._on_error, on_close=self._on_close,
        )
        self._ws.run_forever(ping_interval=ping_s)


# ══════════════════════════════════════════════════════════════════════════════
#  BINANCE
# ══════════════════════════════════════════════════════════════════════════════

class BinanceWsFeed(_WsFeed):
    """
    Binance combined stream endpoint supports multiple symbols in one URL.
    Stream format: {symbol_lower}@ticker  →  payload["c"] = last price.
    """

    def __init__(self, symbols: list, rest_fn):
        super().__init__("binance", symbols, rest_fn)

    def _connect(self):
        # Build combined stream URL: btcusdt@ticker/ethusdt@ticker/...
        streams = "/".join(
            f"{s.replace('-','').lower()}@ticker" for s in self._symbols
        )
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        def on_open(ws):
            log.info(f"[WS/binance] Connected — {len(self._symbols)} stream(s)")

        def on_message(ws, message):
            msg  = json.loads(message)
            data = msg.get("data", {})
            sym  = data.get("s", "")         # e.g. "BTCUSDT"
            price = data.get("c")            # last price from @ticker
            if price:
                # Convert back to canonical form: BTCUSDT → BTC-USDT
                symbol = self._binance_to_symbol(sym)
                if symbol:
                    self._update(symbol, float(price))

        self._ws = websocket.WebSocketApp(
            url, on_open=on_open, on_message=on_message,
            on_error=self._on_error, on_close=self._on_close,
        )
        self._ws.run_forever()

    def _binance_to_symbol(self, binance_sym: str) -> str:
        """BTCUSDT → BTC-USDT by matching against known symbols."""
        for s in self._symbols:
            if s.replace("-", "").upper() == binance_sym.upper():
                return s
        return ""


# ══════════════════════════════════════════════════════════════════════════════
#  KRAKEN
# ══════════════════════════════════════════════════════════════════════════════

class KrakenWsFeed(_WsFeed):
    """
    Kraken WS v1. Ticker messages are lists: [channelID, data, "ticker", "XBT/USDT"].
    Price field: data["c"][0]  (last trade closed price).
    Symbol mapping: BTC-USDT → XBT/USDT (and DOGE-USDT → XDG/USDT).
    Kraken sends a snapshot on subscribe, then updates per-trade.
    """

    # Only BTC and DOGE use legacy prefix on USDT pairs (confirmed in production logs).
    _USDT_PREFIX = {"BTC": "XBT", "DOGE": "XDG"}

    def __init__(self, symbols: list, rest_fn):
        super().__init__("kraken", symbols, rest_fn)
        # Build reverse map: "XBT/USDT" → "BTC-USDT"
        self._kraken_to_canon = {}
        for s in symbols:
            k = self._to_kraken(s)
            self._kraken_to_canon[k] = s

    def _to_kraken(self, symbol: str) -> str:
        coin, _, quote = symbol.partition("-")
        if quote.upper() == "USDT":
            coin = self._USDT_PREFIX.get(coin, coin)
        return f"{coin}/{quote}"

    def _connect(self):
        kraken_pairs = list(self._kraken_to_canon.keys())

        def on_open(ws):
            sub = {"event": "subscribe", "pair": kraken_pairs,
                   "subscription": {"name": "ticker"}}
            ws.send(json.dumps(sub))
            log.info(f"[WS/kraken] Subscribed to {len(kraken_pairs)} pair(s)")

        def on_message(ws, message):
            msg = json.loads(message)
            if isinstance(msg, list) and len(msg) >= 4 and msg[2] == "ticker":
                pair  = msg[3]                          # e.g. "XBT/USDT"
                price = msg[1]["c"][0]                  # last trade price
                canon = self._kraken_to_canon.get(pair)
                if canon and price:
                    self._update(canon, float(price))

        self._ws = websocket.WebSocketApp(
            "wss://ws.kraken.com",
            on_open=on_open, on_message=on_message,
            on_error=self._on_error, on_close=self._on_close,
        )
        self._ws.run_forever()


# ══════════════════════════════════════════════════════════════════════════════
#  BYBIT
# ══════════════════════════════════════════════════════════════════════════════

class BybitWsFeed(_WsFeed):
    """
    Bybit V5 spot public stream.
    Subscribe to tickers.{symbol} e.g. tickers.BTCUSDT.
    Price field: msg["data"]["lastPrice"].
    """

    def __init__(self, symbols: list, rest_fn):
        super().__init__("bybit", symbols, rest_fn)

    def _connect(self):
        args = [f"tickers.{s.replace('-','')}" for s in self._symbols]

        def on_open(ws):
            ws.send(json.dumps({"op": "subscribe", "args": args}))
            log.info(f"[WS/bybit] Subscribed to {len(args)} stream(s)")

        def on_message(ws, message):
            msg = json.loads(message)
            if msg.get("topic", "").startswith("tickers"):
                d = msg.get("data", {})
                sym_raw = d.get("symbol", "")           # e.g. "BTCUSDT"
                price   = d.get("lastPrice")
                if price:
                    canon = self._bybit_to_symbol(sym_raw)
                    if canon:
                        self._update(canon, float(price))

        self._ws = websocket.WebSocketApp(
            "wss://stream.bybit.com/v5/public/spot",
            on_open=on_open, on_message=on_message,
            on_error=self._on_error, on_close=self._on_close,
        )
        self._ws.run_forever(ping_interval=20)

    def _bybit_to_symbol(self, bybit_sym: str) -> str:
        for s in self._symbols:
            if s.replace("-", "").upper() == bybit_sym.upper():
                return s
        return ""


# ══════════════════════════════════════════════════════════════════════════════
#  OKX
# ══════════════════════════════════════════════════════════════════════════════

class OKXWsFeed(_WsFeed):
    """
    OKX V5 public WebSocket.
    Subscribe to tickers channel for each instId (BTC-USDT stays as-is).
    Price field: msg["data"][0]["last"].
    OKX requires a pong response to their ping, handled via on_ping.
    """

    def __init__(self, symbols: list, rest_fn):
        super().__init__("okx", symbols, rest_fn)

    def _connect(self):
        args = [{"channel": "tickers", "instId": s} for s in self._symbols]

        def on_open(ws):
            ws.send(json.dumps({"op": "subscribe", "args": args}))
            log.info(f"[WS/okx] Subscribed to {len(args)} channel(s)")

        def on_message(ws, message):
            # OKX sends plain "ping" strings as heartbeats
            if message == "ping":
                ws.send("pong")
                return
            msg = json.loads(message)
            if msg.get("arg", {}).get("channel") == "tickers" and msg.get("data"):
                d     = msg["data"][0]
                inst  = d.get("instId")                 # "BTC-USDT"
                price = d.get("last")
                if inst and price:
                    self._update(inst, float(price))

        self._ws = websocket.WebSocketApp(
            "wss://ws.okx.com:8443/ws/v5/public",
            on_open=on_open, on_message=on_message,
            on_error=self._on_error, on_close=self._on_close,
        )
        self._ws.run_forever()


# ══════════════════════════════════════════════════════════════════════════════
#  GATE.IO
# ══════════════════════════════════════════════════════════════════════════════

class GateIOWsFeed(_WsFeed):
    """
    Gate.io V4 spot WebSocket.
    Symbols use underscore: BTC-USDT → BTC_USDT.
    Price field: msg["result"]["last"].
    Gate.io sends periodic server-side pings that must be ponged.
    """

    def __init__(self, symbols: list, rest_fn):
        super().__init__("gateio", symbols, rest_fn)
        self._gate_to_canon = {s.replace("-", "_"): s for s in symbols}

    def _connect(self):
        gate_pairs = list(self._gate_to_canon.keys())

        def on_open(ws):
            sub = {"time": int(time.time()), "channel": "spot.tickers",
                   "event": "subscribe", "payload": gate_pairs}
            ws.send(json.dumps(sub))
            log.info(f"[WS/gateio] Subscribed to {len(gate_pairs)} pair(s)")

        def on_message(ws, message):
            msg = json.loads(message)
            if msg.get("channel") == "spot.ping":
                ws.send(json.dumps({"time": int(time.time()),
                                    "channel": "spot.pong"}))
                return
            if msg.get("channel") == "spot.tickers" and msg.get("event") == "update":
                r     = msg.get("result", {})
                pair  = r.get("currency_pair")          # "BTC_USDT"
                price = r.get("last")
                if pair and price:
                    canon = self._gate_to_canon.get(pair)
                    if canon:
                        self._update(canon, float(price))

        self._ws = websocket.WebSocketApp(
            "wss://api.gateio.ws/ws/v4/",
            on_open=on_open, on_message=on_message,
            on_error=self._on_error, on_close=self._on_close,
        )
        self._ws.run_forever()


# ══════════════════════════════════════════════════════════════════════════════
#  FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def build_ws_feed(exchange_name: str, symbols: list, rest_fn):
    """
    Returns a started WebSocket feed for the given exchange, or None if that
    exchange has no WebSocket support (MEXC, Webull, VirgoCX stay on REST).
    """
    mapping = {
        "kucoin":  KuCoinWsFeed,
        "binance": BinanceWsFeed,
        "kraken":  KrakenWsFeed,
        "bybit":   BybitWsFeed,
        "okx":     OKXWsFeed,
        "gateio":  GateIOWsFeed,
    }
    cls = mapping.get(exchange_name)
    if cls is None:
        log.info(f"[WS] No WebSocket feed for {exchange_name} — using REST polling")
        return None
    feed = cls(symbols, rest_fn)
    feed.start()
    return feed

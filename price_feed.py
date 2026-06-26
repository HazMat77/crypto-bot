"""
Price Feed
===========
Fetches live crypto prices from external sources to reduce
KuCoin API call volume and avoid rate limit errors (429).

Sources:
  coingecko      — free, no key, batch fetches up to 250 coins at once
  binance_public — free public endpoint, no key needed
  kucoin         — falls back to exchange directly if others fail

The price cache is refreshed every 30 seconds. All coin threads
read from the cache instead of hitting KuCoin individually.
This reduces KuCoin API calls by ~90% when trading many coins.
"""

import time
import logging
import threading
import requests
from datetime import datetime

log = logging.getLogger(__name__)

# ── Cache ──────────────────────────────────────────────────────────────────
_price_cache      = {}    # { "BTC": 67432.10, "ETH": 3521.44, ... }
_cache_lock       = threading.Lock()
_cache_updated_at = 0
_CACHE_TTL        = 30    # seconds before refresh

# ── CoinGecko symbol → id mapping for common coins ────────────────────────
# CoinGecko uses full names as IDs, not ticker symbols
_COINGECKO_IDS = {
    "BTC":    "bitcoin",
    "ETH":    "ethereum",
    "SOL":    "solana",
    "XRP":    "ripple",
    "DOGE":   "dogecoin",
    "ADA":    "cardano",
    "POL":    "matic-network",
    "DOT":    "polkadot",
    "AVAX":   "avalanche-2",
    "LINK":   "chainlink",
    "LTC":    "litecoin",
    "BCH":    "bitcoin-cash",
    "UNI":    "uniswap",
    "ATOM":   "cosmos",
    "XLM":    "stellar",
    "RVN":    "ravencoin",
    "PUFFER": "puffer-finance",
    "SIREN":  "siren",
    "XPL":    "plex",
}


def _fetch_coingecko(symbols: list) -> dict:
    """Batch fetch prices from CoinGecko. Returns {symbol: price}."""
    ids_needed = []
    id_to_sym  = {}

    for sym in symbols:
        coin = sym.split("-")[0].upper()
        cg_id = _COINGECKO_IDS.get(coin)
        if cg_id:
            ids_needed.append(cg_id)
            id_to_sym[cg_id] = coin

    if not ids_needed:
        return {}

    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids":           ",".join(ids_needed),
                "vs_currencies": "usd",
            },
            timeout=15,
        )
        if not resp.ok:
            log.warning(f"[PRICE FEED] CoinGecko returned {resp.status_code}")
            return {}

        data   = resp.json()
        result = {}
        for cg_id, prices in data.items():
            coin = id_to_sym.get(cg_id)
            if coin and "usd" in prices:
                result[coin] = float(prices["usd"])

        log.info(f"[PRICE FEED] CoinGecko: fetched {len(result)} prices")
        return result

    except Exception as e:
        log.warning(f"[PRICE FEED] CoinGecko failed: {e}")
        return {}


def _fetch_binance_public(symbols: list) -> dict:
    """Fetch prices from Binance public API (no key needed)."""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            timeout=15,
        )
        if not resp.ok:
            return {}

        all_prices = {t["symbol"]: float(t["price"]) for t in resp.json()}
        result = {}
        for sym in symbols:
            coin     = sym.split("-")[0].upper()
            bin_sym  = f"{coin}USDT"
            if bin_sym in all_prices:
                result[coin] = all_prices[bin_sym]

        log.info(f"[PRICE FEED] Binance public: fetched {len(result)} prices")
        return result

    except Exception as e:
        log.warning(f"[PRICE FEED] Binance public failed: {e}")
        return {}


def refresh_cache(symbols: list, source: str = "coingecko"):
    """Fetch latest prices and update the cache."""
    global _cache_updated_at

    if source == "coingecko":
        new_prices = _fetch_coingecko(symbols)
        # Fall back to Binance if CoinGecko returns too few
        if len(new_prices) < len(symbols) * 0.5:
            log.info("[PRICE FEED] CoinGecko incomplete — supplementing with Binance")
            binance_prices = _fetch_binance_public(symbols)
            for coin, price in binance_prices.items():
                if coin not in new_prices:
                    new_prices[coin] = price

    elif source == "binance_public":
        new_prices = _fetch_binance_public(symbols)

    else:
        return   # "kucoin" mode — each thread fetches directly

    if new_prices:
        with _cache_lock:
            _price_cache.update(new_prices)
            _cache_updated_at = time.time()


def get_price_cached(symbol: str, fallback_fn=None) -> float:
    """
    Get price from cache. If cache is stale or coin missing,
    call fallback_fn (exchange.get_price) and cache the result.
    """
    coin = symbol.split("-")[0].upper()

    with _cache_lock:
        age   = time.time() - _cache_updated_at
        price = _price_cache.get(coin)

    if price and age < _CACHE_TTL * 3:   # accept up to 3x TTL before forcing refresh
        return price

    # Cache miss or stale — use exchange directly
    if fallback_fn:
        try:
            price = fallback_fn(symbol)
            with _cache_lock:
                _price_cache[coin] = price
            return price
        except Exception as e:
            if price:
                log.warning(f"[PRICE FEED] {coin} fallback failed ({e}) — "
                           f"using stale cached price ${price:.6f} (age {age:.0f}s)")
                return price
            log.error(f"[PRICE FEED] {coin} fallback failed ({e}) and no "
                     f"cached price available at all — propagating error")
            raise

    raise ValueError(f"No price available for {symbol}")


def price_updater_worker(symbols: list, source: str, stop_event: threading.Event):
    """Background thread that refreshes price cache every 30 seconds."""
    log.info(f"[PRICE FEED] Updater started — source={source}, {len(symbols)} coins, refresh every {_CACHE_TTL}s")

    # Initial fetch
    refresh_cache(symbols, source)

    while not stop_event.wait(timeout=_CACHE_TTL):
        refresh_cache(symbols, source)

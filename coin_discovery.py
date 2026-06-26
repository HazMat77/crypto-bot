"""
Coin Discovery
===============
Auto-discovers all tradeable USDT pairs on each exchange,
filters out stablecoins, leveraged tokens, and low-volume coins,
then ranks by 24hr volume and returns the top N based on the
current scaling tier.
"""

import logging
import requests

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def should_exclude(symbol: str, exclude_keywords: list) -> bool:
    """Return True if this symbol contains any excluded keyword."""
    coin = symbol.split("-")[0].upper()
    for kw in exclude_keywords:
        if kw.upper() in coin:
            return True
    return False


def get_tier(pool_usdt: float, tiers: list) -> dict:
    """Return the highest scaling tier the pool qualifies for."""
    active = tiers[0]
    for tier in tiers:
        if pool_usdt >= tier["min_pool"]:
            active = tier
    return active


# ══════════════════════════════════════════════════════════════════════════════
#  PER-EXCHANGE DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

def discover_kucoin(min_volume: float, exclude_keywords: list) -> list:
    """Returns list of (symbol, volume_usdt) sorted by volume desc."""
    try:
        resp = requests.get("https://api.kucoin.com/api/v1/market/allTickers", timeout=15)
        data = resp.json().get("data", {}).get("ticker", [])
        pairs = []
        for t in data:
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            if should_exclude(sym, exclude_keywords):
                continue
            try:
                vol = float(t.get("volValue", 0))   # 24hr volume in USDT
            except Exception:
                continue
            if vol < min_volume:
                continue
            pairs.append((sym, vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        log.info(f"[KUCOIN DISCOVER] Found {len(pairs)} qualifying pairs")
        return pairs
    except Exception as e:
        log.error(f"[KUCOIN DISCOVER] Failed: {e}")
        return []


def discover_binance(min_volume: float, exclude_keywords: list) -> list:
    try:
        resp = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=15)
        data = resp.json()
        pairs = []
        for t in data:
            sym_raw = t.get("symbol", "")
            if not sym_raw.endswith("USDT"):
                continue
            coin = sym_raw[:-4]
            sym  = f"{coin}-USDT"
            if should_exclude(sym, exclude_keywords):
                continue
            try:
                vol = float(t.get("quoteVolume", 0))
            except Exception:
                continue
            if vol < min_volume:
                continue
            pairs.append((sym, vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        log.info(f"[BINANCE DISCOVER] Found {len(pairs)} qualifying pairs")
        return pairs
    except Exception as e:
        log.error(f"[BINANCE DISCOVER] Failed: {e}")
        return []


def discover_bybit(min_volume: float, exclude_keywords: list) -> list:
    try:
        resp = requests.get("https://api.bybit.com/v5/market/tickers",
                            params={"category": "spot"}, timeout=15)
        data = resp.json().get("result", {}).get("list", [])
        pairs = []
        for t in data:
            sym_raw = t.get("symbol", "")
            if not sym_raw.endswith("USDT"):
                continue
            coin = sym_raw[:-4]
            sym  = f"{coin}-USDT"
            if should_exclude(sym, exclude_keywords):
                continue
            try:
                vol = float(t.get("turnover24h", 0))
            except Exception:
                continue
            if vol < min_volume:
                continue
            pairs.append((sym, vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        log.info(f"[BYBIT DISCOVER] Found {len(pairs)} qualifying pairs")
        return pairs
    except Exception as e:
        log.error(f"[BYBIT DISCOVER] Failed: {e}")
        return []


def discover_kraken(min_volume: float, exclude_keywords: list) -> list:
    try:
        resp = requests.get("https://api.kraken.com/0/public/Ticker", timeout=15)
        data = resp.json().get("result", {})
        pairs = []
        for pair_code, t in data.items():
            if not pair_code.endswith("USDT"):
                continue
            coin = pair_code[:-4].replace("X", "", 1).replace("XBT", "BTC")
            sym  = f"{coin}-USDT"
            if should_exclude(sym, exclude_keywords):
                continue
            try:
                vol = float(t["v"][1])   # 24hr volume in base currency
                price = float(t["c"][0])
                vol_usdt = vol * price
            except Exception:
                continue
            if vol_usdt < min_volume:
                continue
            pairs.append((sym, vol_usdt))
        pairs.sort(key=lambda x: x[1], reverse=True)
        log.info(f"[KRAKEN DISCOVER] Found {len(pairs)} qualifying pairs")
        return pairs
    except Exception as e:
        log.error(f"[KRAKEN DISCOVER] Failed: {e}")
        return []


def discover_okx(min_volume: float, exclude_keywords: list) -> list:
    try:
        resp = requests.get("https://www.okx.com/api/v5/market/tickers",
                            params={"instType": "SPOT"}, timeout=15)
        data = resp.json().get("data", [])
        pairs = []
        for t in data:
            inst = t.get("instId", "")
            if not inst.endswith("-USDT"):
                continue
            if should_exclude(inst, exclude_keywords):
                continue
            try:
                vol = float(t.get("volCcy24h", 0))
            except Exception:
                continue
            if vol < min_volume:
                continue
            pairs.append((inst, vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        log.info(f"[OKX DISCOVER] Found {len(pairs)} qualifying pairs")
        return pairs
    except Exception as e:
        log.error(f"[OKX DISCOVER] Failed: {e}")
        return []


def discover_gateio(min_volume: float, exclude_keywords: list) -> list:
    try:
        resp = requests.get("https://api.gateio.ws/api/v4/spot/tickers", timeout=15)
        data = resp.json()
        pairs = []
        for t in data:
            pair = t.get("currency_pair", "")
            if not pair.endswith("_USDT"):
                continue
            sym = pair.replace("_", "-")
            if should_exclude(sym, exclude_keywords):
                continue
            try:
                vol = float(t.get("quote_volume", 0))
            except Exception:
                continue
            if vol < min_volume:
                continue
            pairs.append((sym, vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        log.info(f"[GATEIO DISCOVER] Found {len(pairs)} qualifying pairs")
        return pairs
    except Exception as e:
        log.error(f"[GATEIO DISCOVER] Failed: {e}")
        return []


def discover_mexc(min_volume: float, exclude_keywords: list) -> list:
    try:
        resp = requests.get("https://api.mexc.com/api/v3/ticker/24hr", timeout=15)
        data = resp.json()
        pairs = []
        for t in data:
            sym_raw = t.get("symbol", "")
            if not sym_raw.endswith("USDT"):
                continue
            coin = sym_raw[:-4]
            sym  = f"{coin}-USDT"
            if should_exclude(sym, exclude_keywords):
                continue
            try:
                vol = float(t.get("quoteVolume", 0))
            except Exception:
                continue
            if vol < min_volume:
                continue
            pairs.append((sym, vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        log.info(f"[MEXC DISCOVER] Found {len(pairs)} qualifying pairs")
        return pairs
    except Exception as e:
        log.error(f"[MEXC DISCOVER] Failed: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

DISCOVERY_FNS = {
    "kucoin":  discover_kucoin,
    "binance": discover_binance,
    "bybit":   discover_bybit,
    "kraken":  discover_kraken,
    "okx":     discover_okx,
    "gateio":  discover_gateio,
    "mexc":    discover_mexc,
}


# ══════════════════════════════════════════════════════════════════════════════
#  NEWS SCORING
# ══════════════════════════════════════════════════════════════════════════════

def score_coins_by_news(symbols: list) -> dict:
    """Wrapper — delegates to news_aggregator's combined news+data scoring."""
    try:
        from news_aggregator import score_coins_by_news_and_data
        return score_coins_by_news_and_data(symbols)
    except Exception as e:
        log.warning(f"[DISCOVERY] News+data scoring failed: {e}")
        return {}
def get_top_coins(exchange_name: str, max_coins: int,
                  min_volume: float, exclude_keywords: list,
                  use_news_scoring: bool = True,
                  use_correlation_filter: bool = False) -> list:
    """
    Discover all qualifying USDT pairs on the exchange.
    Ranks by a combined score of:
      - 70% 24hr volume (liquidity and momentum)
      - 30% news sentiment score (bullish coverage = higher priority)

    If use_correlation_filter is True, walks the ranked list and skips
    any candidate that's highly correlated (>0.80) with a coin already
    selected — picking the next-best candidate instead. This actively
    avoids ending up with e.g. 6 coins that are all just "BTC with extra
    steps" and call that diversification.

    Returns top max_coins as a list of symbols.
    """
    fn = DISCOVERY_FNS.get(exchange_name)
    if not fn:
        log.warning(f"No discovery function for {exchange_name}")
        return []

    all_pairs = fn(min_volume, exclude_keywords)
    if not all_pairs:
        return []

    symbols = [sym for sym, vol in all_pairs]

    # ── News scoring ───────────────────────────────────────────────────────
    news_scores = {}
    if use_news_scoring:
        news_scores = score_coins_by_news(symbols)

    # ── Combined ranking ───────────────────────────────────────────────────
    # Normalise volume to 0-10 scale
    max_vol = max(vol for _, vol in all_pairs) or 1

    ranked = []
    for sym, vol in all_pairs:
        coin       = sym.split("-")[0].upper()
        vol_score  = (vol / max_vol) * 10           # 0-10
        news_score = news_scores.get(coin, 0.0)     # -5 to +5
        # Shift news_score to 0-10 range: 0 → 5, +5 → 10, -5 → 0
        news_norm  = (news_score + 5)               # 0-10

        # Combined: 70% volume, 30% news
        combined   = (vol_score * 0.70) + (news_norm * 0.30)
        ranked.append((sym, combined, vol_score, news_norm, news_score))

    ranked.sort(key=lambda x: x[1], reverse=True)

    # ── Correlation-aware selection ─────────────────────────────────────────
    if use_correlation_filter and len(ranked) > max_coins:
        top = _select_with_correlation_filter(ranked, max_coins)
    else:
        top = ranked[:max_coins]

    log.info(f"[{exchange_name.upper()}] Top {len(top)} coins (volume + news"
            f"{' + correlation' if use_correlation_filter else ''}):")
    for i, (sym, combined, vol_s, news_n, news_raw) in enumerate(top, 1):
        coin       = sym.split("-")[0]
        news_label = "📈" if news_raw > 1 else "📉" if news_raw < -1 else "➡️"
        log.info(f"  {i:>3}. {sym:<15} score={combined:.2f} "
                 f"(vol={vol_s:.1f} news={news_raw:+.1f}{news_label})")

    return [sym for sym, *_ in top]


def _select_with_correlation_filter(ranked: list, max_coins: int,
                                    correlation_cap: float = 0.80) -> list:
    """
    Greedily walks the ranked candidate list. Always takes the top-ranked
    coin first. For each subsequent candidate, checks its correlation
    against every coin already selected — if it's too correlated with any
    of them (e.g. another large-cap that just moves with BTC), it's
    skipped in favour of the next-best candidate instead.

    Falls back to plain top-N ranking (no skipping) if the correlation
    check itself fails for any reason — a slow/failed network call here
    should never block coin discovery from completing.
    """
    try:
        from portfolio_correlation import CorrelationChecker
        checker = CorrelationChecker(lookback_days=14)   # shorter window — faster, still meaningful

        selected      = []
        selected_syms = []

        for candidate in ranked:
            sym = candidate[0]
            if len(selected) >= max_coins:
                break

            if not selected_syms:
                # First pick is always the top-ranked coin, no correlation check needed
                selected.append(candidate)
                selected_syms.append(sym)
                continue

            # Check correlation against everything already selected
            check_list = selected_syms + [sym]
            corr = checker.check(check_list)

            too_correlated = False
            if not corr.empty:
                candidate_coin = sym.split("-")[0]
                if candidate_coin in corr.columns:
                    for existing_sym in selected_syms:
                        existing_coin = existing_sym.split("-")[0]
                        if existing_coin in corr.columns:
                            c = abs(corr.loc[candidate_coin, existing_coin])
                            if c >= correlation_cap:
                                too_correlated = True
                                log.debug(f"[CORR] Skipping {sym} — {c:.2f} correlated with {existing_sym}")
                                break

            if not too_correlated:
                selected.append(candidate)
                selected_syms.append(sym)

        # If correlation filtering left us short (e.g. everything is correlated
        # in a strong bull run), fill remaining slots from the original ranking
        # rather than running with fewer coins than the tier allows.
        if len(selected) < max_coins:
            for candidate in ranked:
                if len(selected) >= max_coins:
                    break
                if candidate[0] not in selected_syms:
                    selected.append(candidate)
                    selected_syms.append(candidate[0])

        return selected[:max_coins]

    except Exception as e:
        log.warning(f"[CORR] Correlation filter failed ({e}) — falling back to plain top-N ranking")
        return ranked[:max_coins]


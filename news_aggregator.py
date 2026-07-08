"""
Crypto News & Data Aggregator
===============================
Fetches live data from 10 sources — news outlets AND data platforms:

NEWS OUTLETS (RSS feeds, free):
  1. The Block        — institutional, data-driven journalism
  2. CoinDesk         — breaking news, macro, regulatory
  3. Blockworks       — DeFi, institutional finance
  4. Cointelegraph    — altcoin updates, market analysis
  5. Bloomberg Crypto — macro-economic impacts on crypto
  6. Forbes Crypto    — mainstream financial coverage

DATA & ANALYTICS PLATFORMS (public APIs, free):
  7. CoinGecko        — price ranking, legitimacy scores, volume
  8. CoinMarketCap    — market cap, historical data, trading volume
  9. Messari          — institutional research, screeners
 10. Glassnode        — on-chain metrics (exchange inflows/outflows)

All news cached per coin for 10 minutes.
All market data cached globally for 15 minutes.
Headlines are de-duplicated across outlets and weighted by recency and
per-source credibility before scoring. Zero cost — all public endpoints,
unless AI_SENTIMENT_ENABLED is turned on in config.py (adds a Claude API
call per scoring pass).
"""

import difflib
import json
import logging
import os
import re
import threading
import time
from email.utils import parsedate_to_datetime

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import config

log = logging.getLogger(__name__)

try:
    from retry_utils import fetch_with_retry
    HAS_RETRY = True
except ImportError:
    HAS_RETRY = False
    def fetch_with_retry(url, **kwargs):
        return requests.get(url, timeout=kwargs.get("timeout", 10))

# ── News RSS sources ───────────────────────────────────────────────────────
NEWS_SOURCES = {
    "The Block": {
        "rss":    "https://www.theblock.co/rss/feed.xml",
        "backup": "https://www.theblock.co/feed",
        "focus":  "institutional, on-chain data, regulatory",
        "weight": 1.5,   # higher weight = more trusted for scoring
    },
    "CoinDesk": {
        "rss":    "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "backup": "https://feeds.feedburner.com/CoinDesk",
        "focus":  "breaking news, macro, Bitcoin, Ethereum",
        "weight": 1.4,
    },
    "Blockworks": {
        "rss":    "https://blockworks.co/feed",
        "backup": "https://blockworks.co/news/feed",
        "focus":  "institutional finance, DeFi analysis",
        "weight": 1.3,
    },
    "Cointelegraph": {
        "rss":    "https://cointelegraph.com/rss",
        "backup": "https://cointelegraph.com/rss/tag/altcoin",
        "focus":  "altcoin updates, market analysis",
        "weight": 1.2,
    },
    "Bloomberg Crypto": {
        "rss":    "https://feeds.bloomberg.com/crypto/news.rss",
        "backup": "https://www.bloomberg.com/feed/podcast/etf-iq.xml",
        "focus":  "macro-economic impacts on digital assets",
        "weight": 1.5,   # high credibility
    },
    "Forbes Crypto": {
        "rss":    "https://www.forbes.com/digital-assets/feed/",
        "backup": "https://www.forbes.com/crypto-blockchain/feed/",
        "focus":  "mainstream financial coverage of crypto",
        "weight": 1.3,
    },
}

# ── Data platform API endpoints ────────────────────────────────────────────
DATA_SOURCES = {
    "coingecko": {
        "trending":     "https://api.coingecko.com/api/v3/search/trending",
        "market_data":  "https://api.coingecko.com/api/v3/coins/markets",
        "global":       "https://api.coingecko.com/api/v3/global",
    },
    "coinmarketcap": {
        # Public endpoints (no API key for basic data)
        "listings":     "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing",
    },
    "messari": {
        "assets":       "https://data.messari.io/api/v1/assets",
        "news":         "https://data.messari.io/api/v1/news",
    },
    "glassnode": {
        # Free tier endpoints
        "market":       "https://api.glassnode.com/v1/metrics/market/price_usd_close",
    },
}

# ── Caches ─────────────────────────────────────────────────────────────────
# Kept short deliberately — these are refreshed on every dashboard load /
# strategy tick, and RSS feeds themselves rarely publish faster than this
# anyway, so there's little value (and real rate-limit risk) in going lower.
_news_cache      = {}          # { coin: { headlines, fetched_at } }
_cache_lock      = threading.Lock()
_CACHE_TTL       = timedelta(minutes=10)

_market_cache    = {"data": {}, "fetched_at": datetime.min}
_market_lock     = threading.Lock()
_MARKET_CACHE_TTL = timedelta(minutes=15)

# Sentiment keywords with weights
POSITIVE_KW = {
    "surge":2.0,"rally":2.0,"breakout":2.0,"all-time high":3.0,"ath":2.5,
    "bullish":2.0,"adoption":1.5,"partnership":1.5,"launch":1.0,"upgrade":1.5,
    "listing":1.0,"inflows":1.5,"etf":2.0,"approval":2.0,"gains":1.5,
    "record":1.5,"growth":1.0,"rises":1.0,"strong":1.0,"accumulation":1.5,
    "buy":1.0,"pump":1.5,"moon":1.0,"recovery":1.5,"outperform":1.5,
}
NEGATIVE_KW = {
    "crash":-2.5,"hack":-3.0,"exploit":-3.0,"ban":-2.5,"bearish":-2.0,
    "dump":-2.0,"fraud":-3.0,"scam":-3.0,"lawsuit":-2.0,"sec":-1.5,
    "investigation":-2.0,"regulation":-1.0,"outflow":-1.5,"loss":-1.5,
    "falls":-1.0,"drops":-1.0,"plunge":-2.0,"concern":-1.0,"warning":-1.5,
    "delisting":-3.0,"stolen":-2.5,"rug":-3.0,"liquidat":-2.0,
}

# Words that flip the meaning of a keyword match within the same line —
# without this, "no crash," "avoided a hack," or "denies fraud" score just
# as negative as an actual crash/hack/fraud headline.
NEGATION_WORDS = {
    "no", "not", "never", "denies", "denied", "avoids", "avoided", "avert",
    "averted", "isn't", "wasn't", "won't", "didn't", "doesn't", "without",
    "unlikely", "rules out", "despite", "rejects", "rejected",
}
_NEGATION_WINDOW = 40   # chars to look back before a keyword match

COIN_ALIASES = {
    "BTC":["bitcoin","btc"],"ETH":["ethereum","eth","ether"],
    "SOL":["solana","sol"],"XRP":["xrp","ripple"],
    "DOGE":["dogecoin","doge"],"ADA":["cardano","ada"],
    "POL":["polygon","matic","pol"],"DOT":["polkadot","dot"],
    "AVAX":["avalanche","avax"],"LINK":["chainlink","link"],
    "LTC":["litecoin","ltc"],"BCH":["bitcoin cash","bch"],
    "UNI":["uniswap","uni"],"ATOM":["cosmos","atom"],
    "XLM":["stellar","xlm"],"RVN":["ravencoin","rvn"],
    "PUFFER":["puffer"],"SIREN":["siren"],
}


# ══════════════════════════════════════════════════════════════════════════════
#  RSS FETCHING
# ══════════════════════════════════════════════════════════════════════════════

def _parse_pubdate(raw: str):
    """Best-effort parse of an RSS pubDate (RFC 822) or Atom updated/
    published (ISO 8601) timestamp. Returns None on anything unparseable
    rather than raising — a missing/odd date shouldn't break scoring,
    it just means that headline falls back to a neutral recency weight."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fetch_rss(url: str, timeout: int = 10) -> list:
    """Fetch and parse RSS feed. Returns list of (title, description, published_at) tuples — published_at is a datetime or None."""
    try:
        if HAS_RETRY:
            resp = fetch_with_retry(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; CryptoBot/1.0)",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=timeout, max_attempts=2)
        else:
            resp = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0"}, timeout=timeout)

        if not resp or not resp.ok:
            return []

        root  = ET.fromstring(resp.content)
        items = []

        for item in root.findall(".//item")[:15]:
            title = item.findtext("title", "").strip()
            desc  = item.findtext("description", "").strip()
            # Strip HTML from description
            desc = re.sub(r"<[^>]+>", " ", desc)[:300]
            pub_dt = _parse_pubdate(item.findtext("pubDate", ""))
            if title and len(title) > 5:
                items.append((title, desc, pub_dt))

        if not items:
            # Atom format fallback
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//a:entry", ns)[:15]:
                title = entry.findtext("a:title", "", ns).strip()
                desc  = entry.findtext("a:summary", "", ns).strip()[:300]
                pub_raw = (entry.findtext("a:updated", "", ns)
                          or entry.findtext("a:published", "", ns))
                pub_dt  = _parse_pubdate(pub_raw)
                if title:
                    items.append((title, desc, pub_dt))

        return items
    except Exception as e:
        log.debug(f"[NEWS] RSS fetch failed {url}: {e}")
        return []


def _recency_weight(pub_dt) -> float:
    """Older headlines count for less — a story published minutes ago
    should move a coin's score far more than one still sitting in the
    cache from ~an hour ago. Unknown-age headlines get a flat mid-weight
    rather than being discarded or treated as fresh."""
    if pub_dt is None:
        return 0.7
    try:
        now = datetime.now(pub_dt.tzinfo)
        age_hours = (now - pub_dt).total_seconds() / 3600
    except Exception:
        return 0.7
    if age_hours < 0:
        return 1.0
    return max(0.15, 1.0 - age_hours / 24)


def _dedupe_headlines(items: list) -> list:
    """Collapses near-identical headlines that multiple outlets ran on
    the same story — without this, one event gets counted several times
    over just because more outlets covered it, not because sentiment
    toward the coin is actually stronger. Keeps the first (usually
    highest-weighted-source) occurrence of each story."""
    seen    = []
    deduped = []
    for item in items:
        title = item[0]
        norm  = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
        if not norm:
            continue
        if any(difflib.SequenceMatcher(None, norm, s).ratio() > 0.80 for s in seen):
            continue
        seen.append(norm)
        deduped.append(item)
    return deduped


# ══════════════════════════════════════════════════════════════════════════════
#  DATA PLATFORM FETCHING
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_coingecko_market() -> dict:
    """Fetch trending coins, market sentiment and global data from CoinGecko."""
    result = {"trending": [], "fear_greed": "unknown", "btc_dominance": 0}
    try:
        # Trending coins
        resp = requests.get(DATA_SOURCES["coingecko"]["trending"], timeout=10)
        if resp.ok:
            data = resp.json()
            coins = data.get("coins", [])
            result["trending"] = [c["item"]["symbol"].upper() for c in coins[:7]]

        # Global market data
        resp2 = requests.get(DATA_SOURCES["coingecko"]["global"], timeout=10)
        if resp2.ok:
            gdata = resp2.json().get("data", {})
            result["btc_dominance"] = round(
                gdata.get("market_cap_percentage", {}).get("btc", 0), 1)
            change = gdata.get("market_cap_change_percentage_24h_usd", 0)
            result["market_change_24h"] = round(change, 2)

        log.info(f"[DATA] CoinGecko: trending={result['trending'][:5]} "
                f"BTC dom={result['btc_dominance']}%")
    except Exception as e:
        log.debug(f"[DATA] CoinGecko market fetch failed: {e}")
    return result


def _fetch_messari_news() -> list:
    """Fetch latest news from Messari (institutional-grade)."""
    try:
        resp = requests.get(DATA_SOURCES["messari"]["news"],
                           params={"limit": 10}, timeout=10)
        if resp.ok:
            items = resp.json().get("data", [])
            return [(item.get("title",""), item.get("content","")[:200])
                    for item in items if item.get("title")]
    except Exception as e:
        log.debug(f"[DATA] Messari news failed: {e}")
    return []


def _fetch_cmc_trending() -> list:
    """Fetch trending coins from CoinMarketCap public endpoint."""
    try:
        resp = requests.get(
            "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing",
            params={"start": 1, "limit": 20, "sortBy": "percent_change_24h",
                    "sortType": "desc", "convert": "USD", "cryptoType": "all"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.ok:
            data  = resp.json().get("data", {}).get("cryptoCurrencyList", [])
            return [c.get("symbol","") for c in data[:10]]
    except Exception as e:
        log.debug(f"[DATA] CMC trending failed: {e}")
    return []


# ══════════════════════════════════════════════════════════════════════════════
#  MARKET DATA SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def get_market_context() -> dict:
    """
    Returns a combined market context dict:
    {
      trending_coingecko: [...],
      trending_cmc: [...],
      btc_dominance: 58.3,
      market_change_24h: +2.1,
      messari_headlines: [...],
    }
    Cached for 30 minutes.
    """
    with _market_lock:
        age = datetime.now() - _market_cache["fetched_at"]
        if age < _MARKET_CACHE_TTL and _market_cache["data"]:
            return _market_cache["data"]

    log.info("[DATA] Refreshing market context from CoinGecko, CMC, Messari...")
    ctx = {}

    cg  = _fetch_coingecko_market()
    ctx["trending_coingecko"] = cg.get("trending", [])
    ctx["btc_dominance"]      = cg.get("btc_dominance", 0)
    ctx["market_change_24h"]  = cg.get("market_change_24h", 0)

    ctx["trending_cmc"]       = _fetch_cmc_trending()
    ctx["messari_headlines"]  = _fetch_messari_news()

    with _market_lock:
        _market_cache["data"]       = ctx
        _market_cache["fetched_at"] = datetime.now()

    return ctx


# ══════════════════════════════════════════════════════════════════════════════
#  NEWS SCORING
# ══════════════════════════════════════════════════════════════════════════════

def _is_negated(line: str, match_idx: int) -> bool:
    """Checks the text just before a keyword match for a negation word,
    e.g. "no crash", "denies fraud", "avoided a hack" — without this,
    those score exactly as bearish as an actual crash/fraud/hack headline."""
    start   = max(0, match_idx - _NEGATION_WINDOW)
    context = line[start:match_idx]
    return any(f" {neg} " in f" {context} " for neg in NEGATION_WORDS)


def _score_line(line: str, weight: float = 1.0) -> float:
    """Score a single line of text for sentiment. A keyword preceded by a
    negation word within _NEGATION_WINDOW chars has its contribution
    flipped instead of counted at face value."""
    score = 0.0
    for kw, w in POSITIVE_KW.items():
        idx = line.find(kw)
        if idx != -1:
            contribution = w * weight
            score += -contribution if _is_negated(line, idx) else contribution
    for kw, w in NEGATIVE_KW.items():
        idx = line.find(kw)
        if idx != -1:
            contribution = w * weight   # w is negative
            score += -contribution if _is_negated(line, idx) else contribution
    return score


def _coin_mentioned(text: str, coin: str) -> bool:
    """Check if a coin is mentioned in text."""
    aliases = COIN_ALIASES.get(coin.upper(), [coin.lower()])
    aliases = [a for a in aliases if len(a) >= 3]
    return any(a in text.lower() for a in aliases)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN PUBLIC FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_market_news() -> str:
    """
    Fetch general market headlines from all 6 news sources.
    Returns formatted string for AI prompt context.
    Cached 10 minutes.
    """
    with _market_lock:
        cached = _market_cache.get("news_text", "")
        age    = datetime.now() - _market_cache.get("news_fetched_at", datetime.min)
        if cached and age < _CACHE_TTL:
            return cached

    log.info("[NEWS] Fetching from 6 news sources + 3 data platforms...")
    sections = []

    # News RSS sources — de-duped and newest-first so the AI prompt leads
    # with what's actually fresh rather than whatever order the feed returns.
    for source_name, info in NEWS_SOURCES.items():
        items = _fetch_rss(info["rss"])
        if not items:
            items = _fetch_rss(info["backup"])
        items = _dedupe_headlines(items)
        items.sort(key=lambda it: it[2].timestamp() if it[2] else 0, reverse=True)
        if items:
            headlines = "\n".join(f"• {t}" for t, _, _ in items[:3])
            sections.append(f"[{source_name}]\n{headlines}")

    # Messari institutional news
    messari = _fetch_messari_news()
    if messari:
        headlines = "\n".join(f"• {t}" for t, _ in messari[:3])
        sections.append(f"[Messari]\n{headlines}")

    # Market data context
    ctx = get_market_context()
    if ctx.get("trending_coingecko"):
        sections.append(
            f"[CoinGecko Trending]\n"
            f"• Top trending: {', '.join(ctx['trending_coingecko'][:7])}\n"
            f"• BTC dominance: {ctx.get('btc_dominance',0)}%\n"
            f"• 24h market change: {ctx.get('market_change_24h',0):+.1f}%"
        )
    if ctx.get("trending_cmc"):
        sections.append(
            f"[CoinMarketCap Top Movers]\n"
            f"• {', '.join(ctx['trending_cmc'][:10])}"
        )

    result = "\n\n".join(sections) if sections else "No market news retrieved."

    with _market_lock:
        _market_cache["news_text"]       = result
        _market_cache["news_fetched_at"] = datetime.now()

    return result


def fetch_coin_news(coin: str) -> str:
    """
    Fetch news and data signals relevant to a specific coin.
    Combines RSS headlines + CoinGecko trending + CMC momentum.
    Cached per coin for 10 minutes.
    """
    coin_upper = coin.upper()

    with _cache_lock:
        cached = _news_cache.get(coin_upper)
        if cached and (datetime.now() - cached["fetched_at"]) < _CACHE_TTL:
            return cached["headlines"]

    log.info(f"[NEWS] Fetching news for {coin_upper} from all sources...")
    relevant   = []
    market_ctx = []

    # Weighted news from all RSS sources
    for source_name, info in NEWS_SOURCES.items():
        items = _fetch_rss(info["rss"])
        if not items:
            items = _fetch_rss(info["backup"])
        items  = _dedupe_headlines(items)
        weight = info.get("weight", 1.0)
        for title, desc, pub_dt in items:
            if _coin_mentioned(f"{title} {desc}", coin_upper):
                relevant.append((source_name, title, weight * _recency_weight(pub_dt)))
            elif len(market_ctx) < 4:
                market_ctx.append((source_name, title))

    # Messari institutional news
    for title, desc in _fetch_messari_news():
        if _coin_mentioned(f"{title} {desc}", coin_upper):
            relevant.append(("Messari", title, 1.5))

    # Market data signals
    ctx          = get_market_context()
    data_signals = []
    trending_cg  = ctx.get("trending_coingecko", [])
    trending_cmc = ctx.get("trending_cmc", [])

    if coin_upper in trending_cg:
        data_signals.append(f"🔥 {coin_upper} is TRENDING on CoinGecko right now")
    if coin_upper in trending_cmc:
        data_signals.append(f"📈 {coin_upper} is a TOP MOVER on CoinMarketCap (24h)")

    # Build result string
    parts = []

    if relevant:
        coin_lines = "\n".join(
            f"  [{src}] • {title}" for src, title, _ in relevant[:8]
        )
        parts.append(f"COIN-SPECIFIC NEWS ({coin_upper}):\n{coin_lines}")
    else:
        parts.append(f"No specific news found for {coin_upper}.")

    if data_signals:
        parts.append("DATA PLATFORM SIGNALS:\n" +
                     "\n".join(f"  {s}" for s in data_signals))

    if market_ctx:
        mkt_lines = "\n".join(f"  [{src}] • {t}" for src, t in market_ctx[:3])
        parts.append(f"GENERAL MARKET CONTEXT:\n{mkt_lines}")

    btc_dom = ctx.get("btc_dominance", 0)
    mkt_chg = ctx.get("market_change_24h", 0)
    parts.append(
        f"MARKET STATUS: BTC dominance {btc_dom}% | "
        f"Market 24h change {mkt_chg:+.1f}%"
    )

    result = "\n\n".join(parts)

    with _cache_lock:
        _news_cache[coin_upper] = {
            "headlines":  result,
            "fetched_at": datetime.now(),
        }

    log.info(f"[NEWS] {coin_upper}: {len(relevant)} relevant headlines, "
             f"{len(data_signals)} data signals")
    return result


def get_news_summary(coin: str) -> str:
    """Main entry point for AI trade analysis."""
    try:
        news = fetch_coin_news(coin)
        return (
            "\n\nLIVE MARKET INTELLIGENCE\n"
            "(Sources: The Block, CoinDesk, Blockworks, Cointelegraph, "
            "Bloomberg Crypto, Forbes Crypto, Messari, CoinGecko, CoinMarketCap)\n"
            f"{news}"
        )
    except Exception as e:
        log.warning(f"[NEWS] Failed for {coin}: {e}")
        return "\n\nNews context: unavailable"


def score_coins_by_news_and_data(symbols: list) -> dict:
    """
    Score coins using news sentiment + data platform signals.
    Returns { "BTC": 3.2, "ETH": 2.1, "DOGE": -1.4, ... }
    Higher = more bullish signals.

    Headlines are pulled straight from each source (not the pre-formatted
    fetch_market_news() text) so per-source credibility weight and recency
    decay can actually be applied, and are de-duplicated across outlets
    first so one story doesn't get counted 3-6x just because several
    outlets ran it. If config.AI_SENTIMENT_ENABLED is set, the keyword
    score is blended with a Claude-based read of the same headlines.
    """
    try:
        ctx          = get_market_context()
        trending_cg  = set(ctx.get("trending_coingecko", []))
        trending_cmc = set(ctx.get("trending_cmc", []))
        mkt_change   = ctx.get("market_change_24h", 0)
        scores       = {}

        # Gather every headline from every source once, tagged with its
        # source credibility weight and publish time, then de-dupe across
        # outlets before scoring.
        raw_items = []
        for source_name, info in NEWS_SOURCES.items():
            items = _fetch_rss(info["rss"])
            if not items:
                items = _fetch_rss(info["backup"])
            weight = info.get("weight", 1.0)
            for title, desc, pub_dt in items:
                raw_items.append((title, desc, pub_dt, weight))
        for title, desc in _fetch_messari_news():
            raw_items.append((title, desc, None, 1.5))

        raw_items = _dedupe_headlines(raw_items)

        for sym in symbols:
            coin    = sym.split("-")[0].upper()
            if len(coin) < 2:
                continue

            score = 0.0

            # News sentiment scoring — weighted by source credibility and
            # how recently the story ran.
            for title, desc, pub_dt, src_weight in raw_items:
                text = f"{title} {desc}".lower()
                if _coin_mentioned(text, coin):
                    score += (0.5 + _score_line(text)) * src_weight * _recency_weight(pub_dt)

            # Data platform bonuses
            if coin in trending_cg:
                score += 2.0   # CoinGecko trending is a strong signal
            if coin in trending_cmc:
                score += 1.5   # CMC top mover

            # Market-wide context
            if mkt_change > 3:
                score += 0.5   # rising tide lifts all boats
            elif mkt_change < -3:
                score -= 0.5   # broad market selloff

            scores[coin] = round(score, 2)

        # Normalise to -5 to +5
        if scores:
            max_abs = max(abs(v) for v in scores.values()) or 1
            scores  = {k: round(v / max_abs * 5, 2) for k, v in scores.items()}

        if getattr(config, "AI_SENTIMENT_ENABLED", False):
            scores = _blend_ai_sentiment(scores, raw_items, symbols)

        top = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10])
        log.info(f"[SCORE] Top coins by news+data: {top}")

        _persist_sentiment_history(scores)
        return scores

    except Exception as e:
        log.warning(f"[SCORE] Scoring failed: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
#  OPTIONAL: AI-BASED SENTIMENT (Claude) — off by default, see
#  config.AI_SENTIMENT_ENABLED. Keyword scoring above is free and always
#  runs; this adds an LLM read of the same de-duped headlines on top,
#  which handles negation/sarcasm/context far better than keyword matching
#  but costs a small amount per scoring pass.
# ══════════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_AI_SENTIMENT_MODEL = "claude-sonnet-4-6"


def _ai_score_coins(coin_headlines: dict) -> dict:
    """Sends up to a handful of headlines per coin to Claude and asks for
    a -5..+5 sentiment score per coin, returned as strict JSON. Returns {}
    on any failure so callers can fall back to the keyword scores untouched."""
    if not coin_headlines:
        return {}
    try:
        prompt_lines = []
        for coin, headlines in coin_headlines.items():
            prompt_lines.append(f"{coin}:")
            for h in headlines[:6]:
                prompt_lines.append(f"  - {h}")
        prompt = (
            "Score the market sentiment for each of these cryptocurrencies "
            "based on the headlines listed under it, from -5 (very bearish) "
            "to +5 (very bullish). Watch for negation and context (e.g. "
            "\"no crash\" is NOT bearish). Respond with ONLY a JSON object "
            "mapping each coin symbol to a number, nothing else.\n\n"
            + "\n".join(prompt_lines)
        )
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key":         config.AI_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      _AI_SENTIMENT_MODEL,
                "max_tokens": 300,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        if not resp.ok:
            log.warning(f"[AI SENTIMENT] Request failed: {resp.status_code}")
            return {}
        data  = resp.json()
        texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        raw   = "\n".join(texts).strip()
        raw   = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        return {k.upper(): float(v) for k, v in parsed.items()}
    except Exception as e:
        log.warning(f"[AI SENTIMENT] Failed, keeping keyword scores: {e}")
        return {}


def _blend_ai_sentiment(keyword_scores: dict, raw_items: list, symbols: list) -> dict:
    """Blends the free keyword-based scores with an AI read of the same
    headlines (50/50), falling back to pure keyword scores for any coin
    the AI call didn't return or if the call fails outright."""
    coins = {sym.split("-")[0].upper() for sym in symbols if len(sym.split("-")[0]) >= 2}
    coin_headlines = {}
    for coin in coins:
        matches = [title for title, desc, _, _ in raw_items
                  if _coin_mentioned(f"{title} {desc}", coin)]
        if matches:
            coin_headlines[coin] = matches

    ai_scores = _ai_score_coins(coin_headlines)
    if not ai_scores:
        return keyword_scores

    blended = {}
    for coin, kw_score in keyword_scores.items():
        ai_score = ai_scores.get(coin)
        blended[coin] = round((kw_score + ai_score) / 2, 2) if ai_score is not None else kw_score
    return blended


# ══════════════════════════════════════════════════════════════════════════════
#  SENTIMENT HISTORY — small durable time series so the dashboard can plot
#  a trend instead of only ever showing the current snapshot.
# ══════════════════════════════════════════════════════════════════════════════

SENTIMENT_HISTORY_PATH = os.path.join("logs", "sentiment_history.jsonl")
_history_write_lock = threading.Lock()


def _persist_sentiment_history(scores: dict) -> None:
    """Appends one {timestamp, scores} line to the sentiment history log.
    Never raises — a failed write here should never interrupt scoring."""
    if not scores:
        return
    try:
        os.makedirs("logs", exist_ok=True)
        record = {"recorded_at": datetime.now().isoformat(), "scores": scores}
        with _history_write_lock:
            with open(SENTIMENT_HISTORY_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    except Exception as e:
        log.debug(f"[SCORE] Failed to persist sentiment history: {e}")


def load_sentiment_history(hours: int = 48) -> list:
    """Reads back the sentiment history log, optionally limited to the
    last `hours` hours. Returns a list of {recorded_at, scores} dicts,
    oldest first. Silently skips unparseable lines (e.g. a truncated
    final line from a crash mid-write)."""
    if not os.path.exists(SENTIMENT_HISTORY_PATH):
        return []
    cutoff  = datetime.now() - timedelta(hours=hours) if hours else None
    records = []
    with open(SENTIMENT_HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if cutoff and datetime.fromisoformat(rec["recorded_at"]) < cutoff:
                    continue
                records.append(rec)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return records


def clear_cache(coin: str = None):
    with _cache_lock:
        if coin:
            _news_cache.pop(coin.upper(), None)
        else:
            _news_cache.clear()

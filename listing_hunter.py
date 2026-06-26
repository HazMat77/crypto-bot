"""
New Listing Hunter
===================
Monitors KuCoin's announcement feed for new coin listings.
When a new listing is detected:
  1. Parses the listing date and time
  2. Schedules an automatic buy at exactly that time
  3. Applies a separate take-profit strategy for listing pumps
  4. Sends Telegram alerts throughout

Why new listings matter:
  New coins on KuCoin often spike 50-200%+ in the first few minutes.
  Being first in automatically at listing time captures this pump.
  The bot uses a tighter take-profit (10-15%) for listings vs normal trades.

Strategy for new listings (different from regular trading):
  - Buy $5 at listing open
  - Take profit at +15% (listing pump target)
  - Stop loss at -8% (listings can crash fast too)
  - Max hold 2 hours (listing pumps are short-lived)
  - No RSI/MA required — time-based entry only
"""

import re
import logging
import threading
import requests
import time
from datetime import datetime, timezone
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ── KuCoin announcement endpoints ─────────────────────────────────────────
KUCOIN_ANNOUNCEMENTS_URL = "https://www.kucoin.com/api/v1/bulletins"
KUCOIN_NEWS_URL          = "https://www.kucoin.com/news/categories/new-listings"
KUCOIN_API_ANNOUNCEMENTS = "https://www.kucoin.com/api/v1/announcement?annType=new-listings&lang=en_US&page=1&pageSize=20"

# ── Listing-specific risk settings ────────────────────────────────────────
LISTING_BUY_USDT      = 5.0      # always $5 per new listing
LISTING_TAKE_PROFIT   = 0.15     # 15% take profit (listings pump hard)
LISTING_STOP_LOSS     = 0.08     # 8% stop loss (listings can crash too)
LISTING_MAX_HOLD_MINS = 120      # 2 hour max hold for listing trades
LISTING_ENTRY_SECONDS = 5        # buy this many seconds after listing opens

# ── Patterns to detect listing announcements ──────────────────────────────
LISTING_PATTERNS = [
    r"will\s+list\s+([A-Z0-9]+)",
    r"listing\s+([A-Z0-9]+)\s+on",
    r"([A-Z0-9]+)\s+will\s+be\s+listed",
    r"new\s+listing[:\s]+([A-Z0-9]+)",
    r"([A-Z0-9]+)-USDT\s+trading",
    # Campaign/giveaway style titles, e.g. "Arcium (ARX) Listing Campaign"
    # The ticker is usually in parentheses right after the project name.
    r"\(([A-Z0-9]{2,8})\)\s+Listing\s+Campaign",
    r"\(([A-Z0-9]{2,8})\)\s+(?:will\s+be\s+|is\s+now\s+)?listed",
    # "X (TICKER) being listed on KuCoin" — appears in campaign body text
    r"\(([A-Z0-9]{2,8})\)\s+being\s+listed",
]

DATE_PATTERNS = [
    # ISO format: 2026-06-20 10:00 UTC
    r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)\s*(?:\(UTC\)|UTC)?",
    # Long format: June 20, 2026 at 10:00 AM UTC
    r"(\w+ \d{1,2},?\s+\d{4})\s+at\s+(\d{1,2}:\d{2}\s*(?:AM|PM)?)\s*(?:UTC)?",
    # Short format: 20/06/2026 10:00
    r"(\d{1,2}[./]\d{1,2}[./]\d{4})\s+(\d{2}:\d{2})",
    # Unix-style: at 10:00 UTC on June 20
    r"at\s+(\d{2}:\d{2})\s*(?:UTC)?\s+on\s+(\w+ \d{1,2},?\s+\d{4})",
    # "Trading Opening Time: 12:00 on June 22, 2026 (UTC)" — time-first, "on", then date
    r"(\d{1,2}:\d{2})\s+on\s+(\w+ \d{1,2},?\s+\d{4})\s*(?:\(UTC\)|UTC)?",
]

def _safe_parse_date(date_str: str, time_str: str) -> datetime:
    """Try multiple date formats, return None if all fail."""
    import calendar
    # Swap if time came first (for the last pattern)
    candidates = [
        f"{date_str} {time_str}",
        f"{time_str} {date_str}",
    ]
    formats = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%B %d, %Y %H:%M",   "%B %d %Y %H:%M",
        "%B %d, %Y %I:%M %p","%B %d %Y %I:%M %p",
        "%d/%m/%Y %H:%M",    "%m/%d/%Y %H:%M",
        "%H:%M %B %d, %Y",   "%H:%M %B %d %Y",
    ]
    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(candidate.strip(), fmt)
            except ValueError:
                continue
    return None


class NewListingHunter:
    """
    Monitors KuCoin for new listing announcements and auto-buys at launch.
    """

    def __init__(self, exchanges: dict, pool_usdt: dict, pool_locks: dict,
                 coin_holdings, coin_in_position, coin_buy_price, coin_buy_spent,
                 stats_lock, trade_count_ref, total_pnl_ref,
                 daily_lock, daily_trades):
        self.exchanges        = exchanges
        self.pool_usdt        = pool_usdt
        self.pool_locks       = pool_locks
        self.coin_holdings    = coin_holdings
        self.coin_in_position = coin_in_position
        self.coin_buy_price   = coin_buy_price
        self.coin_buy_spent   = coin_buy_spent
        self.stats_lock       = stats_lock
        self.daily_lock       = daily_lock
        self.daily_trades     = daily_trades

        self.seen_listings    = set()     # track already-scheduled listings
        self.scheduled        = {}        # { symbol: scheduled_time }
        self.lock             = threading.Lock()

    # ══════════════════════════════════════════════════════════════════════
    #  ANNOUNCEMENT FETCHING
    # ══════════════════════════════════════════════════════════════════════

    def fetch_announcements(self) -> list:
        """Fetch latest new listing announcements from KuCoin."""
        results = []

        # Method 1: KuCoin API endpoint
        try:
            resp = requests.get(
                KUCOIN_API_ANNOUNCEMENTS,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            if resp.ok:
                data  = resp.json()
                items = data.get("data", {}).get("items", [])
                for item in items:
                    title   = item.get("annTitle", "")
                    summary = item.get("annDesc", "")
                    if any(kw in title.lower() for kw in ["list", "trading", "new listing"]):
                        results.append({
                            "title":   title,
                            "content": summary,
                            "source":  "kucoin_api",
                        })
        except Exception as e:
            log.debug(f"[LISTING] API fetch failed: {e}")

        # Method 2: KuCoin news page scrape
        if not results:
            try:
                resp = requests.get(
                    KUCOIN_NEWS_URL,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=15,
                )
                if resp.ok:
                    soup  = BeautifulSoup(resp.text, "html.parser")
                    items = soup.find_all(["article", "div"], class_=re.compile(r"news|article|listing", re.I))
                    for item in items[:10]:
                        text = item.get_text(" ", strip=True)
                        if any(kw in text.lower() for kw in ["will list", "new listing", "trading pair"]):
                            results.append({
                                "title":   text[:200],
                                "content": text,
                                "source":  "web_scrape",
                            })
            except Exception as e:
                log.debug(f"[LISTING] Web scrape failed: {e}")

        return results

    # ══════════════════════════════════════════════════════════════════════
    #  PARSING
    # ══════════════════════════════════════════════════════════════════════

    def parse_coin_symbol(self, text: str) -> str:
        """Extract coin symbol from announcement text."""
        for pattern in LISTING_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                coin = match.group(1).upper()
                # Basic validation — real coin symbols 2-8 chars
                if 2 <= len(coin) <= 8 and coin.isalnum():
                    return coin
        return None

    def parse_listing_time(self, text: str) -> datetime:
        """Extract listing date/time from announcement text. Returns UTC datetime or None."""
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    g = match.groups()
                    dt = _safe_parse_date(g[0], g[1] if len(g) > 1 else "00:00")
                    if dt:
                        # Assume UTC if no timezone info
                        return dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
        return None

    # ══════════════════════════════════════════════════════════════════════
    #  SCHEDULING
    # ══════════════════════════════════════════════════════════════════════

    def schedule_listing_buy(self, coin: str, listing_time: datetime,
                             stop_event: threading.Event):
        """Schedule a buy thread to fire at exactly listing_time."""
        symbol = f"{coin}-USDT"
        key    = f"{coin}_{listing_time.isoformat()}"

        with self.lock:
            if key in self.seen_listings:
                return
            self.seen_listings.add(key)
            self.scheduled[symbol] = listing_time

        log.info(f"[LISTING] 🎯 Scheduled buy: {symbol} at {listing_time} UTC")
        self._tg_scheduled(coin, symbol, listing_time)

        # Launch a thread that sleeps until listing time then buys
        t = threading.Thread(
            target=self._listing_buy_worker,
            args=(coin, symbol, listing_time, stop_event),
            daemon=True,
            name=f"listing:{symbol}",
        )
        t.start()

    def _listing_buy_worker(self, coin: str, symbol: str,
                            listing_time: datetime, stop_event: threading.Event):
        """Sleeps until listing time, then buys and monitors the position."""
        now        = datetime.now(timezone.utc)
        wait_secs  = (listing_time - now).total_seconds() - LISTING_ENTRY_SECONDS

        if wait_secs > 86400 * 7:   # more than 7 days away — skip
            log.warning(f"[LISTING] {symbol} listing is >7 days away — skipping")
            return

        if wait_secs > 0:
            log.info(f"[LISTING] {symbol} — sleeping {wait_secs/3600:.1f}h until listing")
            # Sleep in chunks so stop_event can interrupt
            slept = 0
            while slept < wait_secs and not stop_event.is_set():
                chunk = min(60, wait_secs - slept)
                stop_event.wait(timeout=chunk)
                slept += chunk

                # Send reminder 5 minutes before
                remaining = wait_secs - slept
                if 290 <= remaining <= 310:
                    self._tg_reminder(coin, symbol, listing_time)

        if stop_event.is_set():
            return

        # ── Attempt the buy ────────────────────────────────────────────
        log.info(f"[LISTING] 🚀 {symbol} — listing time! Attempting buy...")

        for ex_name, exchange in self.exchanges.items():
            try:
                import config
                # Verify pair now exists
                try:
                    price = exchange.get_price(symbol)
                except Exception:
                    # Wait up to 30s for pair to become available
                    for attempt in range(6):
                        time.sleep(5)
                        try:
                            price = exchange.get_price(symbol)
                            break
                        except Exception:
                            if attempt == 5:
                                log.warning(f"[LISTING] {symbol} not tradeable yet on {ex_name}")
                                self._tg_failed(coin, symbol, ex_name, "Pair not yet available after 30s")
                                return

                qty             = round(LISTING_BUY_USDT / price, 6)
                listing_reserve = getattr(config, "LISTING_RESERVE_USDT", LISTING_BUY_USDT)

                if config.PAPER_TRADING:
                    with self.pool_locks[ex_name]:
                        # Listing buys USE the reserve — that's what it's there for
                        if self.pool_usdt[ex_name] < LISTING_BUY_USDT:
                            log.warning(f"[LISTING] Pool ${self.pool_usdt[ex_name]:.2f} too low for {symbol} buy")
                            self._tg_failed(coin, symbol, ex_name,
                                           f"Pool too low — need ${LISTING_BUY_USDT:.2f} USDT")
                            return
                        self.pool_usdt[ex_name]               -= LISTING_BUY_USDT
                        self.coin_holdings[ex_name][symbol]    = qty
                        self.coin_in_position[ex_name][symbol] = True
                        self.coin_buy_price[ex_name][symbol]   = price
                        self.coin_buy_spent[ex_name][symbol]   = LISTING_BUY_USDT
                        log.info(f"[LISTING] Reserve deployed — pool ${self.pool_usdt[ex_name]:.2f} "
                                f"(refills when position closes)")
                else:
                    exchange.place_market_buy(symbol, LISTING_BUY_USDT)
                    with self.pool_locks[ex_name]:
                        self.coin_in_position[ex_name][symbol] = True
                        self.coin_buy_price[ex_name][symbol]   = price
                        self.coin_buy_spent[ex_name][symbol]   = LISTING_BUY_USDT

                log.info(f"[LISTING] ✅ Bought {qty} {coin} @ ${price:.6f} on {ex_name.upper()}")
                self._tg_bought(coin, symbol, ex_name, price, qty)

                # ── Monitor the listing position ───────────────────────
                self._monitor_listing_position(coin, symbol, ex_name, exchange, price, stop_event)
                return

            except Exception as e:
                log.error(f"[LISTING] Buy failed on {ex_name}: {e}")
                self._tg_failed(coin, symbol, ex_name, str(e))

    def _monitor_listing_position(self, coin: str, symbol: str, ex_name: str,
                                   exchange, buy_price: float,
                                   stop_event: threading.Event):
        """
        Monitor a listing position with tighter TP/SL than normal trades.

        Resilience note: if exchange.get_price() fails repeatedly (common
        right after a brand-new listing — thin liquidity, API hiccups),
        this loop must NOT just retry forever without ever re-checking
        price — that would silently starve the stop-loss check while a
        position keeps losing money. After a few consecutive failures we
        back off and keep retrying, but we never give up monitoring; if
        failures persist for too long, we force-exit via the existing
        max-hold-time safety net rather than leaving the position
        unmonitored indefinitely.
        """
        start_time = datetime.now()
        log.info(f"[LISTING] Monitoring {symbol} — TP:{LISTING_TAKE_PROFIT*100:.0f}% SL:{LISTING_STOP_LOSS*100:.0f}%")

        consecutive_failures = 0
        MAX_CONSECUTIVE_FAILURES = 8   # ~ a few minutes of backoff before forcing an exit attempt

        while not stop_event.is_set():
            try:
                import config
                # Check max hold time
                hold_mins = (datetime.now() - start_time).total_seconds() / 60
                if hold_mins >= LISTING_MAX_HOLD_MINS:
                    if self._listing_sell(coin, symbol, ex_name, exchange, buy_price,
                                          "⏰ Max hold time reached", stop_event):
                        return
                    # Sell failed (e.g. price feed still down) — keep monitoring
                    # and retry rather than abandoning the position unclosed.

                # Check if still holding
                if not self.coin_in_position[ex_name].get(symbol, False):
                    return

                try:
                    price = exchange.get_price(symbol)
                except Exception as price_err:
                    consecutive_failures += 1
                    log.warning(f"[LISTING] {coin} price fetch failed "
                               f"({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}): {price_err}")

                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        # We genuinely cannot see the price — don't leave the
                        # position unmonitored indefinitely. Force an exit
                        # attempt. If it fails too (price feed still down),
                        # reset the counter and keep trying on the next
                        # pass instead of giving up on the position.
                        log.error(f"[LISTING] {coin} — {consecutive_failures} consecutive "
                                 f"price-fetch failures, forcing exit attempt")
                        if self._listing_sell(coin, symbol, ex_name, exchange, buy_price,
                                              "⚠️ Forced exit — price feed unreachable", stop_event):
                            return
                        consecutive_failures = 0
                        stop_event.wait(timeout=15)
                        continue

                    # Exponential-ish backoff so we don't hammer a struggling API,
                    # but cap it short so we keep checking the stop-loss often
                    backoff = min(30, 5 * consecutive_failures)
                    stop_event.wait(timeout=backoff)
                    continue

                # Got a price successfully — reset the failure counter
                consecutive_failures = 0

                pct_change = (price - buy_price) / buy_price

                log.info(f"[LISTING] {coin} @ ${price:.6f} ({pct_change*100:+.2f}%)")

                # Take profit
                if pct_change >= LISTING_TAKE_PROFIT:
                    if self._listing_sell(coin, symbol, ex_name, exchange, buy_price,
                                          f"✅ Take-profit hit: +{pct_change*100:.1f}%", stop_event):
                        return
                    # Sell failed — fall through and keep monitoring/retrying

                # Stop loss
                elif pct_change <= -LISTING_STOP_LOSS:
                    if self._listing_sell(coin, symbol, ex_name, exchange, buy_price,
                                          f"🛑 Stop-loss hit: {pct_change*100:.1f}%", stop_event):
                        return
                    # Sell failed — fall through and keep monitoring/retrying

            except Exception as e:
                log.warning(f"[LISTING] Monitor error: {e}")

            stop_event.wait(timeout=15)   # check every 15 seconds for listings

    def _listing_sell(self, coin: str, symbol: str, ex_name: str, exchange,
                      buy_price: float, reason: str, stop_event) -> bool:
        """Execute a listing position sell. Returns True only on confirmed success —
        callers must NOT treat the position as closed unless this returns True,
        otherwise a failed sell (e.g. price feed still down) would leave the
        position open with nothing left monitoring it."""
        import config
        try:
            price = exchange.get_price(symbol)
            qty   = self.coin_holdings[ex_name].get(symbol, 0)

            if config.PAPER_TRADING:
                proceeds = round(qty * price, 8)
                pnl      = proceeds - LISTING_BUY_USDT
                fees     = round((LISTING_BUY_USDT * 0.001) + (proceeds * 0.001), 8)
                net      = round(pnl - fees, 8)

                with self.pool_locks[ex_name]:
                    self.pool_usdt[ex_name]               += proceeds
                    self.coin_holdings[ex_name][symbol]    = 0.0
                    self.coin_in_position[ex_name][symbol] = False
            else:
                exchange.place_market_sell(symbol, qty)
                proceeds = round(qty * price, 8)
                pnl      = proceeds - LISTING_BUY_USDT
                fees     = round((LISTING_BUY_USDT * 0.001) + (proceeds * 0.001), 8)
                net      = round(pnl - fees, 8)
                with self.pool_locks[ex_name]:
                    self.coin_in_position[ex_name][symbol] = False

            pct = (price - buy_price) / buy_price * 100
            log.info(f"[LISTING] SOLD {coin} @ ${price:.6f} | Net: {'+'if net>=0 else ''}${net:.4f} ({pct:+.1f}%)")

            with self.daily_lock:
                self.daily_trades.append({
                    "exchange":   ex_name, "coin": coin,
                    "buy_price":  buy_price, "sell_price": price,
                    "qty":        qty, "spent": LISTING_BUY_USDT,
                    "proceeds":   proceeds, "pnl_gross": pnl,
                    "fees":       fees, "pnl_net": net,
                    "time":       datetime.now().strftime("%H:%M:%S"),
                    "type":       "new_listing",
                })

            self._tg_sold(coin, symbol, ex_name, buy_price, price, net, reason, gross=pnl, fees=fees)
            return True

        except Exception as e:
            log.error(f"[LISTING] Sell failed: {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════
    #  TELEGRAM NOTIFICATIONS
    # ══════════════════════════════════════════════════════════════════════

    def _tg(self, message: str):
        import config
        if not getattr(config, "TELEGRAM_ENABLED", False):
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID,
                      "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception:
            pass

    def _tg_scheduled(self, coin, symbol, listing_time):
        import config
        mode = "📄 PAPER" if config.PAPER_TRADING else "💰 LIVE"
        self._tg(
            f"🆕 <b>New Listing Detected!</b>\n━━━━━━━━━━━━━━━━\n"
            f"Coin:       <b>{coin}</b> ({symbol})\n"
            f"Lists at:   <b>{listing_time.strftime('%Y-%m-%d %H:%M UTC')}</b>\n"
            f"Buy amount: <b>${LISTING_BUY_USDT:.2f} USDT</b>\n"
            f"Take profit:<b>+{LISTING_TAKE_PROFIT*100:.0f}%</b>  "
            f"Stop loss: <b>-{LISTING_STOP_LOSS*100:.0f}%</b>\n"
            f"Mode:       {mode}\n"
            f"⏰ Bot will auto-buy at listing time\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _tg_reminder(self, coin, symbol, listing_time):
        self._tg(
            f"⏰ <b>Listing in 5 minutes!</b>\n━━━━━━━━━━━━━━━━\n"
            f"<b>{coin}</b> lists at "
            f"{listing_time.strftime('%H:%M UTC')}\n"
            f"Bot is ready to buy ${LISTING_BUY_USDT:.2f} USDT automatically.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _tg_bought(self, coin, symbol, ex_name, price, qty):
        import config
        mode = "📄 PAPER" if config.PAPER_TRADING else "💰 LIVE"
        self._tg(
            f"🚀 {mode} <b>New Listing Buy!</b>\n━━━━━━━━━━━━━━━━\n"
            f"Coin:     <b>{coin}</b> [{ex_name.upper()}]\n"
            f"Price:    <b>${price:,.6f}</b>\n"
            f"Qty:      <b>{qty:.6f} {coin}</b>\n"
            f"Spent:    <b>${LISTING_BUY_USDT:.2f} USDT</b>\n"
            f"Target:   +{LISTING_TAKE_PROFIT*100:.0f}% = ${price*(1+LISTING_TAKE_PROFIT):,.6f}\n"
            f"Stop:     -{LISTING_STOP_LOSS*100:.0f}% = ${price*(1-LISTING_STOP_LOSS):,.6f}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _tg_sold(self, coin, symbol, ex_name, buy_price, sell_price, net, reason, gross=None, fees=0.0):
        import config
        mode  = "📄 PAPER" if config.PAPER_TRADING else "💰 LIVE"
        sign  = "+" if net >= 0 else ""
        arrow = "📈" if net >= 0 else "📉"

        if gross is not None:
            sign_g = "+" if gross >= 0 else ""
            breakdown = (
                f"Gross P&amp;L: <b>{sign_g}${gross:.4f}</b>\n"
                f"Trade fee:  <b>-${fees:.4f}</b>\n"
                f"Net P&amp;L:   <b>{sign}${net:.4f} USDT</b>\n"
            )
        else:
            breakdown = f"Net P&amp;L: <b>{sign}${net:.4f} USDT</b>\n"

        self._tg(
            f"{arrow} {mode} <b>Listing Exit — {coin}</b>\n━━━━━━━━━━━━━━━━\n"
            f"Reason:     {reason}\n"
            f"Buy price:  <b>${buy_price:,.6f}</b>\n"
            f"Sell price: <b>${sell_price:,.6f}</b>\n"
            f"{breakdown}"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _tg_failed(self, coin, symbol, ex_name, reason):
        self._tg(
            f"⚠️ <b>Listing Buy Failed — {coin}</b>\n━━━━━━━━━━━━━━━━\n"
            f"Exchange: {ex_name.upper()}\n"
            f"Reason:   {reason}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN LOOP
    # ══════════════════════════════════════════════════════════════════════

    def run(self, stop_event: threading.Event):
        """Main loop — checks for new listings every 30 minutes."""
        log.info("[LISTING] 🎯 New listing hunter started — checking every 30 minutes")

        # How far back a listing can be and still be worth jumping on.
        # KuCoin listing pumps are usually most active in the first few
        # hours; this is a deliberately conservative window, not "all
        # listings ever" — an announcement from last week is not a fresh
        # trade opportunity, but one from this morning still might be.
        RECENT_LISTING_WINDOW_HOURS = 6

        while not stop_event.wait(timeout=30 * 60):
            try:
                announcements = self.fetch_announcements()
                for ann in announcements:
                    text = f"{ann['title']} {ann['content']}"
                    coin = self.parse_coin_symbol(text)
                    if not coin:
                        continue

                    listing_time = self.parse_listing_time(text)
                    if not listing_time:
                        continue

                    now = datetime.now(timezone.utc)

                    if listing_time > now:
                        # Future listing — schedule the buy as before
                        self.schedule_listing_buy(coin, listing_time, stop_event)
                    else:
                        # Listing already happened. If it's recent enough,
                        # treat it the same way — _listing_buy_worker already
                        # falls straight through to "attempt the buy now"
                        # when wait_secs is negative, so this reuses that
                        # exact same path instead of duplicating it.
                        hours_ago = (now - listing_time).total_seconds() / 3600
                        if hours_ago <= RECENT_LISTING_WINDOW_HOURS:
                            log.info(f"[LISTING] {coin} listed {hours_ago:.1f}h ago — "
                                    f"still within the watch window, checking now")
                            self.schedule_listing_buy(coin, listing_time, stop_event)
                        else:
                            log.debug(f"[LISTING] {coin} listed {hours_ago:.1f}h ago — "
                                     f"outside the {RECENT_LISTING_WINDOW_HOURS}h watch window, skipping")

            except Exception as e:
                log.warning(f"[LISTING] Check failed: {e}")

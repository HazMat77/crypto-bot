"""
Telegram Command Handler
=========================
Listens for commands sent TO the bot from your Telegram chat.
You can message your bot directly and it responds with live data.

Available commands:
  /status          — current pool balance, tier, open positions
  /trades          — today's completed trades with P&L
  /daily           — today's full daily report
  /monthly         — this month's full summary
  /yearly          — year-to-date summary (from the durable trade ledger)
  /coins           — which coins are currently being traded
  /on              — enable AI analyst (filters signals by confidence)
  /off             — disable AI analyst (trade on RSI/MA signals only)
  /pause           — pause all new buys (keeps monitoring)
  /resume          — resume trading after pause
  /stop            — gracefully stop the bot
  /news            — latest headlines from all 5 sources
  /score           — current news sentiment scores per coin
  /help            — list all commands

How it works:
  Polls Telegram's getUpdates endpoint every 5 seconds.
  When a command is received from YOUR chat ID only, it executes.
  All other messages are ignored for security.
"""

import logging
import threading
import requests
import time
from datetime import datetime, date
from collections import defaultdict

log = logging.getLogger(__name__)


class TelegramCommandHandler:

    def __init__(self, config, pool_usdt, pool_locks,
                 coin_in_position, coin_buy_price, coin_buy_spent,
                 coin_holdings, active_coins, active_coins_lock,
                 trade_count_ref, total_pnl_ref, total_fees_ref,
                 daily_trades, daily_lock,
                 monthly_trades, monthly_lock,
                 stop_callback=None, pause_flag=None,
                 exchanges=None, sell_callback=None, mode_ref=None,
                 coin_strategy_type=None):

        self.config             = config
        self.exchanges          = exchanges or {}
        self.pool_usdt          = pool_usdt
        self.pool_locks         = pool_locks
        self.coin_in_position   = coin_in_position
        self.coin_buy_price     = coin_buy_price
        self.coin_buy_spent     = coin_buy_spent
        self.coin_holdings      = coin_holdings
        self.coin_strategy_type = coin_strategy_type or {}  # used to flag
                                                              # orphan-recovered positions
                                                              # with estimated (not real)
                                                              # entry prices in /status
        self.active_coins       = active_coins
        self.active_coins_lock  = active_coins_lock
        self.trade_count_ref    = trade_count_ref
        self.total_pnl_ref      = total_pnl_ref
        self.total_fees_ref     = total_fees_ref
        self.daily_trades       = daily_trades
        self.daily_lock         = daily_lock
        self.monthly_trades     = monthly_trades
        self.monthly_lock       = monthly_lock
        self.stop_callback      = stop_callback
        self.pause_flag         = pause_flag   # threading.Event
        self.sell_callback      = sell_callback  # fn(ex_name, exchange_obj, symbol, price, mode)
        self.mode_ref           = mode_ref or ["paper"]

        self.last_update_id     = 0
        self.base_url           = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
        self.authorized_chat_id = str(config.TELEGRAM_CHAT_ID)
        self._pending_sell_menu = None   # set by cmd_sell_all, resolved by _handle_sell_menu_reply

    # ══════════════════════════════════════════════════════════════════════
    #  TELEGRAM POLLING
    # ══════════════════════════════════════════════════════════════════════

    def _get_updates(self):
        """Poll Telegram for new messages."""
        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={"offset": self.last_update_id + 1, "timeout": 5},
                timeout=10,
            )
            if resp.ok:
                return resp.json().get("result", [])
        except Exception as e:
            log.debug(f"[CMD] getUpdates error: {e}")
        return []

    def _send(self, text: str, parse_mode: str = "HTML"):
        """Send a message back to the authorized chat."""
        try:
            requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id":    self.authorized_chat_id,
                    "text":       text,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
        except Exception as e:
            log.warning(f"[CMD] Send failed: {e}")

    def _send_document(self, file_path: str, caption: str = ""):
        """Send a file (CSV, PNG, etc.) to the authorized chat as a
        Telegram document/photo attachment."""
        try:
            is_image = file_path.lower().endswith((".png", ".jpg", ".jpeg"))
            endpoint = "sendPhoto" if is_image else "sendDocument"
            field    = "photo" if is_image else "document"
            with open(file_path, "rb") as f:
                requests.post(
                    f"{self.base_url}/{endpoint}",
                    data={"chat_id": self.authorized_chat_id, "caption": caption},
                    files={field: f},
                    timeout=30,
                )
        except Exception as e:
            log.warning(f"[CMD] Send document failed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    #  COMMAND HANDLERS
    # ══════════════════════════════════════════════════════════════════════

    def cmd_help(self):
        self._send(
            "🤖 <b>Trading Bot Commands</b>\n━━━━━━━━━━━━━━━━\n"
            "/status    — Pool balance, tier, open positions\n"
            "/heartbeat — On-demand heartbeat (pool, P&L, open positions)\n"
            "/version   — Bot version number\n"
            "/update    — Check for and apply an available update now\n"
            "/trades    — Today's completed trades\n"
            "/daily     — Full daily P&L report (with ROI%)\n"
            "/weekly    — This week's summary (with ROI% + win rate)\n"
            "/monthly   — This month's summary (with ROI% + win rate)\n"
            "/yearly    — Year-to-date summary (from the durable trade ledger)\n"
            "/coins     — Active trading coins\n"
            "/news      — Latest crypto headlines\n"
            "/score     — News sentiment scores\n"
            "/regime    — Current market regime\n"
            "/engine    — Strategy engine stats\n"
            "/ai_stats  — Hybrid AI cost stats (fake vs real API usage)\n"
            "/hybrid    — Hybrid allocator status (spot/futures vs staking gate)\n"
            "/portfolio — Correlation heatmap + Value-at-Risk for active coins\n"
            "/optimize  — Quick backtest + parameter suggestions (BTC-USDT)\n"
            "/tax_export— Realized gains CSV (full trade history, FIFO cost basis)\n"
            "/diag      — Why aren't trades firing?\n"
            "/capacity  — Capital deployment ceiling vs actual\n"
            "/autoapply — Auto-apply settings + recent changes\n"
            "/aggressive— Switch to aggressive mode (more trades, higher targets)\n"
            "/safe      — Switch to conservative mode (fewer, safer trades)\n"
            "/on        — Enable AI analyst (vets signals before trading)\n"
            "/off       — Disable AI analyst (trade on RSI/MA signals only)\n"
            "/sell      — Sell menu: all positions, or pick one coin\n"
            "/find      — Check now for new BTC/BCH/USDT deposits (live mode)\n"
            "/pause     — Pause new buys\n"
            "/resume    — Resume trading\n"
            "/stop      — Stop the bot\n"
            "/help      — Show this message"
        )

    def cmd_status(self):
        cfg     = self.config
        mode    = "📄 PAPER" if cfg.PAPER_TRADING else "💰 LIVE"
        sign    = "+" if self.total_pnl_ref[0] >= 0 else ""
        arrow   = "📈" if self.total_pnl_ref[0] >= 0 else "📉"
        paused  = "⛔ PAUSED" if (self.pause_flag and self.pause_flag.is_set()) else "✅ ACTIVE"
        try:
            from version import __version__
            version_str = f"v{__version__}"
        except Exception:
            version_str = "unknown"

        # Pool info per exchange
        pool_lines = ""
        for ex, bal in self.pool_usdt.items():
            reserve   = getattr(cfg, "LISTING_RESERVE_USDT", 0.0)
            tradeable = max(0, bal - reserve)
            with self.active_coins_lock:
                n_coins = len(self.active_coins.get(ex, []))
            pool_lines += (f"  • {ex.upper()}: <b>${bal:.2f}</b> total "
                          f"(${tradeable:.2f} tradeable + ${reserve:.2f} reserved) "
                          f"— watching {n_coins} coins\n")

        # Open positions
        open_pos = ""
        for ex, positions in self.coin_in_position.items():
            for sym, holding in positions.items():
                if holding:
                    coin       = sym.split("-")[0]
                    buy_price  = self.coin_buy_price[ex].get(sym, 0)
                    spent      = self.coin_buy_spent[ex].get(sym, 0)
                    is_orphan  = self.coin_strategy_type.get(ex, {}).get(sym) == "orphan_recovery"
                    est_tag    = " ⚠️ <i>(estimated entry — recovered orphan, not a real buy price)</i>" if is_orphan else ""
                    open_pos += f"  • {coin} [{ex.upper()}] — bought @ ${buy_price:.6f} (${spent:.2f}){est_tag}\n"
        if not open_pos:
            open_pos = "  None\n"

        self._send(
            f"📊 <b>Bot Status</b>\n━━━━━━━━━━━━━━━━\n"
            f"Mode:     {mode}\n"
            f"Version:  {version_str}\n"
            f"Status:   {paused}\n\n"
            f"<b>Pool Balances:</b>\n{pool_lines}\n"
            f"<b>Trades today:</b>  {self.trade_count_ref[0]}\n"
            f"{arrow} <b>Total P&L:</b>   {sign}${self.total_pnl_ref[0]:.4f} USDT\n"
            f"💸 <b>Total fees:</b>  ${self.total_fees_ref[0]:.4f} USDT\n\n"
            f"<b>Open positions:</b>\n{open_pos}"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def cmd_trades(self):
        with self.daily_lock:
            trades = list(self.daily_trades)

        if not trades:
            self._send("📋 <b>Today's Trades</b>\n━━━━━━━━━━━━━━━━\nNo completed trades today yet.")
            return

        lines = ""
        for t in trades[-10:]:   # last 10
            sign   = "+" if t.get("pnl_net", 0) >= 0 else ""
            sign_g = "+" if t.get("pnl_gross", 0) >= 0 else ""
            arrow  = "📈" if t.get("pnl_net", 0) >= 0 else "📉"
            lines += (f"{arrow} <b>{t['coin']}</b> [{t['exchange'].upper()}] "
                     f"@ {t.get('time','?')}\n"
                     f"   Buy ${t['buy_price']:.4f} → Sell ${t['sell_price']:.4f}\n"
                     f"   Gross: {sign_g}${t.get('pnl_gross',0):.4f}  "
                     f"Fee: -${t.get('fees',0):.4f}  "
                     f"Net: <b>{sign}${t.get('pnl_net',0):.4f}</b>\n\n")

        gross = sum(t.get("pnl_gross", 0) for t in trades)
        fees  = sum(t.get("fees", 0)      for t in trades)
        net   = gross - fees
        sign  = "+" if net >= 0 else ""

        self._send(
            f"📋 <b>Today's Trades ({len(trades)} total)</b>\n━━━━━━━━━━━━━━━━\n"
            f"{lines}"
            f"━━━━━━━━━━━━━━━━\n"
            f"Gross: {'+' if gross>=0 else ''}${gross:.4f}\n"
            f"Fees:  -${fees:.4f}\n"
            f"<b>Net: {sign}${net:.4f} USDT</b>\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def cmd_daily(self):
        with self.daily_lock:
            trades = list(self.daily_trades)

        today = date.today().strftime("%Y-%m-%d")
        mode  = "📄 PAPER" if self.config.PAPER_TRADING else "💰 LIVE"

        if not trades:
            self._send(
                f"🌙 <b>Daily Report — {today}</b>\n━━━━━━━━━━━━━━━━\n"
                f"Mode: {mode}\nNo trades completed today.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return

        num   = len(trades)
        wins  = [t for t in trades if t.get("pnl_gross", 0) >= 0]
        gross = sum(t.get("pnl_gross", 0) for t in trades)
        fees  = sum(t.get("fees", 0)      for t in trades)
        net   = gross - fees
        wr    = len(wins) / num * 100

        # By coin
        coin_stats = {}
        for t in trades:
            k = f"{t['coin']} [{t['exchange'].upper()}]"
            coin_stats.setdefault(k, {"n": 0, "gross": 0.0, "fees": 0.0, "net": 0.0})
            coin_stats[k]["n"]     += 1
            coin_stats[k]["gross"] += t.get("pnl_gross", 0)
            coin_stats[k]["fees"]  += t.get("fees", 0)
            coin_stats[k]["net"]   += t.get("pnl_net", 0)

        coin_lines = ""
        for k, s in sorted(coin_stats.items(), key=lambda x: x[1]["net"], reverse=True):
            sg = "+" if s["gross"] >= 0 else ""
            sn = "+" if s["net"]   >= 0 else ""
            coin_lines += (f"  • {k} ({s['n']} trade{'s' if s['n']!=1 else ''})\n"
                          f"      Gross {sg}${s['gross']:.4f}  Fee -${s['fees']:.4f}  "
                          f"Net <b>{sn}${s['net']:.4f}</b>\n")

        sign     = "+" if net >= 0 else ""
        arrow    = "📈" if net >= 0 else "📉"
        starting = getattr(self.config, "PAPER_STARTING_USDT", 100.0)
        roi      = (net / starting * 100) if starting > 0 else 0
        sign_roi = "+" if roi >= 0 else ""

        self._send(
            f"🌙 <b>Daily Report — {today}</b>\n━━━━━━━━━━━━━━━━\n"
            f"Mode: {mode}\n\n"
            f"📊 {num} trades  ✅ {len(wins)} wins  ❌ {num-len(wins)} losses  🎯 {wr:.0f}% win rate\n\n"
            f"💵 Gross:  {'+' if gross>=0 else ''}${gross:.4f}\n"
            f"💸 Fees:   -${fees:.4f}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{arrow} <b>Net: {sign}${net:.4f} USDT</b>\n"
            f"📊 <b>ROI: {sign_roi}{roi:.2f}% on ${starting:.0f} starting pool</b>\n\n"
            f"📌 <b>By coin:</b>\n{coin_lines}"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def cmd_monthly(self):
        with self.monthly_lock:
            trades = list(self.monthly_trades)

        month = datetime.now().strftime("%B %Y")
        mode  = "📄 PAPER" if self.config.PAPER_TRADING else "💰 LIVE"

        if not trades:
            self._send(
                f"📅 <b>Monthly Report — {month}</b>\n━━━━━━━━━━━━━━━━\n"
                f"Mode: {mode}\nNo trades this month yet.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return

        num   = len(trades)
        wins  = [t for t in trades if t.get("pnl_gross", 0) >= 0]
        gross = sum(t.get("pnl_gross", 0) for t in trades)
        fees  = sum(t.get("fees", 0)      for t in trades)
        net   = gross - fees
        wr    = len(wins) / num * 100

        # Best and worst trade
        best  = max(trades, key=lambda t: t.get("pnl_net", 0))
        worst = min(trades, key=lambda t: t.get("pnl_net", 0))

        # By coin
        coin_stats = {}
        for t in trades:
            k = t["coin"]
            coin_stats.setdefault(k, {"n": 0, "gross": 0.0, "fees": 0.0, "net": 0.0})
            coin_stats[k]["n"]     += 1
            coin_stats[k]["gross"] += t.get("pnl_gross", 0)
            coin_stats[k]["fees"]  += t.get("fees", 0)
            coin_stats[k]["net"]   += t.get("pnl_net", 0)

        top_coins = sorted(coin_stats.items(), key=lambda x: x[1]["net"], reverse=True)[:5]
        coin_lines = ""
        for k, s in top_coins:
            sg = "+" if s["gross"] >= 0 else ""
            sn = "+" if s["net"]   >= 0 else ""
            coin_lines += (f"  • {k} ({s['n']} trades)\n"
                          f"      Gross {sg}${s['gross']:.4f}  Fee -${s['fees']:.4f}  "
                          f"Net <b>{sn}${s['net']:.4f}</b>\n")

        sign     = "+" if net >= 0 else ""
        arrow    = "📈" if net >= 0 else "📉"
        starting = getattr(self.config, "PAPER_STARTING_USDT", 100.0)
        roi      = (net / starting * 100) if starting > 0 else 0
        sign_roi = "+" if roi >= 0 else ""

        # Pool balances
        pool_lines = "\n".join([f"  • {e.upper()}: ${b:.2f}" for e, b in self.pool_usdt.items()])

        self._send(
            f"📅 <b>Monthly Report — {month}</b>\n━━━━━━━━━━━━━━━━\n"
            f"Mode: {mode}\n\n"
            f"📊 <b>Totals:</b>\n"
            f"  Trades:   {num}\n"
            f"  Win rate: {wr:.0f}% ({len(wins)} wins / {num-len(wins)} losses)\n\n"
            f"💵 Gross P&L:  {'+' if gross>=0 else ''}${gross:.4f}\n"
            f"💸 Total fees: -${fees:.4f}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{arrow} <b>Net profit: {sign}${net:.4f} USDT</b>\n"
            f"📊 <b>ROI: {sign_roi}{roi:.2f}% on ${starting:.0f} starting pool</b>\n\n"
            f"🏆 Best trade:  {best['coin']} net +${best.get('pnl_net',0):.4f} "
            f"(gross +${best.get('pnl_gross',0):.4f}, fee -${best.get('fees',0):.4f})\n"
            f"💔 Worst trade: {worst['coin']} net ${worst.get('pnl_net',0):.4f} "
            f"(gross ${worst.get('pnl_gross',0):.4f}, fee -${worst.get('fees',0):.4f})\n\n"
            f"📌 <b>Top 5 coins:</b>\n{coin_lines}"
            f"🏦 <b>Pool balances:</b>\n{pool_lines}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def cmd_yearly(self):
        try:
            from reports import build_report, format_report_text
            starting = getattr(self.config, "PAPER_STARTING_USDT", 100.0)
            mode     = "📄 PAPER" if self.config.PAPER_TRADING else "💰 LIVE"
            report   = build_report("yearly", starting_pool=starting)
            self._send(format_report_text(report, mode_label=mode) +
                      f"\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            self._send(f"⚠️ Could not build yearly report: {e}")

    def cmd_coins(self):
        lines = ""
        with self.active_coins_lock:
            for ex, coins in self.active_coins.items():
                lines += f"\n<b>{ex.upper()}</b> ({len(coins)} coins):\n"
                for i, sym in enumerate(coins, 1):
                    holding = self.coin_in_position[ex].get(sym, False)
                    status  = "🟢 HOLDING" if holding else "⏸ watching"
                    lines  += f"  {i:>2}. {sym:<15} {status}\n"

                # Orphaned positions — held but no longer in the active
                # ranking (e.g. dropped by an hourly news re-rank). These
                # are NOT shown above since they're not in `coins`, but
                # they're still real open positions still being monitored
                # for exit. Surfacing them explicitly is the whole point —
                # silently omitting them is what made a real stop-loss
                # miss invisible until checked manually.
                orphaned = [
                    sym for sym, held in self.coin_in_position.get(ex, {}).items()
                    if held and sym not in coins
                ]
                if orphaned:
                    lines += f"\n  ⚠️ <b>Held but dropped from active ranking</b> "
                    lines += f"(still being monitored for exit):\n"
                    for sym in orphaned:
                        buy_price = self.coin_buy_price.get(ex, {}).get(sym, 0)
                        lines += f"      {sym:<15} bought @ ${buy_price:.6f}\n"

        self._send(
            f"🪙 <b>Active Trading Coins</b>\n━━━━━━━━━━━━━━━━"
            f"{lines}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def cmd_news(self):
        try:
            from news_aggregator import fetch_market_news
            self._send("📰 Fetching latest news from 5 sources...")
            headlines = fetch_market_news()
            # Trim to fit Telegram's 4096 char limit
            if len(headlines) > 3500:
                headlines = headlines[:3500] + "\n...(truncated)"
            self._send(
                f"📰 <b>Latest Crypto News</b>\n━━━━━━━━━━━━━━━━\n"
                f"{headlines}\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            self._send(f"⚠️ Could not fetch news: {e}")

    def cmd_score(self):
        try:
            from coin_discovery import score_coins_by_news
            with self.active_coins_lock:
                all_symbols = list({s for coins in self.active_coins.values() for s in coins})

            self._send("🔍 Scoring coins by news sentiment...")
            scores = score_coins_by_news(all_symbols)

            if not scores:
                self._send("No news scores available right now.")
                return

            sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            lines = ""
            for coin, score in sorted_scores:
                bar   = "▓" * int(abs(score))
                emoji = "📈" if score > 1 else "📉" if score < -1 else "➡️"
                sign  = "+" if score >= 0 else ""
                lines += f"  {emoji} <b>{coin:<8}</b> {sign}{score:.1f}  {bar}\n"

            self._send(
                f"📊 <b>News Sentiment Scores</b>\n━━━━━━━━━━━━━━━━\n"
                f"Scale: -5 (very bearish) to +5 (very bullish)\n\n"
                f"{lines}\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            self._send(f"⚠️ Score error: {e}")

    def cmd_regime(self):
        try:
            from adaptive_intelligence import get_intelligence, REGIMES, REGIME_STRATEGIES
            from breakout_strategy import get_active_strategy
            intel = get_intelligence()
            if not intel:
                self._send("⏳ Adaptive intelligence not yet started. "
                          "Send /regime again in 30 seconds.")
                return

            info     = intel.regime_info
            regime   = info["regime"]
            strat    = REGIME_STRATEGIES.get(regime, {})
            learn_s  = intel.learning_summary()
            r_info   = learn_s.get(regime, {})
            sig_type = get_active_strategy(regime)

            history_lines = ""
            for h in list(intel.detector.regime_history)[-5:]:
                history_lines += (f"  {h['time'][11:16]}  "
                                 f"{REGIMES.get(h['regime'],{}).get('emoji','?')} "
                                 f"{h['regime']} ({h['confidence']}%)\n")

            if sig_type == "breakout":
                strategy_block = (
                    f"⚙️ <b>Active Strategy: 📈 BREAKOUT/MOMENTUM</b>\n"
                    f"  (different logic than mean-reversion — trades WITH\n"
                    f"  the trend instead of against it)\n"
                    f"  Entry:    Donchian 20-candle breakout + volume confirm\n"
                    f"  Exit:     Donchian breakdown OR ATR trailing stop\n"
                    f"  Take Profit: {strat.get('take_profit_pct',0)*100:.0f}%\n"
                    f"  Aggr Pool:   {strat.get('aggressive_pct',0.2)*100:.0f}%\n\n"
                )
            else:
                strategy_block = (
                    f"⚙️ <b>Active Strategy: Mean-Reversion (RSI+MA)</b>\n"
                    f"  RSI Buy/Sell: {strat.get('rsi_buy','?')}/{strat.get('rsi_sell','?')}\n"
                    f"  Stop Loss:    {strat.get('stop_loss_pct',0)*100:.0f}%\n"
                    f"  Take Profit:  {strat.get('take_profit_pct',0)*100:.0f}%\n"
                    f"  Max Hold:     {strat.get('max_hold_hours','?')}h\n"
                    f"  Aggr Pool:    {strat.get('aggressive_pct',0.2)*100:.0f}%\n\n"
                )

            self._send(
                f"{info['emoji']} <b>Market Regime: {regime}</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Confidence:   <b>{info['confidence']}%</b>\n"
                f"Description:  {info['description']}\n\n"
                f"{strategy_block}"
                f"🧠 <b>Learning Status:</b>\n"
                f"  Trades in regime: {r_info.get('trades',0)}\n"
                f"  Win rate:         {r_info.get('win_rate',0):.1f}%\n"
                f"  Using learned:    {'✅ Yes' if r_info.get('learned') else '⏳ Gathering data'}\n\n"
                f"📜 <b>Recent History:</b>\n{history_lines}"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            self._send(f"⚠️ Regime data unavailable: {e}")

    def cmd_engine(self):
        try:
            from strategy_engine import _trackers
            from adaptive_intelligence import get_intelligence

            if not _trackers:
                self._send("⏳ Strategy engine has no trade data yet. "
                          "Check back after the first few trades.")
                return

            lines    = ""
            total_t  = 0
            for key, tracker in sorted(_trackers.items()):
                s      = tracker.summary()
                total_t += s["trades"]
                ev_col = "📈" if s["expectancy"] >= 0 else "📉"
                lines  += (f"  <b>{key}</b>\n"
                          f"    WR: {s['win_rate']:.0%}  "
                          f"EV: {ev_col}${s['expectancy']:.4f}  "
                          f"PF: {s['profit_factor']:.2f}  "
                          f"({s['trades']} trades)\n")

            intel    = get_intelligence()
            learn_s  = intel.learning_summary() if intel else {}
            learn_lines = ""
            for regime, data in learn_s.items():
                status = "✅ Learned" if data.get("learned") else f"⏳ {data['trades']}/20"
                learn_lines += f"  {regime}: {status}  WR={data['win_rate']}%\n"

            self._send(
                f"⚡ <b>Adaptive Strategy Engine</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Total trades tracked: <b>{total_t}</b>\n\n"
                f"<b>Per-coin performance:</b>\n{lines or '  No data yet'}\n"
                f"<b>Regime learning:</b>\n{learn_lines or '  No data yet'}\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            self._send(f"⚠️ Engine data unavailable: {e}")

    def cmd_ai_stats(self):
        """Hybrid AI cost stats — how many signals used free fake AI vs a
        paid real API call, and the current hybrid/staking configuration."""
        try:
            from ai_analyst import get_hybrid_stats
            cfg = self.config
            stats = get_hybrid_stats()

            if cfg.PAPER_TRADING:
                mode_line = "📄 PAPER — always uses fake AI (hybrid mode has no effect here)"
            elif getattr(cfg, "LIVE_FAKE_AI_ONLY", False):
                mode_line = "💰 LIVE — LIVE_FAKE_AI_ONLY forces fake AI for every signal"
            elif getattr(cfg, "AI_HYBRID_MODE", False):
                mode_line = (f"💰 LIVE — HYBRID (real usage rate {cfg.AI_REAL_USAGE_RATE:.0%}, "
                            f"escalate at {cfg.AI_MIN_CONFIDENCE_FOR_REAL}%+ confidence)")
            else:
                mode_line = "💰 LIVE — always real AI (hybrid mode disabled)"

            self._send(
                f"🤖 <b>Hybrid AI Stats</b>\n━━━━━━━━━━━━━━━━\n"
                f"Mode: {mode_line}\n\n"
                f"Fake AI calls: <b>{stats['fake']}</b>\n"
                f"Real AI calls: <b>{stats['real']}</b>\n"
                f"Real API usage: <b>{stats['real_pct']}%</b> of {stats['total']} total signals\n\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            self._send(f"⚠️ AI stats unavailable: {e}")

    def cmd_hybrid(self):
        """Hybrid allocator status — whether spot/futures entries are
        currently being gated against this exchange's staking yield."""
        try:
            cfg = self.config
            enabled  = getattr(cfg, "HYBRID_OPTIMIZER_ENABLED", False)
            staking  = getattr(cfg, "STAKING_ENABLED", False)
            futures  = getattr(cfg, "FUTURES_ENABLED", False)
            active   = enabled and staking

            lines = ""
            for ex_name in self.pool_usdt.keys():
                ex_cfg = cfg.EXCHANGES.get(ex_name, {})
                stake_on = staking and ex_cfg.get("staking_enabled", False)
                fut_on   = futures and ex_cfg.get("futures_enabled", False)
                lines += (f"  • {ex_name.upper()}: staking={'✅' if stake_on else '❌'} "
                         f" futures={'✅' if fut_on else '❌'}\n")

            self._send(
                f"⚖️ <b>Hybrid Allocator</b>\n━━━━━━━━━━━━━━━━\n"
                f"Gate active: <b>{'✅ YES — trades must beat staking yield' if active else '❌ no (staking not enabled, gate is a no-op)'}</b>\n"
                f"Min edge over staking: <b>{getattr(cfg, 'HYBRID_MIN_EDGE_OVER_STAKING', 0.0):.2%}</b>\n\n"
                f"<b>Per-exchange:</b>\n{lines or '  No exchanges configured'}\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            self._send(f"⚠️ Hybrid status unavailable: {e}")

    def cmd_portfolio(self):
        """Correlation heatmap + Value-at-Risk for the currently active
        coin list — runs in the background since it fetches price history
        per coin and can take a little while."""
        self._send("📊 Building portfolio correlation + risk report — this fetches "
                  "price history per coin, give it a moment...")
        threading.Thread(target=self._cmd_portfolio_worker, daemon=True).start()

    def _cmd_portfolio_worker(self):
        try:
            from portfolio_correlation import CorrelationChecker
            from portfolio_risk import PortfolioRiskAnalyzer

            with self.active_coins_lock:
                symbols = []
                for coins in self.active_coins.values():
                    for s in coins:
                        if s not in symbols:
                            symbols.append(s)
            symbols = symbols[:15]   # cap — each coin is a separate price-history fetch

            if len(symbols) < 2:
                self._send("⚠️ Need at least 2 active coins to check correlation.")
                return

            checker = CorrelationChecker(lookback_days=30)
            checker.check(symbols)
            if checker.corr_matrix is None or checker.corr_matrix.empty:
                self._send("⚠️ Could not fetch enough price history for a correlation report.")
                return

            coins = list(checker.corr_matrix.columns)
            n     = len(coins)
            pairs = [(i, j) for i in range(n) for j in range(n) if i < j]
            avg_corr = sum(abs(checker.corr_matrix.iloc[i, j]) for i, j in pairs) / len(pairs) if pairs else 0
            verdict  = ("✅ Well diversified" if avg_corr < 0.5
                       else "⚠️ Moderately correlated" if avg_corr < 0.75
                       else "❌ Highly correlated — add uncorrelated coins")

            high_pairs = sorted(
                ((f"{coins[i]}/{coins[j]}", checker.corr_matrix.iloc[i, j]) for i, j in pairs
                 if checker.corr_matrix.iloc[i, j] >= 0.80),
                key=lambda x: -x[1]
            )[:6]
            high_lines = "\n".join(f"    {p} = {v:.2f}" for p, v in high_pairs) or "    (none)"

            msg = (
                f"📊 <b>Portfolio Correlation Report</b>\n━━━━━━━━━━━━━━━━\n"
                f"Coins checked: <b>{n}</b>  |  Avg correlation: <b>{avg_corr:.2f}</b>\n"
                f"{verdict}\n\n"
                f"⚠️ High correlation pairs (≥0.80):\n{high_lines}\n"
            )

            # Value at Risk — equal-weighted across the checked coins using
            # total pool value as a rough stand-in for portfolio size (this
            # bot doesn't track live per-coin USD exposure outside an open
            # position, so this is a "if fully deployed evenly" estimate,
            # not a snapshot of actual current holdings).
            try:
                total_pool = sum(self.pool_usdt.values())
                weights    = {c: 1.0 for c in coins}
                risk       = PortfolioRiskAnalyzer(checker)
                var        = risk.value_at_risk(weights, total_pool, 0.95, "historical")
                msg += (f"\n💰 <b>Est. 1-day VaR (95%, equal-weighted)</b>\n"
                       f"  ${var['var_usdt']:.2f} ({var['var_pct']:.1f}% of ${total_pool:.2f} pool)\n")
            except Exception as e:
                log.debug(f"[CMD] Portfolio VaR skipped: {e}")

            msg += f"\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            self._send(msg)

            # Best-effort heatmap image — matplotlib is an optional dep
            # (see requirements.txt); silently skip the image if missing.
            try:
                checker.plot()
                import os
                if os.path.exists("correlation_matrix.png"):
                    self._send_document("correlation_matrix.png", caption="Correlation heatmap")
            except Exception as e:
                log.debug(f"[CMD] Correlation heatmap image skipped: {e}")

        except Exception as e:
            self._send(f"⚠️ Portfolio report failed: {e}")

    def cmd_optimize(self):
        """Runs a quick backtest + grid search on BTC-USDT and suggests
        settings — runs in the background, can take a minute or two."""
        self._send("🔍 Running a quick backtest + parameter search — this can take "
                  "a minute or two, I'll message you when it's done...")
        threading.Thread(target=self._cmd_optimize_worker, daemon=True).start()

    def _cmd_optimize_worker(self):
        try:
            from strategy_optimizer import run_quick_backtest
            result = run_quick_backtest(symbol="BTC-USDT", days=60)

            if "error" in result:
                self._send(f"⚠️ Optimize failed: {result['error']}")
                return

            cur = result["current_backtest"]
            cs  = result["current_settings"]
            msg = (
                f"🔍 <b>Quick Optimize — BTC-USDT, 60d</b>\n━━━━━━━━━━━━━━━━\n"
                f"<b>Current settings:</b> RSI {cs['rsi_buy']}/{cs['rsi_sell']} "
                f"MA={cs['ma_period']} SL={cs['stop_loss_pct']*100:.0f}% TP={cs['take_profit_pct']*100:.0f}%\n"
                f"  → WR={cur['win_rate']}% ROI={cur['roi_pct']:+.1f}% "
                f"DD={cur['max_drawdown']:.1f}% ({cur['total_trades']} trades)\n\n"
            )

            if result.get("suggested_settings"):
                sp = result["suggested_settings"]
                sb = result["suggested_backtest"]
                msg += (
                    f"<b>Suggested settings (walk-forward tested):</b>\n"
                    f"  RSI {sp['rsi_buy']}/{sp['rsi_sell']} MA={sp['ma_period']} "
                    f"SL={sp['stop_loss_pct']*100:.0f}% TP={sp['take_profit_pct']*100:.0f}%\n"
                    f"  → WR={sb['win_rate']}% ROI={sb['roi_pct']:+.1f}% DD={sb['max_drawdown']:.1f}%\n\n"
                    f"This is a suggestion from historical data, not an automatic change — "
                    f"nothing in config.py has been modified. Apply manually if it looks right."
                )
            else:
                msg += "No better parameter set found in this search — current settings look reasonable."

            msg += f"\n\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            self._send(msg)

        except Exception as e:
            self._send(f"⚠️ Optimize failed: {e}")

    def cmd_tax_export(self):
        """Exports the full realized-gains history (from the durable
        trade ledger, not just today/this month) as a CSV and sends it."""
        try:
            from tax_export import export_tax_csv
            import os

            os.makedirs("logs", exist_ok=True)
            path = os.path.join("logs", f"tax_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
            summary = export_tax_csv(path)

            if summary["rows"] == 0:
                self._send("📄 No completed trades in the ledger yet — nothing to export.\n"
                          "(The ledger only started recording once this update was installed — "
                          "trades from before that aren't in it.)")
                return

            self._send(
                f"📄 <b>Tax Export</b>\n━━━━━━━━━━━━━━━━\n"
                f"{summary['rows']} closed trades exported.\n"
                f"Total realized gain/loss: <b>${summary['total_gain_loss']:.2f}</b>\n"
                f"  Short-term: ${summary['short_term_total']:.2f}\n"
                f"  Long-term:  ${summary['long_term_total']:.2f}\n"
                + (f"  Unclassified (missing dates): ${summary['unclassified_total']:.2f}\n"
                   if summary["unclassified_total"] else "") +
                f"\n⚠️ Not tax advice — verify against your jurisdiction's rules "
                f"and a tax professional before filing.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self._send_document(path, caption="Realized gains — full trade history")
        except Exception as e:
            self._send(f"⚠️ Tax export failed: {e}")

    def cmd_capacity(self):
        """Shows max possible capital deployment vs current pool size."""
        try:
            from coin_discovery import get_tier
            cfg = self.config
            lines = ["💼 <b>Capital Deployment Capacity</b>", "━━━━━━━━━━━━━━━━"]

            floor_pct     = getattr(cfg, "CAPITAL_FLOOR_PCT", 0.0)
            starting_pool = getattr(cfg, "PAPER_STARTING_USDT", 100.0)
            capital_floor = starting_pool * floor_pct
            listing_res   = getattr(cfg, "LISTING_RESERVE_USDT", 0.0)

            if floor_pct > 0:
                lines.append(f"🔒 Capital floor: ${capital_floor:.2f} "
                           f"({floor_pct*100:.0f}% of starting capital — NEVER traded)")

            for ex, pool in self.pool_usdt.items():
                tier = get_tier(pool, cfg.SCALING_TIERS)
                tradeable_for_tier = max(0.0, pool - listing_res - capital_floor)
                ceiling = min(tier["max_per_trade"] * tier["max_coins"], tradeable_for_tier)
                pct     = min(100, ceiling / pool * 100) if pool > 0 else 0

                open_count = sum(1 for h in self.coin_in_position.get(ex, {}).values() if h)
                deployed   = sum(
                    self.coin_buy_spent[ex].get(s, 0)
                    for s, h in self.coin_in_position.get(ex, {}).items() if h
                )
                actual_pct = deployed / pool * 100 if pool > 0 else 0

                lines.append(f"\n<b>{ex.upper()}</b> — {tier['label']} tier")
                lines.append(f"  Pool:            ${pool:.2f}")
                if floor_pct > 0 or listing_res > 0:
                    lines.append(f"  Reserved:        ${capital_floor + listing_res:.2f} "
                               f"(${capital_floor:.2f} floor + ${listing_res:.2f} listing)")
                lines.append(f"  Max per trade:   ${tier['max_per_trade']:.0f}")
                lines.append(f"  Max coins:       {tier['max_coins']}")
                lines.append(f"  Theoretical ceiling: ${ceiling:.0f} ({pct:.0f}% of pool)")
                lines.append(f"  Currently deployed:  ${deployed:.2f} ({actual_pct:.0f}% of pool, {open_count} open)")

            self._send("\n".join(lines))
        except Exception as e:
            self._send(f"⚠️ Capacity check error: {e}")


        """Diagnostic command — shows exactly why trades are/aren't firing."""
        try:
            from strategy_engine import _trackers
            cfg = self.config

            lines = [
                f"🔧 <b>Diagnostics</b>",
                f"━━━━━━━━━━━━━━━━",
                f"RSI_BUY / RSI_SELL:     {cfg.RSI_BUY} / {cfg.RSI_SELL}",
                f"ENGINE_CONFIDENCE_MIN:  {getattr(cfg,'ENGINE_CONFIDENCE_MIN',55)}%",
                f"AI_CONFIDENCE_MIN:      {getattr(cfg,'AI_CONFIDENCE_MIN',70)}%",
                f"AI_ENABLED:             {cfg.AI_ENABLED}",
                f"PAPER_TRADING:          {cfg.PAPER_TRADING}",
                f"Paused (manual):        {self.pause_flag.is_set() if self.pause_flag else 'N/A'}",
                "",
                f"<b>Coins tracked by engine:</b> {len(_trackers)}",
            ]
            if not _trackers:
                lines.append("  No signals have reached the engine yet.")
                lines.append("  This usually means RSI/MA hasn't triggered, or")
                lines.append("  candle data is still warming up (need 40+ candles).")
            self._send("\n".join(lines))
        except Exception as e:
            self._send(f"⚠️ Diagnostics error: {e}")

    def cmd_aggressive(self):
        """
        Switch to 50/50 split mode: half the active coins trade SAFE
        (18% TP / 4% SL), half trade AGGRESSIVE (25% TP / 10% SL).
        This is a PERMANENT switch — stays active until /safe is sent.
        """
        try:
            updates = {
                "DUAL_POOL_ENABLED":    True,
                "AGGRESSIVE_POOL_PCT":  0.50,   # /aggressive = 50/50 split
                # Safe half keeps its own settings — untouched, still 18%/4%
                "NORMAL_RSI_BUY":       35,
                "NORMAL_RSI_SELL":      65,
                "NORMAL_STOP_LOSS":     0.04,
                "NORMAL_TAKE_PROFIT":   0.18,
                # Aggressive half — wider RSI band, full ceiling
                "AGGRESSIVE_RSI_BUY":   42,
                "AGGRESSIVE_RSI_SELL":  58,
                "AGGRESSIVE_STOP_LOSS": 0.10,
                "AGGRESSIVE_TAKE_PROFIT":0.25,
                "ENGINE_CONFIDENCE_MIN":50,
            }
            self._write_config_updates(updates)
            self._send(
                "🔥 <b>Aggressive Mode Activated — 50/50 Split</b>\n━━━━━━━━━━━━━━━━\n"
                "Active coins are now split:\n\n"
                "🛡️ <b>Half the coins — SAFE</b>\n"
                "  RSI Buy/Sell:  35 / 65\n"
                "  Take Profit:   18%\n"
                "  Stop Loss:     4%\n\n"
                "🔥 <b>Half the coins — AGGRESSIVE</b>\n"
                "  RSI Buy/Sell:  42 / 58 (wider — more signals)\n"
                "  Take Profit:   25%\n"
                "  Stop Loss:     10%\n\n"
                "Note: during a strong trending regime (BULL_STRONG /\n"
                "BEAR_STRONG), coins switch to breakout logic instead of\n"
                "RSI — aggressive coins there get a shorter, more reactive\n"
                "breakout window instead of a wider RSI band, but the same\n"
                "\"fires more often\" intent applies. Check /regime to see\n"
                "which logic is currently active.\n\n"
                "This is a <b>permanent</b> change — the split stays active\n"
                "until you send /safe to revert to 100% safe mode.\n\n"
                "✅ <b>Live now</b> — no restart needed, every coin thread\n"
                "is reading these new settings on its next poll cycle.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            log.info("[CMD] Switched to AGGRESSIVE 50/50 split mode via Telegram")
        except Exception as e:
            self._send(f"⚠️ Could not switch mode: {e}")

    def cmd_safe(self):
        """
        Revert to 100% SAFE mode — no split, every coin trades with
        18% take-profit / 4% stop-loss. Undoes /aggressive.
        """
        try:
            updates = {
                "DUAL_POOL_ENABLED":    False,   # turns off the split entirely
                "RSI_BUY":              35,
                "RSI_SELL":             65,
                "STOP_LOSS_PCT":        0.04,
                "TAKE_PROFIT_PCT":      0.18,
                "NORMAL_RSI_BUY":       35,
                "NORMAL_RSI_SELL":      65,
                "NORMAL_STOP_LOSS":     0.04,
                "NORMAL_TAKE_PROFIT":   0.18,
                "ENGINE_CONFIDENCE_MIN":55,
            }
            self._write_config_updates(updates)
            self._send(
                "🛡️ <b>Safe Mode Activated — 100%</b>\n━━━━━━━━━━━━━━━━\n"
                "Aggressive split disabled. Every coin now trades:\n"
                "  RSI Buy/Sell:  35 / 65\n"
                "  Take Profit:   18%\n"
                "  Stop Loss:     4%\n\n"
                "✅ <b>Live now</b> — no restart needed.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            log.info("[CMD] Switched to 100% SAFE mode via Telegram")
        except Exception as e:
            self._send(f"⚠️ Could not switch mode: {e}")

    def cmd_heartbeat(self):
        """
        On-demand version of the scheduled 30-minute heartbeat — shows pool
        balances, tier, trade count, P&L, and live unrealized P&L on open
        positions right now, instead of waiting for the next scheduled one.

        Works regardless of HEARTBEAT_VISIBLE_BY_DEFAULT (see config.py) —
        that flag only controls whether the automatic 30-min heartbeat is
        sent unprompted; /heartbeat always responds when you ask for it.
        """
        cfg  = self.config
        mode = self.mode_ref[0] if self.mode_ref else "paper"
        tag  = "📄 PAPER" if mode == "paper" else "💰 LIVE"

        # Live unrealized P&L on each open position, same calc as bot.py's
        # heartbeat_worker — fetches current price per held coin.
        open_pos_lines = ""
        for ex_name, positions in self.coin_in_position.items():
            exchange_obj = self.exchanges.get(ex_name)
            for sym, holding in positions.items():
                if not holding:
                    continue
                coin = sym.split("-")[0]
                try:
                    cur_price = exchange_obj.get_price(sym) if exchange_obj else 0
                    spent     = self.coin_buy_spent[ex_name].get(sym, 0)
                    qty       = self.coin_holdings[ex_name].get(sym, 0)
                    upnl      = round(qty * cur_price - spent, 4)
                    sign      = "+" if upnl >= 0 else ""
                    open_pos_lines += f"  • {coin} [{ex_name.upper()}]: {sign}${upnl:.3f}\n"
                except Exception as e:
                    open_pos_lines += f"  • {coin} [{ex_name.upper()}]: ⚠️ price check failed ({e})\n"
        if not open_pos_lines:
            open_pos_lines = "  None open\n"

        pool_lines = ""
        for ex, bal in self.pool_usdt.items():
            with self.active_coins_lock:
                n_coins = len(self.active_coins.get(ex, []))
            pool_lines += f"  • {ex.upper()}: ${bal:.2f} ({n_coins} coins watched)\n"

        sign  = "+" if self.total_pnl_ref[0] >= 0 else ""
        arrow = "📈" if self.total_pnl_ref[0] >= 0 else "📉"

        self._send(
            f"💓 <b>Heartbeat — {tag}</b> <i>(on demand)</i>\n━━━━━━━━━━━━━━━━\n"
            f"Pools:\n{pool_lines}\n"
            f"📊 Trades: <b>{self.trade_count_ref[0]}</b>\n"
            f"{arrow} P&amp;L: <b>{sign}${self.total_pnl_ref[0]:.4f} USDT</b>\n"
            f"📌 Open positions:\n{open_pos_lines}"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        log.info("[CMD] /heartbeat sent on demand")

    def cmd_version(self):
        try:
            from version import __version__, RELEASE_DATE
        except Exception:
            self._send("⚠️ version.py not found.")
            return
        mode = self.mode_ref[0] if self.mode_ref else "paper"
        tag  = "📄 PAPER" if mode == "paper" else "💰 LIVE"
        self._send(
            f"🏷️ <b>Bot Version</b>\n━━━━━━━━━━━━━━━━\n"
            f"Version: <b>v{__version__}</b>\n"
            f"Released: {RELEASE_DATE}\n"
            f"Mode: {tag}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def cmd_update(self):
        """
        Manual trigger for applying an update — the deliberate-action
        counterpart to AUTO_UPDATE_MODE = "notify_only". Checks right now
        (doesn't rely on any cached state from the background checker),
        and if an update is genuinely available, pulls it and exits so the
        watchdog relaunches the bot on the new code. If there's nothing
        new, or anything blocks the pull (e.g. uncommitted local edits),
        says so plainly and changes nothing.
        """
        try:
            import auto_updater
        except Exception as e:
            self._send(f"⚠️ Auto-updater module not available: {e}")
            return

        self._send("🔍 Checking for updates now...")
        result = auto_updater.check_for_update(self.config)

        if not result["update_available"]:
            self._send(
                f"✅ <b>Already Up To Date</b>\n━━━━━━━━━━━━━━━━\n"
                f"{result['reason'] or 'No update found.'}\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return

        self._send(
            f"⬇️ <b>Update found — applying now</b>\n━━━━━━━━━━━━━━━━\n"
            f"Current: <code>{result['local_commit'][:8]}</code>\n"
            f"New:     <code>{result['remote_commit'][:8]}</code>\n"
            f"Pulling and restarting — the watchdog will bring it back "
            f"up automatically. You may see a brief 'unhealthy' alert "
            f"during the restart window; that's expected."
        )

        applied = auto_updater.perform_update(self.config, tg_send_fn=self._send)
        if applied:
            log.info("[CMD] /update applied — exiting now so the watchdog relaunches on new code")
            import os
            os._exit(0)   # same reasoning as auto_updater.update_check_worker: only
                           # os._exit reliably terminates the whole process from a
                           # non-main thread (the Telegram command handler's own thread)
        # if applied is False, perform_update has already sent the reason via tg_send_fn

    def _list_open_positions(self) -> list:
        """Returns [(ex_name, symbol, coin), ...] for every currently held position."""
        out = []
        for ex_name, positions in self.coin_in_position.items():
            for sym, holding in positions.items():
                if holding:
                    out.append((ex_name, sym, sym.split("-")[0]))
        return out

    def _sell_one(self, ex_name: str, sym: str) -> tuple:
        """
        Sells a single position. Returns (label, error_or_None).
        Shared by the 'sell all' and 'sell one coin' paths so both go
        through identical price-fetch + sell_callback logic.
        """
        mode         = self.mode_ref[0] if self.mode_ref else "paper"
        exchange_obj = self.exchanges.get(ex_name)
        label        = f"{sym} [{ex_name.upper()}]"
        try:
            price = exchange_obj.get_price(sym) if exchange_obj else self.coin_buy_price[ex_name].get(sym, 0)
            self.sell_callback(ex_name, exchange_obj, sym, price, mode)
            return label, None
        except Exception as e:
            return label, str(e)

    def cmd_sell_all(self):
        """
        /sell entrypoint. Shows a numbered menu instead of immediately
        selling everything:
          1. Sell ALL open positions to USDT
          2/3/... Sell one specific coin (one option per open position)
        Your next message (a number, or the coin symbol/name itself)
        resolves the menu. Sending anything else, or letting it sit
        unanswered while you send other commands, simply leaves the menu
        pending — it doesn't block other commands from working normally.
        """
        if not self.sell_callback:
            self._send("⚠️ Sell is not available in this build.")
            return

        positions = self._list_open_positions()
        if not positions:
            self._send(
                "💰 <b>Sell</b>\n━━━━━━━━━━━━━━━━\nNo open positions to sell."
            )
            return

        lines = ["💰 <b>Sell — choose an option</b>\n━━━━━━━━━━━━━━━━",
                 "<b>1.</b> Sell ALL open positions → USDT"]
        for i, (ex_name, sym, coin) in enumerate(positions, start=2):
            lines.append(f"<b>{i}.</b> Sell {coin} only [{ex_name.upper()}]")
        lines.append("\nReply with a number, or just type the coin symbol "
                     "(e.g. <code>ZEC</code>) to sell that one.")
        lines.append("This expires once you send another command, or in 10 minutes.")

        self._pending_sell_menu = {
            "positions": positions,            # [(ex_name, sym, coin), ...] — index 0 = option "2"
            "created_at": datetime.now(),
        }
        self._send("\n".join(lines))
        log.info(f"[CMD] /sell menu shown — {len(positions)} open position(s)")

    def _handle_sell_menu_reply(self, text: str) -> bool:
        """
        Resolves a pending /sell menu against the user's next message.
        Returns True if the message was consumed as a menu reply (whether
        or not it matched a valid option) so it isn't also treated as an
        unknown command; returns False if there's no pending menu at all,
        so normal command routing proceeds untouched.
        """
        menu = getattr(self, "_pending_sell_menu", None)
        if not menu:
            return False

        # Expire stale menus instead of resolving against a long-past list
        # of positions that may no longer reflect what's actually open.
        age_minutes = (datetime.now() - menu["created_at"]).total_seconds() / 60
        if age_minutes > 10:
            self._pending_sell_menu = None
            return False   # let it fall through to normal command handling

        positions = menu["positions"]
        choice    = text.strip()

        # Numeric choice: "1" = sell all, "2"+ = the matching position
        if choice.isdigit():
            n = int(choice)
            self._pending_sell_menu = None
            if n == 1:
                self._execute_sell_all(positions)
                return True
            idx = n - 2
            if 0 <= idx < len(positions):
                ex_name, sym, coin = positions[idx]
                self._execute_sell_one(ex_name, sym, coin)
                return True
            self._send(f"⚠️ {n} isn't a valid option. /sell again to see the list.")
            return True

        # Symbol/coin-name choice: match against the open positions list,
        # case-insensitively, against either the coin ticker or full symbol.
        choice_upper = choice.upper().lstrip("/")
        matches = [(ex, sym, coin) for ex, sym, coin in positions
                   if coin.upper() == choice_upper or sym.upper() == choice_upper]
        if matches:
            self._pending_sell_menu = None
            if len(matches) == 1:
                ex_name, sym, coin = matches[0]
                self._execute_sell_one(ex_name, sym, coin)
            else:
                # Same coin held on more than one exchange — sell all matches
                self._execute_sell_all(matches)
            return True

        # Didn't match a number or a held coin — leave the menu pending in
        # case this was an unrelated message, but say so clearly.
        self._send(
            f"❓ <code>{text}</code> doesn't match a menu option or open "
            f"position. Reply with a number from the list, or /sell again."
        )
        return True

    def _execute_sell_all(self, positions: list):
        sold, failed = [], []
        for ex_name, sym, coin in positions:
            label, err = self._sell_one(ex_name, sym)
            (failed if err else sold).append(f"{label}: {err}" if err else label)

        sold_str   = "\n".join(f"  ✅ {s}" for s in sold)   if sold   else "  None were open"
        failed_str = f"Failures:\n" + "\n".join(f"  ❌ {f}" for f in failed) if failed else ""
        self._send(
            "💰 <b>Sell-All Executed</b>\n━━━━━━━━━━━━━━━━\n"
            f"{sold_str}\n{failed_str}\n"
            "All proceeds returned to USDT in the trading pool.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        log.info(f"[CMD] /sell (all) executed — sold {len(sold)}, failed {len(failed)}")

    def _execute_sell_one(self, ex_name: str, sym: str, coin: str):
        label, err = self._sell_one(ex_name, sym)
        if err:
            self._send(
                f"❌ <b>Sell Failed — {coin} [{ex_name.upper()}]</b>\n"
                f"━━━━━━━━━━━━━━━━\n{err}\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            log.error(f"[CMD] /sell ({coin}) failed: {err}")
        else:
            self._send(
                f"✅ <b>Sold — {coin} [{ex_name.upper()}]</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Position closed, proceeds returned to USDT in the trading pool.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            log.info(f"[CMD] /sell ({coin}) executed successfully")

    def cmd_find_deposits(self):
        """
        Manually check right now for new BTC/BCH/USDT deposits and pull them
        into the trading pool immediately, instead of waiting for the
        twice-monthly auto-convert schedule.
        """
        try:
            from deposit_monitor import DepositMonitor, AutoConverter
        except Exception as e:
            self._send(f"⚠️ Deposit modules unavailable: {e}")
            return

        if self.config.PAPER_TRADING:
            self._send(
                "📄 <b>Paper mode</b> — no real deposits to check.\n"
                "/find only checks live exchange balances in LIVE mode."
            )
            return

        self._send("🔍 Checking exchanges for new BTC/BCH/USDT deposits now...")

        results = []
        for ex_name, exchange_obj in self.exchanges.items():
            try:
                # Check USDT balance directly
                usdt_bal = exchange_obj.get_usdt_balance()
                with self.pool_locks[ex_name]:
                    pool_before = self.pool_usdt[ex_name]
                    if usdt_bal > pool_before:
                        diff = usdt_bal - pool_before
                        self.pool_usdt[ex_name] = usdt_bal
                        results.append(f"  💵 {ex_name.upper()}: +${diff:.2f} USDT found and added")

                # Check BTC/BCH and convert 80% immediately (same logic as scheduled conversion)
                for coin in ("BTC", "BCH"):
                    try:
                        balance = exchange_obj.get_coin_balance(coin)
                        if balance and balance > 0:
                            symbol = f"{coin}-USDT"
                            price  = exchange_obj.get_price(symbol)
                            sell_qty = round(balance * 0.80, 8)
                            if sell_qty * price < 1.0:
                                continue   # not worth converting, too small
                            exchange_obj.place_market_sell(symbol, sell_qty)
                            usdt_gained = round(sell_qty * price, 4)
                            with self.pool_locks[ex_name]:
                                self.pool_usdt[ex_name] += usdt_gained
                            results.append(
                                f"  🪙 {ex_name.upper()}: converted {sell_qty:.6f} {coin} "
                                f"→ ${usdt_gained:.2f} USDT (80%, kept 20%)"
                            )
                    except Exception:
                        continue

            except Exception as e:
                results.append(f"  ⚠️ {ex_name.upper()}: check failed — {e}")

        result_str = "\n".join(results) if results else "  No new deposits found."
        self._send(
            "🔍 <b>Manual Deposit Check Complete</b>\n━━━━━━━━━━━━━━━━\n"
            f"{result_str}\n\n"
            "This runs the same 80%-convert logic as the 15th/30th schedule,\n"
            "but on demand whenever you send /find.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        log.info(f"[CMD] /find manual deposit check complete — {len(results)} updates")

    def _write_config_updates(self, updates: dict):
        """Helper — writes a dict of param:value pairs into config.py AND
        reloads the live config module so the change takes effect immediately
        instead of only existing on disk until the next restart."""
        import re
        with open("config.py", "r", encoding="utf-8") as f:
            content = f.read()
        for key, val in updates.items():
            pattern = rf"^({re.escape(key)}\s*=\s*)(.+?)(\s*(?:#.*)?)$"
            content = re.sub(pattern, rf"\g<1>{val}\g<3>",
                             content, flags=re.MULTILINE)
        content += f"\n# Mode switch via Telegram {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        with open("config.py", "w", encoding="utf-8") as f:
            f.write(content)

        from config_live import reload_config
        reloaded = reload_config()
        if not reloaded:
            log.error("[CMD] Config written to disk but LIVE RELOAD FAILED — "
                     "bot may still be running on old settings until restarted")

    def cmd_diag(self):
        """Diagnostic command — shows exactly why trades are/aren't firing."""
        try:
            from strategy_engine import _trackers
            cfg = self.config

            with self.active_coins_lock:
                coin_lines = {ex: list(coins) for ex, coins in self.active_coins.items()}

            active_summary = ""
            for ex, coins in coin_lines.items():
                holding = [s for s in coins if self.coin_in_position.get(ex,{}).get(s)]
                active_summary += (f"\n  <b>{ex.upper()}</b>: {len(coins)} active coins "
                                  f"({len(holding)} currently held)\n"
                                  f"    {', '.join(c.split('-')[0] for c in coins) or 'none'}")

            sl_cap    = getattr(cfg, "MAX_STOP_LOSS_PCT", 0.04)
            min_trade = getattr(cfg, "MIN_TRADE_USDT", 10.0)
            reserve   = getattr(cfg, "LISTING_RESERVE_USDT", 5.0)

            # ── Check if running process actually matches what's on disk ────
            # Catches the failure mode where /aggressive, /safe, regime auto-
            # adaptation, or the monthly/weekly self-tuning wrote new values
            # to config.py but the live reload silently failed, leaving the
            # bot running on stale settings with no visible symptom otherwise.
            sync_line = ""
            try:
                from config_live import verify_live_matches_disk
                sync = verify_live_matches_disk()
                if sync.get("in_sync") is False:
                    mismatch_str = "\n".join(
                        f"    {m['key']}: live={m['live_value']} but disk={m['disk_value']}"
                        for m in sync["mismatches"]
                    )
                    sync_line = (f"\n⚠️ <b>LIVE/DISK MISMATCH DETECTED</b>\n"
                               f"The bot is running on OLD settings despite config.py\n"
                               f"having different values on disk:\n{mismatch_str}\n"
                               f"This means a recent /aggressive, /safe, or auto-tune\n"
                               f"change did not actually take effect. Restart the bot.\n")
                elif sync.get("in_sync") is True:
                    sync_line = "\n✅ Live settings match config.py on disk — in sync.\n"
            except Exception:
                pass

            # ── Per-coin pool-type breakdown — the actual proof point ───────
            # Recomputes the EXACT same split logic bot.py uses, so this
            # shows the real, current NORMAL/AGGRESSIVE assignment per coin
            # rather than just whether DUAL_POOL_ENABLED is True. This is
            # the direct way to confirm whether /aggressive is actually
            # affecting anything, instead of inferring it from the flag.
            pool_split_lines = ""
            dual_enabled = getattr(cfg, "DUAL_POOL_ENABLED", False)
            aggr_pct     = getattr(cfg, "AGGRESSIVE_POOL_PCT", 0.20)
            for ex, coins in coin_lines.items():
                if not coins:
                    continue
                if not dual_enabled:
                    pool_split_lines += f"\n  <b>{ex.upper()}</b>: all {len(coins)} coins → NORMAL (split is OFF)\n"
                    continue
                split = max(1, int(len(coins) * (1 - aggr_pct)))
                normal_coins     = coins[:split]
                aggressive_coins = coins[split:]
                pool_split_lines += (
                    f"\n  <b>{ex.upper()}</b> ({len(coins)} coins, split is ON):\n"
                    f"    🛡️ NORMAL ({len(normal_coins)}): "
                    f"{', '.join(c.split('-')[0] for c in normal_coins) or 'none'}\n"
                    f"    🔥 AGGRESSIVE ({len(aggressive_coins)}): "
                    f"{', '.join(c.split('-')[0] for c in aggressive_coins) or 'none'}\n"
                )

            lines = [
                f"🔧 <b>Diagnostics</b>",
                f"━━━━━━━━━━━━━━━━",
                f"Mode:                   {'🔥 AGGRESSIVE (50/50 split)' if getattr(cfg,'DUAL_POOL_ENABLED',False) else '🛡️ SAFE (100%)'}",
                f"RSI_BUY / RSI_SELL:     {cfg.RSI_BUY} / {cfg.RSI_SELL}",
                f"ENGINE_CONFIDENCE_MIN:  {getattr(cfg,'ENGINE_CONFIDENCE_MIN',55)}%",
                f"AI_CONFIDENCE_MIN:      {getattr(cfg,'AI_CONFIDENCE_MIN',70)}%",
                f"AI_ENABLED:             {cfg.AI_ENABLED}",
                f"PAPER_TRADING:          {cfg.PAPER_TRADING}",
                f"MIN_TRADE_USDT:         ${min_trade:.2f}",
                f"LISTING_RESERVE_USDT:   ${reserve:.2f}",
                f"MAX_STOP_LOSS_PCT:      {sl_cap*100:.0f}% (hard cap)",
                f"AUTO_APPLY_ENABLED:     {'✅ ON — changes ≤' + str(int(getattr(cfg,'AUTO_APPLY_MAX_CHANGE_PCT',0.05)*100)) + '% apply automatically' if getattr(cfg,'AUTO_APPLY_ENABLED',False) else '❌ OFF — every proposal waits on Y/N'}",
                f"Paused (manual):        {self.pause_flag.is_set() if self.pause_flag else 'N/A'}",
                sync_line,
                f"\n💰 <b>Actual pool-type assignment right now:</b>{pool_split_lines}",
                f"\n📋 <b>Active coins per exchange:</b>{active_summary}",
                "",
                f"<b>Coins tracked by engine (have seen ≥1 signal):</b> {len(_trackers)}",
            ]
            if not _trackers:
                lines.append("  No signals have reached the engine yet.")
                lines.append("  This usually means RSI/MA hasn't triggered, or")
                lines.append("  candle data is still warming up (need 40+ candles).")
            self._send("\n".join(lines))
        except Exception as e:
            self._send(f"⚠️ Diagnostics error: {e}")


    def cmd_autoapply(self):
        """Shows recent auto-applied changes and current auto-apply settings."""
        import json
        from pathlib import Path
        cfg = self.config

        enabled    = getattr(cfg, "AUTO_APPLY_ENABLED", False)
        small_cap  = getattr(cfg, "AUTO_APPLY_MAX_CHANGE_PCT", 0.05)
        large_cap  = getattr(cfg, "AUTO_APPLY_REQUIRE_APPROVAL_PCT", 0.15)

        header = (
            f"⚡ <b>Auto-Apply Settings</b>\n━━━━━━━━━━━━━━━━\n"
            f"Status:        {'✅ ON' if enabled else '❌ OFF'}\n"
            f"Auto-apply if: change ≤ {small_cap*100:.0f}%\n"
            f"Always ask if: change ≥ {large_cap*100:.0f}%\n"
            f"Coin/wallet/exchange changes always require approval\n\n"
        )

        try:
            log_path = Path("logs/approval_history.json")
            if not log_path.exists():
                self._send(header + "No approval history yet.")
                return

            with open(log_path) as f:
                history = json.load(f)

            auto_applied = [h for h in history if h.get("outcome") == "auto_applied"][-8:]

            if not auto_applied:
                self._send(header + "No changes have been auto-applied yet.\n"
                          "Everything so far has gone through manual Y/N approval.")
                return

            lines = [header + f"<b>Last {len(auto_applied)} auto-applied changes:</b>\n"]
            for h in reversed(auto_applied):
                t = h.get("time", "")[:16].replace("T", " ")
                changes = ", ".join(f"{k}→{v}" for k, v in h.get("proposed", {}).items())
                lines.append(f"  🕐 {t}\n      {h.get('type','?')}\n      {changes}\n")

            self._send("".join(lines))

        except Exception as e:
            self._send(f"{header}⚠️ Could not load history: {e}")

    def cmd_weekly(self):
        with self.monthly_lock:
            all_trades = list(self.monthly_trades)

        # Filter to current ISO week using the date field stored on each trade
        today    = date.today()
        iso_year, iso_week, _ = today.isocalendar()
        trades = []
        for t in all_trades:
            try:
                t_date = date.fromisoformat(t.get("date", ""))
                ty, tw, _ = t_date.isocalendar()
                if ty == iso_year and tw == iso_week:
                    trades.append(t)
            except (ValueError, AttributeError, TypeError):
                pass

        week_label = f"Week {iso_week}, {today.strftime('%Y')} ({today.strftime('%b %d')} week)"
        mode       = "📄 PAPER" if self.config.PAPER_TRADING else "💰 LIVE"
        starting   = getattr(self.config, "PAPER_STARTING_USDT", 100.0)

        if not trades:
            self._send(
                f"📅 <b>Weekly Report — {week_label}</b>\n━━━━━━━━━━━━━━━━\n"
                f"Mode: {mode}\nNo trades this week yet.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return

        num   = len(trades)
        wins  = [t for t in trades if t.get("pnl_gross", 0) >= 0]
        gross = sum(t.get("pnl_gross", 0) for t in trades)
        fees  = sum(t.get("fees", 0)      for t in trades)
        net   = gross - fees
        wr    = len(wins) / num * 100
        roi   = (net / starting * 100) if starting > 0 else 0

        best  = max(trades, key=lambda t: t.get("pnl_net", 0))
        worst = min(trades, key=lambda t: t.get("pnl_net", 0))

        coin_stats = {}
        for t in trades:
            k = t["coin"]
            coin_stats.setdefault(k, {"n": 0, "gross": 0.0, "fees": 0.0, "net": 0.0})
            coin_stats[k]["n"]     += 1
            coin_stats[k]["gross"] += t.get("pnl_gross", 0)
            coin_stats[k]["fees"]  += t.get("fees", 0)
            coin_stats[k]["net"]   += t.get("pnl_net", 0)

        top_coins = sorted(coin_stats.items(), key=lambda x: x[1]["net"], reverse=True)[:5]
        coin_lines = ""
        for k, s in top_coins:
            sg = "+" if s["gross"] >= 0 else ""
            sn = "+" if s["net"]   >= 0 else ""
            coin_lines += (f"  • {k} ({s['n']} trades)\n"
                          f"      Gross {sg}${s['gross']:.4f}  Fee -${s['fees']:.4f}  "
                          f"Net <b>{sn}${s['net']:.4f}</b>\n")

        sign     = "+" if net >= 0 else ""
        sign_roi = "+" if roi >= 0 else ""
        arrow    = "📈" if net >= 0 else "📉"

        self._send(
            f"📅 <b>Weekly Report — {week_label}</b>\n━━━━━━━━━━━━━━━━\n"
            f"Mode: {mode}\n\n"
            f"📊 <b>Totals:</b>\n"
            f"  Trades:   {num}\n"
            f"  Win rate: {wr:.0f}% ({len(wins)} wins / {num-len(wins)} losses)\n\n"
            f"💵 Gross P&L:  {'+' if gross>=0 else ''}${gross:.4f}\n"
            f"💸 Total fees: -${fees:.4f}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{arrow} <b>Net: {sign}${net:.4f} USDT</b>\n"
            f"📊 <b>ROI: {sign_roi}{roi:.2f}% on ${starting:.0f} starting pool</b>\n\n"
            f"🏆 Best trade:  {best['coin']} net +${best.get('pnl_net',0):.4f}\n"
            f"💔 Worst trade: {worst['coin']} net ${worst.get('pnl_net',0):.4f}\n\n"
            f"📌 <b>Top coins:</b>\n{coin_lines}"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def cmd_ai_on(self):
        try:
            self._write_config_updates({"AI_ENABLED": True})
            self._send(
                "🧠 <b>AI Trading Enabled</b>\n━━━━━━━━━━━━━━━━\n"
                "The AI analyst will now review buy signals before trades are placed.\n"
                f"Signals below AI_CONFIDENCE_MIN ({getattr(self.config,'AI_CONFIDENCE_MIN',70)}%) will be skipped.\n\n"
                "✅ <b>Live now</b> — no restart needed.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            log.info("[CMD] AI enabled via Telegram /on")
        except Exception as e:
            self._send(f"⚠️ Could not enable AI: {e}")

    def cmd_ai_off(self):
        try:
            self._write_config_updates({"AI_ENABLED": False})
            self._send(
                "🤖 <b>AI Trading Disabled</b>\n━━━━━━━━━━━━━━━━\n"
                "Bot will now trade on RSI/MA signals alone — no AI filter.\n"
                "Expect more signals to fire; they won't be vetted by AI confidence.\n\n"
                "✅ <b>Live now</b> — no restart needed.\n"
                "Send /on to re-enable AI at any time.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            log.info("[CMD] AI disabled via Telegram /off")
        except Exception as e:
            self._send(f"⚠️ Could not disable AI: {e}")

    def cmd_pause(self):
        if self.pause_flag:
            self.pause_flag.set()
            self._send(
                "⛔ <b>Trading Paused</b>\n━━━━━━━━━━━━━━━━\n"
                "No new buy orders will be placed.\n"
                "Existing positions continue to be monitored.\n"
                "Send /resume to restart buying.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            log.info("[CMD] Trading paused by Telegram command")
        else:
            self._send("⚠️ Pause not available in this mode.")

    def cmd_resume(self):
        if self.pause_flag:
            self.pause_flag.clear()
            self._send(
                "✅ <b>Trading Resumed</b>\n━━━━━━━━━━━━━━━━\n"
                "Bot is now actively looking for buy signals.\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            log.info("[CMD] Trading resumed by Telegram command")
        else:
            self._send("⚠️ Resume not available.")

    def cmd_stop(self):
        self._send(
            "🛑 <b>Stopping Bot...</b>\n━━━━━━━━━━━━━━━━\n"
            "Completing current cycles and shutting down.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        log.info("[CMD] Stop requested via Telegram")
        if self.stop_callback:
            threading.Thread(target=self.stop_callback, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    #  COMMAND ROUTER
    # ══════════════════════════════════════════════════════════════════════

    COMMANDS = {
        "/help":    "cmd_help",
        "/status":  "cmd_status",
        "/heartbeat":"cmd_heartbeat",
        "/version": "cmd_version",
        "/update":  "cmd_update",
        "/trades":  "cmd_trades",
        "/daily":   "cmd_daily",
        "/weekly":  "cmd_weekly",
        "/monthly": "cmd_monthly",
        "/yearly":  "cmd_yearly",
        "/coins":   "cmd_coins",
        "/news":    "cmd_news",
        "/score":   "cmd_score",
        "/regime":  "cmd_regime",
        "/engine":  "cmd_engine",
        "/ai_stats":"cmd_ai_stats",
        "/hybrid":  "cmd_hybrid",
        "/portfolio":"cmd_portfolio",
        "/optimize":"cmd_optimize",
        "/tax_export":"cmd_tax_export",
        "/diag":    "cmd_diag",
        "/capacity":"cmd_capacity",
        "/autoapply":"cmd_autoapply",
        "/aggressive":"cmd_aggressive",
        "/safe":    "cmd_safe",
        "/sell":    "cmd_sell_all",
        "/find":    "cmd_find_deposits",
        "/on":      "cmd_ai_on",
        "/off":     "cmd_ai_off",
        "/pause":   "cmd_pause",
        "/resume":  "cmd_resume",
        "/stop":    "cmd_stop",
    }

    def handle_update(self, update: dict):
        """Process a single Telegram update."""
        message = update.get("message", {})
        if not message:
            return

        chat_id = str(message.get("chat", {}).get("id", ""))
        text    = message.get("text", "").strip()

        # Security: only respond to YOUR chat ID
        if chat_id != self.authorized_chat_id:
            log.warning(f"[CMD] Unauthorized message from chat_id {chat_id} — ignored")
            return

        # ── Check for Y/N approval responses FIRST ────────────────────
        from approval_gate import handle_response, get_pending_count
        if get_pending_count() > 0:
            if handle_response(text, chat_id, self.config):
                return   # handled as approval response

        # ── Check for a pending /sell menu reply ───────────────────────
        # Only consumes the message if a menu is actually pending. A new
        # slash-command (e.g. /status) sent instead of a menu reply is
        # left alone here and falls through to normal command routing —
        # that's what the "not text.lower().startswith('/')" guard is for.
        if self._pending_sell_menu and not text.lower().startswith("/"):
            if self._handle_sell_menu_reply(text):
                return

        text_lower = text.lower()

        # Strip bot username if present
        if "@" in text_lower:
            text_lower = text_lower.split("@")[0]

        log.info(f"[CMD] Received: {text_lower}")

        handler_name = self.COMMANDS.get(text_lower)
        if handler_name:
            try:
                getattr(self, handler_name)()
            except Exception as e:
                log.error(f"[CMD] Handler error for {text_lower}: {e}")
                self._send(f"⚠️ Error running {text_lower}: {e}")
        elif text_lower.startswith("/"):
            self._send(
                f"❓ Unknown command: <code>{text_lower}</code>\n"
                f"Send /help to see all available commands."
            )

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN LOOP
    # ══════════════════════════════════════════════════════════════════════

    def run(self, stop_event: threading.Event):
        """Poll for commands every 5 seconds."""
        log.info("[CMD] Telegram command handler started")
        log.info(f"[CMD] Listening for commands from chat ID: {self.authorized_chat_id}")

        # ── Self-check: every registered command must have a real method ───
        # Catches the class of bug where a command is added to COMMANDS but
        # its method body is missing/renamed — fails loudly at startup instead
        # of silently erroring the first time someone sends that command.
        missing = [(cmd, m) for cmd, m in self.COMMANDS.items() if not hasattr(self, m)]
        if missing:
            for cmd, m in missing:
                log.error(f"[CMD] ⚠️ BROKEN COMMAND: {cmd} → {m}() is registered "
                         f"but does not exist on the handler. This command will fail.")
            self._send(
                "⚠️ <b>Command table warning</b>\n"
                f"{len(missing)} command(s) registered but not implemented:\n" +
                "\n".join(f"  • {cmd}" for cmd, _ in missing) +
                "\nThese will error if used. Check the logs and update the bot files."
            )
        else:
            log.info(f"[CMD] ✅ All {len(self.COMMANDS)} commands verified — handler table is consistent")

        log.info("[CMD] Send /help to your bot to see available commands")

        self._send(
            "🤖 <b>Bot command handler ready!</b>\n"
            "Send /help to see all available commands."
        )

        while not stop_event.is_set():
            try:
                updates = self._get_updates()
                for update in updates:
                    self.last_update_id = max(
                        self.last_update_id,
                        update.get("update_id", 0)
                    )
                    self.handle_update(update)
            except Exception as e:
                log.debug(f"[CMD] Poll error: {e}")

            stop_event.wait(timeout=5)

        log.info("[CMD] Command handler stopped")

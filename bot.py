"""
HazMat Crypto Bot — Multi-Exchange Multi-Coin RSI + MA Trading Bot
====================================================================
- Auto-discovers all coins on each exchange
- Dynamically scales trade size and coin count based on pool size
- Paper trading ON by default
- Telegram + AI optional
"""

import time
import logging
import os
import sys
import argparse
import threading
from datetime import datetime, timedelta
from collections import defaultdict

import bootstrap
bootstrap.ensure_installed()

import requests
import pandas as pd
import config
import ai_analyst
from exchanges import build_exchanges
from coin_discovery import get_top_coins, get_tier
from deposit_monitor import DepositMonitor, AutoConverter
from price_feed import get_price_cached, price_updater_worker
from risk_manager import (check_position, check_drawdown, is_paused,
                          on_buy, on_sell, calc_atr, volatility_adjusted_size,
                          tg_risk_exit, get_size_multiplier, get_entry_time)
from trade_ledger import record_trade
from listing_hunter import NewListingHunter
from telegram_commands import TelegramCommandHandler
from strategy_optimizer import StrategyOptimizer
from strategy_engine import evaluate_signal, record_trade_outcome, get_tracker
from adaptive_intelligence import AdaptiveIntelligence, set_intelligence
from market_study import MarketStudy
import approval_gate as approval_gate_module
from futures_manager import (manage_open_short, evaluate_and_open_short,
                             has_open_short, close_all_shorts)
from staking_manager import staking_worker, ensure_liquid
from hybrid_allocator import evaluate_hybrid_gate

# ── Parse --mode ───────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["paper", "live"], default=None)
args = parser.parse_args()
if args.mode == "paper":
    config.PAPER_TRADING = True
elif args.mode == "live":
    config.PAPER_TRADING = False


# ── Logging ────────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
log_file = f"logs/bot_{datetime.now().strftime('%Y%m%d')}.log"

# Windows' cmd.exe / PowerShell console defaults to the cp1252 codepage,
# which can't encode emoji (⚠️ ✅ 🛑 etc.) used throughout this bot's log
# messages. Without this wrapper, logging.StreamHandler crashes with a
# UnicodeEncodeError every time one of those characters is logged — it's
# caught internally by the logging module so it doesn't kill the process,
# but it prints a full traceback to the console on every occurrence. This
# wraps stdout so unencodable characters are swapped for a safe substitute
# (e.g. "?") instead of raising. The log FILE (above) isn't affected by
# this at all — it's already forced to UTF-8 and renders these correctly.
console_stream = open(
    sys.stdout.fileno(), mode="w", encoding=sys.stdout.encoding,
    errors="replace", buffering=1, closefd=False,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(console_stream),
    ],
)
log = logging.getLogger(__name__)

# ── Load exchanges ─────────────────────────────────────────────────────────────
EXCHANGES = build_exchanges(config.EXCHANGES)

# ── Per-exchange state ─────────────────────────────────────────────────────────
pool_locks       = {ex: threading.Lock() for ex in EXCHANGES}
pool_usdt        = {ex: config.PAPER_STARTING_USDT for ex in EXCHANGES}
coin_holdings    = {ex: defaultdict(float) for ex in EXCHANGES}
coin_in_position = {ex: defaultdict(bool)  for ex in EXCHANGES}
coin_buy_price   = {ex: defaultdict(float) for ex in EXCHANGES}
coin_buy_spent   = {ex: defaultdict(float) for ex in EXCHANGES}

# ── Dual pool tracking ─────────────────────────────────────────────────────
# Each coin is tagged as "normal" or "aggressive" when bought
# This determines which risk settings apply when selling
coin_pool_type   = {ex: defaultdict(str) for ex in EXCHANGES}  # "normal" | "aggressive"
coin_strategy_type = {ex: defaultdict(str) for ex in EXCHANGES}  # "mean_reversion" | "breakout" | "dip_buy"
                                                                   # — which logic opened this position,
                                                                   # so the sell check uses matching logic

def get_pool_params(pool_type: str) -> dict:
    """Return RSI/risk parameters for the given pool type."""
    import config as _cfg
    if pool_type == "aggressive" and getattr(_cfg, "DUAL_POOL_ENABLED", False):
        return {
            "rsi_buy":          _cfg.AGGRESSIVE_RSI_BUY,
            "rsi_sell":         _cfg.AGGRESSIVE_RSI_SELL,
            "stop_loss_pct":    _cfg.AGGRESSIVE_STOP_LOSS,
            "take_profit_pct":  _cfg.AGGRESSIVE_TAKE_PROFIT,
            "trailing_stop_pct":_cfg.AGGRESSIVE_TRAILING_STOP,
            "max_hold_hours":   _cfg.AGGRESSIVE_MAX_HOLD_HOURS,
        }
    return {
        "rsi_buy":          getattr(_cfg, "NORMAL_RSI_BUY",  _cfg.RSI_BUY),
        "rsi_sell":         getattr(_cfg, "NORMAL_RSI_SELL", _cfg.RSI_SELL),
        "stop_loss_pct":    getattr(_cfg, "NORMAL_STOP_LOSS",    getattr(_cfg, "STOP_LOSS_PCT",    0.06)),
        "take_profit_pct":  getattr(_cfg, "NORMAL_TAKE_PROFIT",  getattr(_cfg, "TAKE_PROFIT_PCT",  0.04)),
        "trailing_stop_pct":getattr(_cfg, "NORMAL_TRAILING_STOP",getattr(_cfg, "TRAILING_STOP_PCT",0.03)),
        "max_hold_hours":   getattr(_cfg, "NORMAL_MAX_HOLD_HOURS",getattr(_cfg, "MAX_HOLD_HOURS",  48)),
    }

def get_aggressive_allocation(ex_name: str) -> float:
    """Return the USDT amount allocated to aggressive pool."""
    if not getattr(config, "DUAL_POOL_ENABLED", False):
        return 0.0
    return pool_usdt[ex_name] * getattr(config, "AGGRESSIVE_POOL_PCT", 0.20)

def get_normal_allocation(ex_name: str) -> float:
    """Return the USDT amount allocated to normal pool."""
    if not getattr(config, "DUAL_POOL_ENABLED", False):
        return pool_usdt[ex_name]
    return pool_usdt[ex_name] * (1.0 - getattr(config, "AGGRESSIVE_POOL_PCT", 0.20))

# ── Active coin lists per exchange (updated by scaling monitor) ────────────────
active_coins     = {ex: [] for ex in EXCHANGES}
active_coins_lock = threading.Lock()

# ── Global stats ───────────────────────────────────────────────────────────────
stats_lock       = threading.Lock()
trade_count      = 0
total_pnl        = 0.0
total_fees       = 0.0

daily_lock       = threading.Lock()
daily_trades     = []
daily_reset_date = datetime.now().date()

# Monthly trade tracking (for /monthly command)
monthly_lock        = threading.Lock()
monthly_trades      = []
monthly_reset_month = datetime.now().strftime("%Y-%m")

# Manual pause flag (set by /pause command, cleared by /resume)
manual_pause = threading.Event()

# Tracks which exchanges have already had their emergency close-all
# triggered, so 10+ coin threads detecting the same emergency in the same
# poll cycle don't all try to liquidate everything simultaneously.
_emergency_handled      = set()
_emergency_handled_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
#  SCALING LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def get_current_tier(ex_name: str) -> dict:
    return get_tier(pool_usdt[ex_name], config.SCALING_TIERS)

def get_max_per_trade(ex_name: str) -> float:
    return get_current_tier(ex_name)["max_per_trade"]

def get_max_coins(ex_name: str) -> int:
    return get_current_tier(ex_name)["max_coins"]


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

def tg_send(message, alert_type="general"):
    if not config.TELEGRAM_ENABLED:
        return
    type_map = {
        "buy": config.NOTIFY_ON_BUY, "sell": config.NOTIFY_ON_SELL,
        "error": config.NOTIFY_ON_ERROR, "start": config.NOTIFY_ON_START,
        "stop": config.NOTIFY_ON_STOP, "skip": config.NOTIFY_BALANCE_SKIP,
        "general": True,
    }
    if not type_map.get(alert_type, True):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram: {e}")

def tg_buy(ex, symbol, qty, price, spent, mode, pool_rem):
    coin = symbol.split("-")[0]
    tag  = "📄 PAPER" if mode == "paper" else "💰 LIVE"
    tg_send(
        f"{tag} <b>BUY — {coin}</b> [{ex.upper()}]\n━━━━━━━━━━━━━━━━\n"
        f"💵 Price:     <b>${price:,.6f}</b>\n"
        f"📦 Qty:       <b>{qty:.6f} {coin}</b>\n"
        f"💸 Spent:     <b>${spent:.2f} USDT</b>\n"
        f"🏦 Pool left: <b>${pool_rem:.2f} USDT</b>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "buy")

def tg_sell(ex, symbol, qty, price, proceeds, buy_price, buy_spent, pnl_gross, pnl_pct, pool_total, mode, fees=0.0, pnl_net=None):
    coin  = symbol.split("-")[0]
    tag   = "📄 PAPER" if mode == "paper" else "💰 LIVE"

    # pnl_net defaults to gross-fees if not explicitly passed (keeps old call sites safe)
    if pnl_net is None:
        pnl_net = pnl_gross - fees

    net_pct = (pnl_net / buy_spent * 100) if buy_spent else 0
    arrow   = "📈" if pnl_net >= 0 else "📉"   # arrow reflects NET, not gross

    # Format with sign BEFORE the dollar sign (e.g. "-$8.00" not "$-8.00")
    def fmt(val, pct=None):
        sign = "+" if val >= 0 else "-"
        s = f"{sign}${abs(val):.4f}"
        if pct is not None:
            s += f" ({sign}{abs(pct):.2f}%)"
        return s

    tg_send(
        f"{tag} <b>SELL — {coin}</b> [{ex.upper()}]\n━━━━━━━━━━━━━━━━\n"
        f"💵 Sold @ <b>${price:,.6f}</b>  Bought @ <b>${buy_price:,.6f}</b>\n"
        f"📦 Qty:      <b>{qty:.6f} {coin}</b>\n"
        f"💰 Proceeds: <b>${proceeds:.4f} USDT</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📊 Gross P&amp;L:  <b>{fmt(pnl_gross, pnl_pct)}</b>\n"
        f"💸 Trade fee:   <b>-${fees:.4f}</b>\n"
        f"{arrow} <b>Net P&amp;L:    {fmt(pnl_net, net_pct)}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🏦 Pool:     <b>${pool_total:.2f} USDT</b>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "sell")

def tg_scale(ex, old_tier, new_tier, pool, coins):
    tg_send(
        f"📊 <b>Scaling Update — {ex.upper()}</b>\n━━━━━━━━━━━━━━━━\n"
        f"Pool:      <b>${pool:.2f} USDT</b>\n"
        f"Tier:      <b>{old_tier} → {new_tier}</b>\n"
        f"Max trade: <b>${get_tier(pool, config.SCALING_TIERS)['max_per_trade']:.0f} USDT</b>\n"
        f"Coins now: <b>{len(coins)} active</b>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "general")

def tg_start(active_exchanges, mode, tiers_info):
    tag = "📄 PAPER" if mode == "paper" else "💰 LIVE"
    ex_lines = "\n".join([
        f"  • {e.upper()}: {info['label']} tier — ${info['max_per_trade']}/trade, {info['coins']} coins"
        for e, info in tiers_info.items()
    ])
    ai_line = (f"🤖 AI: <b>ON — {config.AI_MODE}, {config.AI_CONFIDENCE_MIN}% confidence</b>\n"
               if config.AI_ENABLED else "🤖 AI: disabled\n")
    try:
        from version import __version__
        version_line = f"Version:   <b>v{__version__}</b>\n"
    except Exception:
        version_line = ""
    tg_send(
        f"🚀 <b>Multi-Exchange Bot Started</b>\n━━━━━━━━━━━━━━━━\n"
        f"{version_line}"
        f"Mode: {tag}\n"
        f"Exchanges:\n{ex_lines}\n"
        f"Pool/exch: <b>${config.PAPER_STARTING_USDT:.2f} USDT</b>\n"
        f"Reserved:  <b>${getattr(config,'LISTING_RESERVE_USDT',0):.2f} USDT</b> (new listings)\n"
        f"Tradeable: <b>${max(0, config.PAPER_STARTING_USDT - getattr(config,'LISTING_RESERVE_USDT',0)):.2f} USDT</b>\n"
        f"{ai_line}"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "start")

def tg_stop(tc, tpnl, pools, mode):
    tag  = "📄 PAPER" if mode == "paper" else "💰 LIVE"
    sign = "+" if tpnl >= 0 else ""
    pool_lines = "\n".join([f"  • {e.upper()}: ${b:.2f}" for e, b in pools.items()])
    tg_send(
        f"🛑 <b>Bot Stopped</b>\n━━━━━━━━━━━━━━━━\n"
        f"Mode: {tag}  |  Trades: <b>{tc}</b>\n"
        f"P&amp;L: <b>{sign}${tpnl:.4f} USDT</b>\n"
        f"Pools:\n{pool_lines}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "stop")

def tg_skip(ex, symbol, balance, minimum):
    coin = symbol.split("-")[0]
    tg_send(
        f"⛔ <b>Skipped — {coin} [{ex.upper()}]</b>\n"
        f"Pool <b>${balance:.2f}</b> below min <b>${minimum:.2f}</b> USDT.\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "skip")

def tg_error(ex, symbol, err):
    coin = symbol.split("-")[0]
    tg_send(f"⚠️ <b>Error — {coin} [{ex.upper()}]</b>\n{err}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "error")

def tg_ai_veto(ex, symbol, action, conf, reason, sentiment="unknown"):
    coin = symbol.split("-")[0]
    s_emoji = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(sentiment,"❓")
    tg_send(
        f"🤖 <b>AI Vetoed — {coin} [{ex.upper()}]</b>\n━━━━━━━━━━━━━━━━\n"
        f"Signal: <b>{action}</b> | Confidence: <b>{conf}%</b>\n"
        f"News:   {s_emoji} <b>{sentiment}</b>\n"
        f"Reason: {reason}\n➡️ Skipped.\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "general")

def tg_ai_approve(ex, symbol, action, conf, reason, sentiment="unknown"):
    coin = symbol.split("-")[0]
    s_emoji = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(sentiment,"❓")
    tg_send(
        f"🤖 <b>AI Approved — {coin} [{ex.upper()}]</b>\n━━━━━━━━━━━━━━━━\n"
        f"Signal:    <b>{action}</b> | Confidence: <b>{conf}%</b>\n"
        f"News:      {s_emoji} <b>{sentiment}</b>\n"
        f"Reason:    {reason}\n✅ Proceeding.\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "general")

def tg_invalid_pair(ex, symbol):
    tg_send(f"⚠️ <b>Invalid Pair</b> — {symbol} [{ex.upper()}] not found. Skipping.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "error")

def _tg_coin_rerank(ex_name, new_coins, added, removed):
    """Notify when hourly news re-rank changes the active coin list."""
    added_str   = "\n".join([f"  ✅ {s}" for s in sorted(added)])   if added   else "  None"
    removed_str = "\n".join([f"  ❌ {s}" for s in sorted(removed)]) if removed else "  None"
    tg_send(
        f"🔄 <b>Hourly Coin Re-rank — {ex_name.upper()}</b>\n━━━━━━━━━━━━━━━━\n"
        f"News analysis updated coin priorities.\n\n"
        f"<b>Added to active list:</b>\n{added_str}\n\n"
        f"<b>Removed from active list:</b>\n{removed_str}\n\n"
        f"Now trading: <b>{len(new_coins)} coins</b>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "general")

def tg_heartbeat(pools, positions, tc, tpnl, tiers, mode):
    tag   = "📄 PAPER" if mode == "paper" else "💰 LIVE"
    sign  = "+" if tpnl >= 0 else ""
    arrow = "📈" if tpnl >= 0 else "📉"
    pool_lines = "\n".join([
        # BUG FIX: this previously read tiers[e]['max_coins'] — the tier's
        # CEILING (e.g. "Starter tier allows up to 15 coins"), not how many
        # coins are actually being watched right now. On an exchange with
        # fewer qualifying pairs than the ceiling (seen live: KuCoin found
        # 257 candidates so its ceiling and real count looked similar by
        # coincidence; Kraken found only 11, so the ceiling of 15 was
        # actively wrong) this silently overstated the real watchlist size.
        # tiers[e]['coins'] is the real len(coins) already computed at
        # startup/re-rank time — see tiers_info construction in run().
        f"  • {e.upper()}: ${b:.2f} ({tiers.get(e,{}).get('label','?')} — {tiers.get(e,{}).get('coins','?')} coins)"
        for e, b in pools.items()
    ])
    pos_lines = "".join([f"  • {k}: {'+'if v>=0 else ''}${v:.3f}\n" for k, v in positions.items()]) or "  None open\n"
    tg_send(
        f"💓 <b>Heartbeat — {tag}</b>\n━━━━━━━━━━━━━━━━\n"
        f"Pools:\n{pool_lines}\n"
        f"📊 Trades: <b>{tc}</b>\n"
        f"{arrow} P&amp;L: <b>{sign}${tpnl:.4f} USDT</b>\n"
        f"📌 Open positions:\n{pos_lines}"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "general")

def tg_daily_report(report_date, trades, pools, tiers, mode):
    tag = "📄 PAPER" if mode == "paper" else "💰 LIVE"
    if not trades:
        tg_send(
            f"🌙 <b>Daily Report — {report_date}</b>\n{tag}\nNo trades today.\n"
            f"Pools:\n" + "\n".join([f"  • {e.upper()}: ${b:.2f}" for e, b in pools.items()])
            + f"\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "general")
        return

    num     = len(trades)
    wins    = [t for t in trades if t["pnl_gross"] >= 0]
    gross   = sum(t["pnl_gross"] for t in trades)
    fees    = sum(t["fees"]      for t in trades)
    net     = gross - fees
    wr      = len(wins)/num*100
    sign    = "+" if net >= 0 else ""
    arrow   = "📈" if net >= 0 else "📉"

    coin_stats = {}
    for t in trades:
        k = f"{t['coin']} [{t['exchange'].upper()}]"
        coin_stats.setdefault(k, {"n":0,"gross":0.0,"fees":0.0})
        coin_stats[k]["n"]     += 1
        coin_stats[k]["gross"] += t["pnl_gross"]
        coin_stats[k]["fees"]  += t["fees"]

    coin_lines = ""
    for k, s in sorted(coin_stats.items(), key=lambda x: x[1]["gross"]-x[1]["fees"], reverse=True):
        n  = s["gross"] - s["fees"]
        sg = "+" if s["gross"] >= 0 else ""
        sn = "+" if n >= 0 else ""
        coin_lines += (f"  • {k} ({s['n']} trade{'s'if s['n']!=1 else ''})\n"
                      f"      Gross {sg}${s['gross']:.4f}  Fee -${s['fees']:.4f}  "
                      f"Net <b>{sn}${n:.4f}</b>\n")

    pool_lines = "\n".join([
        f"  • {e.upper()}: ${b:.2f} ({tiers.get(e,{}).get('label','?')} tier)"
        for e, b in pools.items()
    ])

    tg_send(
        f"🌙 <b>Daily Report — {report_date}</b>\n━━━━━━━━━━━━━━━━\n"
        f"Mode: {tag}\n\n"
        f"📊 {num} trades  ✅ {len(wins)} wins  ❌ {num-len(wins)} losses  🎯 {wr:.0f}%\n\n"
        f"💵 Gross: {'+'if gross>=0 else ''}${gross:.4f}\n"
        f"💸 Fees:  -${fees:.4f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{arrow} <b>Net: {sign}${net:.4f} USDT</b>\n\n"
        f"📌 By coin:\n{coin_lines}"
        f"🏦 Pools:\n{pool_lines}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "general")


# ══════════════════════════════════════════════════════════════════════════════
#  INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return round(float((100-(100/(1+rs))).iloc[-1]), 2)

def calc_ma(series, period=20):
    return round(float(series.rolling(period).mean().iloc[-1]), 6)


# ══════════════════════════════════════════════════════════════════════════════
#  TRADING ACTIONS
# ══════════════════════════════════════════════════════════════════════════════

def place_buy(ex_name, exchange, symbol, price, mode, pool_type: str = "normal",
              strategy_type: str = "mean_reversion"):
    coin = symbol.split("-")[0]

    # ── Calculate tradeable balance ─────────────────────────────────────────
    # Two separate reserves are subtracted before anything is tradeable:
    #   1. LISTING_RESERVE_USDT  — small, protects listing-hunter capital
    #   2. CAPITAL_FLOOR_RESERVE — larger, a genuine "never touch this"
    #      emergency fund calculated as a % of STARTING capital (a fixed
    #      reference point), not the fluctuating peak the drawdown tiers
    #      use. This is intentionally a SEPARATE, additional layer — the
    #      tiered drawdown breaker can still pause/reduce trading well
    #      before this floor is ever approached; this floor is the final
    #      backstop if everything else somehow still failed to protect
    #      enough capital.
    listing_reserve = getattr(config, "LISTING_RESERVE_USDT", 0.0)

    floor_pct      = getattr(config, "CAPITAL_FLOOR_PCT", 0.0)
    starting_pool  = getattr(config, "PAPER_STARTING_USDT", 100.0)
    capital_floor  = starting_pool * floor_pct

    with pool_locks[ex_name]:
        pool            = pool_usdt[ex_name]
        tradeable_pool  = max(0.0, pool - listing_reserve - capital_floor)
        max_trade       = get_max_per_trade(ex_name)

        # Check tradeable balance (not total pool)
        if tradeable_pool < config.MIN_TRADE_USDT:
            if pool - listing_reserve >= config.MIN_TRADE_USDT and capital_floor > 0:
                log.warning(f"[{ex_name.upper()}:{coin}] ⛔ Tradeable pool ${tradeable_pool:.2f} "
                           f"below min — ${capital_floor:.2f} held as untouchable capital floor "
                           f"({floor_pct*100:.0f}% of starting capital)")
            elif pool >= config.MIN_TRADE_USDT:
                log.warning(f"[{ex_name.upper()}:{coin}] ⛔ Tradeable pool ${tradeable_pool:.2f} "
                           f"below min — ${listing_reserve:.2f} reserved for listings")
            else:
                log.warning(f"[{ex_name.upper()}:{coin}] ⛔ Pool ${pool:.2f} below min ${config.MIN_TRADE_USDT:.2f}")
            tg_skip(ex_name, symbol, tradeable_pool, config.MIN_TRADE_USDT)
            return False

        usdt_amt = min(max_trade, tradeable_pool) * config.TRADE_PCT

        # Scale down position size if pool is in a drawdown caution tier
        size_mult = get_size_multiplier(ex_name)
        if size_mult < 1.0:
            usdt_amt = round(usdt_amt * size_mult, 2)
            log.info(f"[{ex_name.upper()}:{coin}] Drawdown caution — "
                    f"size reduced to {size_mult*100:.0f}% (${usdt_amt:.2f})")

        qty      = round(usdt_amt / price, 6)

        if config.PAPER_TRADING:
            pool_usdt[ex_name]               -= usdt_amt
            coin_holdings[ex_name][symbol]    = qty
            coin_in_position[ex_name][symbol] = True
            coin_buy_price[ex_name][symbol]   = price
            coin_buy_spent[ex_name][symbol]   = usdt_amt
            coin_pool_type[ex_name][symbol]   = pool_type  # tag for sell logic
            coin_strategy_type[ex_name][symbol] = strategy_type  # mean_reversion | breakout

    # Record entry for risk management
    on_buy(ex_name, symbol, price)

    log.info(f"[{ex_name.upper()}:{coin}] 🟢 BUY {qty:.6f} @ ${price:.6f} | ${usdt_amt:.2f} | Pool ${pool_usdt[ex_name]:.2f}")

    if not config.PAPER_TRADING:
        try:
            # If some of this exchange's idle USDT is parked in flexible
            # staking, redeem whatever's needed to cover this buy BEFORE
            # placing it — otherwise a real order could fail on
            # insufficient balance even though the pool total (staked +
            # liquid) was enough. If redemption can't actually cover the
            # trade, don't place the order at all — bail out cleanly
            # instead of sending a market order likely to be rejected for
            # insufficient balance.
            if not ensure_liquid(ex_name, exchange, "USDT", usdt_amt, config, mode):
                log.error(f"[{ex_name.upper()}:{coin}] Buy aborted — could not free enough "
                         f"liquid USDT (some may still be in staking)")
                tg_error(ex_name, symbol, "Buy aborted — insufficient liquid balance even after unstaking")
                return False
            exchange.place_market_buy(symbol, usdt_amt)
            with pool_locks[ex_name]:
                coin_in_position[ex_name][symbol] = True
                coin_buy_price[ex_name][symbol]   = price
                coin_buy_spent[ex_name][symbol]   = usdt_amt
        except Exception as e:
            log.error(f"[{ex_name.upper()}:{coin}] Buy failed: {e}")
            tg_error(ex_name, symbol, f"Buy failed: {e}")
            return False

    tg_buy(ex_name, symbol, qty, price, usdt_amt, mode, pool_usdt[ex_name])
    return True


def place_sell(ex_name, exchange, symbol, price, mode):
    global trade_count, total_pnl, total_fees
    coin     = symbol.split("-")[0]
    fee_rate = exchange.fee_rate

    if config.PAPER_TRADING:
        qty       = coin_holdings[ex_name][symbol]
        buy_price = coin_buy_price[ex_name][symbol]
        buy_spent = coin_buy_spent[ex_name][symbol]

        proceeds  = round(qty * price, 8)
        pnl_gross = proceeds - buy_spent
        fees      = round((buy_spent * fee_rate) + (proceeds * fee_rate), 8)
        pnl_net   = round(pnl_gross - fees, 8)
        pnl_pct   = (pnl_gross / buy_spent * 100) if buy_spent > 0 else 0

        # Read BEFORE on_sell() clears it — the durable ledger (for tax
        # export) needs the acquisition date, not just the exit date.
        entry_time = get_entry_time(ex_name, symbol)

        with pool_locks[ex_name]:
            pool_usdt[ex_name]               += proceeds
            coin_holdings[ex_name][symbol]    = 0.0
            coin_in_position[ex_name][symbol] = False

        # Clear risk tracking
        on_sell(ex_name, symbol)

        record_trade({
            "exchange": ex_name, "coin": coin, "symbol": symbol, "side": "spot_long",
            "entry_time": entry_time.isoformat() if entry_time else None,
            "exit_time": datetime.now().isoformat(),
            "buy_price": buy_price, "sell_price": price, "qty": qty,
            "spent": buy_spent, "proceeds": proceeds,
            "pnl_gross": pnl_gross, "fees": fees, "pnl_net": pnl_net,
            "mode": mode,
        })

        with stats_lock:
            trade_count += 1
            total_pnl   += pnl_net
            total_fees  += fees

        # Record outcome in strategy engine for self-optimisation
        pnl_pct_actual = (price - buy_price) / buy_price if buy_price > 0 else 0
        record_trade_outcome(ex_name, symbol, pnl_net, pnl_pct_actual, "rsi_signal")

        # Feed adaptive intelligence for regime learning
        from adaptive_intelligence import get_intelligence
        intel = get_intelligence()
        if intel:
            intel.record_trade_for_learning(
                intel.current_regime, pnl_net, pnl_pct_actual)

        with daily_lock:
            daily_trades.append({
                "exchange": ex_name, "coin": coin,
                "buy_price": buy_price, "sell_price": price,
                "qty": qty, "spent": buy_spent, "proceeds": proceeds,
                "pnl_gross": pnl_gross, "fees": fees, "pnl_net": pnl_net,
                "time": datetime.now().strftime("%H:%M:%S"),
            })

        with monthly_lock:
            monthly_trades.append({
                "exchange": ex_name, "coin": coin,
                "buy_price": buy_price, "sell_price": price,
                "qty": qty, "spent": buy_spent, "proceeds": proceeds,
                "pnl_gross": pnl_gross, "fees": fees, "pnl_net": pnl_net,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
            })

        sign = "+" if pnl_net >= 0 else ""
        log.info(f"[{ex_name.upper()}:{coin}] 🔴 SELL @ ${price:.6f} | "
                 f"Net {sign}${pnl_net:.4f} | Fees -${fees:.4f} | Pool ${pool_usdt[ex_name]:.2f}")
        tg_sell(ex_name, symbol, qty, price, proceeds, buy_price, buy_spent, pnl_gross, pnl_pct, pool_usdt[ex_name], mode, fees=fees, pnl_net=pnl_net)

    else:
        try:
            qty = exchange.get_coin_balance(coin)
            if qty > 0:
                exchange.place_market_sell(symbol, qty)
                proceeds  = round(qty * price, 6)
                buy_spent = coin_buy_spent[ex_name][symbol]
                pnl_gross = proceeds - buy_spent
                fees      = round((buy_spent + proceeds) * fee_rate, 6)
                pnl_net   = round(pnl_gross - fees, 6)
                pnl_pct   = (pnl_gross / buy_spent * 100) if buy_spent > 0 else 0
                entry_time = get_entry_time(ex_name, symbol)
                with pool_locks[ex_name]:
                    coin_in_position[ex_name][symbol] = False
                on_sell(ex_name, symbol)
                with stats_lock:
                    trade_count += 1; total_pnl += pnl_net; total_fees += fees
                with daily_lock:
                    daily_trades.append({
                        "exchange": ex_name, "coin": coin,
                        "buy_price": coin_buy_price[ex_name][symbol], "sell_price": price,
                        "qty": qty, "spent": buy_spent, "proceeds": proceeds,
                        "pnl_gross": pnl_gross, "fees": fees, "pnl_net": pnl_net,
                        "time": datetime.now().strftime("%H:%M:%S"),
                    })
                record_trade({
                    "exchange": ex_name, "coin": coin, "symbol": symbol, "side": "spot_long",
                    "entry_time": entry_time.isoformat() if entry_time else None,
                    "exit_time": datetime.now().isoformat(),
                    "buy_price": coin_buy_price[ex_name][symbol], "sell_price": price, "qty": qty,
                    "spent": buy_spent, "proceeds": proceeds,
                    "pnl_gross": pnl_gross, "fees": fees, "pnl_net": pnl_net,
                    "mode": mode,
                })
                sign = "+" if pnl_net >= 0 else ""
                log.info(f"[{ex_name.upper()}:{coin}] 🔴 SELL Net {sign}${pnl_net:.4f}")
                tg_sell(ex_name, symbol, qty, price, proceeds, coin_buy_price[ex_name][symbol],
                        buy_spent, pnl_gross, pnl_pct, pool_usdt[ex_name], mode, fees=fees, pnl_net=pnl_net)
        except Exception as e:
            log.error(f"[{ex_name.upper()}:{coin}] Sell failed: {e}")
            tg_error(ex_name, symbol, f"Sell failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  SCALING MONITOR — rechecks pool every 30min, updates active coins + tier
# ══════════════════════════════════════════════════════════════════════════════

def scaling_monitor(mode, stop_event):
    """
    Every hour: re-ranks active coins using latest news scores.
    Every 30 min: checks if pool crossed a new tier threshold.
    Sends Telegram update when coin list changes significantly.
    """
    current_tiers     = {ex: get_current_tier(ex)["label"] for ex in EXCHANGES}
    last_coin_refresh = 0   # timestamp of last news-based re-rank

    while not stop_event.wait(timeout=30 * 60):
        now = time.time()

        for ex_name, exchange in EXCHANGES.items():
            try:
                new_tier  = get_current_tier(ex_name)
                old_label = current_tiers.get(ex_name, "")

                # ── Tier change ────────────────────────────────────────────
                if new_tier["label"] != old_label:
                    log.info(f"[{ex_name.upper()}] 📊 Tier change: {old_label} → {new_tier['label']}")
                    new_coins = get_top_coins(
                        ex_name, new_tier["max_coins"],
                        config.MIN_VOLUME_USDT, config.EXCLUDE_KEYWORDS,
                        use_news_scoring=getattr(config, "NEWS_COIN_RANKING", True),
            use_correlation_filter=getattr(config, "CORRELATION_AWARE_SELECTION", False)
                    )
                    with active_coins_lock:
                        active_coins[ex_name] = new_coins
                    current_tiers[ex_name] = new_tier["label"]
                    exchange.resubscribe_ws_feed(new_coins)
                    tg_scale(ex_name, old_label, new_tier["label"], pool_usdt[ex_name], new_coins)

                # ── Hourly news-based re-rank ──────────────────────────────
                elif now - last_coin_refresh >= 3600:
                    log.info(f"[{ex_name.upper()}] 🔄 Hourly coin re-rank using latest news...")
                    new_coins = get_top_coins(
                        ex_name, new_tier["max_coins"],
                        config.MIN_VOLUME_USDT, config.EXCLUDE_KEYWORDS,
                        use_news_scoring=getattr(config, "NEWS_COIN_RANKING", True),
            use_correlation_filter=getattr(config, "CORRELATION_AWARE_SELECTION", False)
                    )
                    with active_coins_lock:
                        old_coins = set(active_coins[ex_name])
                        new_set   = set(new_coins)
                        added     = new_set - old_coins
                        removed   = old_coins - new_set
                        active_coins[ex_name] = new_coins

                    if added or removed:
                        exchange.resubscribe_ws_feed(new_coins)
                        log.info(f"[{ex_name.upper()}] Coin list updated — "
                                f"added: {added}, removed: {removed}")
                        _tg_coin_rerank(ex_name, new_coins, added, removed)
                    else:
                        log.info(f"[{ex_name.upper()}] Coin list unchanged after re-rank")

            except Exception as e:
                log.warning(f"[SCALE MONITOR:{ex_name}] {e}")

        # Update refresh timestamp after processing all exchanges
        if now - last_coin_refresh >= 3600:
            last_coin_refresh = now


# ══════════════════════════════════════════════════════════════════════════════
#  EMERGENCY CIRCUIT BREAKER — 25%+ drawdown: close everything, pause bot
# ══════════════════════════════════════════════════════════════════════════════

def _trigger_emergency_close_all(ex_name: str, mode: str):
    """
    Fires once per emergency event (guarded so 10+ coin threads detecting
    the same emergency in the same poll cycle don't race each other).
    Closes every open position on this exchange at market and engages the
    manual pause flag so the bot stops attempting new buys entirely until
    a human explicitly sends /resume.
    """
    with _emergency_handled_lock:
        if ex_name in _emergency_handled:
            return   # already handled by another thread this cycle
        _emergency_handled.add(ex_name)

    log.error(f"[{ex_name.upper()}] 🚨 EMERGENCY DRAWDOWN — closing all positions and pausing bot")

    closed = []
    failed = []
    with active_coins_lock:
        symbols = list(active_coins.get(ex_name, []))

    for symbol in symbols:
        if coin_in_position.get(ex_name, {}).get(symbol):
            try:
                exchange = EXCHANGES[ex_name]
                price    = exchange.get_price(symbol)
                place_sell(ex_name, exchange, symbol, price, mode)
                closed.append(symbol)
            except Exception as e:
                failed.append(f"{symbol}: {e}")
                log.error(f"[{ex_name.upper()}] Emergency close failed for {symbol}: {e}")

    # Same emergency also closes any open futures short on this exchange —
    # a drawdown severe enough to trigger this circuit breaker means every
    # open position (spot or futures) should come off, not just spot.
    try:
        close_all_shorts({ex_name: EXCHANGES[ex_name]}, mode, config)
    except Exception as e:
        log.error(f"[{ex_name.upper()}] Emergency futures close failed: {e}")

    # Engage the same manual pause used by /pause — requires explicit /resume
    manual_pause.set()

    closed_str = ", ".join(closed) if closed else "none were open"
    failed_str = ("\n⚠️ Failed to close: " + ", ".join(failed)) if failed else ""

    tg_send(
        f"🚨 <b>EMERGENCY STOP — {ex_name.upper()}</b>\n━━━━━━━━━━━━━━━━\n"
        f"Drawdown exceeded the emergency threshold.\n"
        f"Positions closed: <b>{closed_str}</b>{failed_str}\n\n"
        f"⛔ Bot is now FULLY PAUSED — no new buys on any exchange.\n"
        f"This will NOT auto-resume. Review what happened, then send "
        f"<b>/resume</b> when you're ready to continue.\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "error"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PER-COIN THREAD
# ══════════════════════════════════════════════════════════════════════════════

def coin_worker(ex_name, exchange, symbol, mode, stop_event):
    coin = symbol.split("-")[0]
    tag  = f"{ex_name.upper()}:{coin}"

    try:
        exchange.get_price(symbol)
    except Exception:
        log.error(f"[{tag}] ❌ Not found on {ex_name} — skipping")
        tg_invalid_pair(ex_name, symbol)
        return

    log.info(f"[{tag}] ✅ Thread started")
    ai_mode = getattr(config, "AI_MODE", "alongside")

    # ── Determine pool type for this coin thread ───────────────────────────
    # Alternate coins between normal/aggressive based on position in active list
    # First 80% of coins = normal, last 20% = aggressive
    def get_my_pool_type():
        with active_coins_lock:
            coins = active_coins.get(ex_name, [])
        if not getattr(config, "DUAL_POOL_ENABLED", False) or not coins:
            return "normal"
        try:
            idx      = coins.index(symbol)
            split    = max(1, int(len(coins) * (1 - getattr(config, "AGGRESSIVE_POOL_PCT", 0.20))))
            return "aggressive" if idx >= split else "normal"
        except ValueError:
            return "normal"

    # ── Error suppression — only notify Telegram once per error type ───────
    last_error_msg  = ""
    error_count     = 0
    MAX_SILENT_ERRS = 3   # after 3 identical errors, go quiet until it clears

    # ── Minimum candles needed ─────────────────────────────────────────────
    MIN_CANDLES = config.RSI_PERIOD + config.MA_PERIOD + 5

    while not stop_event.is_set():

        # Check if coin still in active list — BUT NEVER exit while holding
        # an open position. The hourly news re-rank can drop a coin from
        # active_coins at any time; if we exited here unconditionally, an
        # open position would be silently abandoned with NO stop-loss,
        # take-profit, or any exit logic ever running against it again.
        # A held position must always keep being monitored until it's
        # actually closed, regardless of whether the coin is still
        # "in favour" for NEW buys.
        with active_coins_lock:
            still_active = symbol in active_coins[ex_name]

        is_holding = coin_in_position[ex_name].get(symbol, False)

        if not still_active and not is_holding:
            log.info(f"[{tag}] No longer in active list and no open position — thread exiting")
            return
        elif not still_active and is_holding:
            log.info(f"[{tag}] ⚠️ Dropped from active list by re-rank but STILL HOLDING — "
                    f"continuing to monitor for exit (stop-loss/take-profit/sell signal)")

        try:
            # ── Rate limit: stagger API calls across threads ───────────────
            time.sleep(0.5)

            # ── PRICE-ONLY SAFETY CHECK — runs BEFORE any data-quality gate ──
            # This is the critical fix: a held position's stop-loss/take-
            # profit must NEVER be skipped just because candle data for RSI/MA
            # is missing, thin, or contains NaNs. A coin with bad candle data
            # is MORE risky to leave unmonitored, not a reason to stop
            # checking it. This check only needs the current price — it does
            # NOT depend on RSI, MA, or any candle history — so it runs and
            # can close the position even when everything below it would
            # otherwise be skipped for the rest of this cycle.
            is_holding_now = coin_in_position[ex_name].get(symbol, False)
            if is_holding_now:
                try:
                    price_source = getattr(config, "PRICE_SOURCE", "kucoin")
                    if price_source != "kucoin":
                        safety_price = get_price_cached(symbol, fallback_fn=lambda s: exchange.get_price(s))
                    else:
                        safety_price = exchange.get_price(symbol)

                    safety_buy_price = coin_buy_price[ex_name].get(symbol)
                    if safety_buy_price:
                        should_exit, exit_reason = check_position(
                            ex_name, symbol, safety_price, safety_buy_price)
                        if should_exit:
                            pnl = round((safety_price - safety_buy_price) / safety_buy_price
                                       * coin_buy_spent[ex_name][symbol], 4)
                            log.warning(f"[{tag}] 🛡️ PRICE-SAFETY EXIT (bypassing any data "
                                       f"quality issue): {exit_reason}")
                            tg_risk_exit(ex_name, symbol, exit_reason, safety_price,
                                        safety_buy_price, pnl, mode)
                            place_sell(ex_name, exchange, symbol, safety_price, mode)
                            stop_event.wait(timeout=config.POLL_SECONDS)
                            continue
                except Exception as e:
                    log.warning(f"[{tag}] Price-safety check itself failed: {e} "
                               f"— will retry next cycle, position remains open")

            df = exchange.get_candles(symbol, config.CANDLE_INTERVAL)

            # ── Validate enough candle data exists ────────────────────────
            if df is None or len(df) < MIN_CANDLES:
                log.warning(f"[{tag}] Not enough candle data ({len(df) if df is not None else 0} rows, need {MIN_CANDLES}) — skipping RSI/MA logic this cycle (stop-loss/take-profit still checked every cycle regardless)")
                stop_event.wait(timeout=config.POLL_SECONDS)
                continue

            # Check for NaN in close prices
            if df["close"].isnull().any() or (df["close"] == 0).any():
                log.warning(f"[{tag}] Invalid candle data (NaN or zeros) — skipping RSI/MA logic this cycle (stop-loss/take-profit still checked every cycle regardless)")
                stop_event.wait(timeout=config.POLL_SECONDS)
                continue

            # ── Use price feed cache (reduces KuCoin API calls by ~90%) ──
            price_source = getattr(config, "PRICE_SOURCE", "kucoin")
            if price_source != "kucoin":
                price = get_price_cached(symbol, fallback_fn=lambda s: exchange.get_price(s))
            else:
                price = exchange.get_price(symbol)
            rsi   = calc_rsi(df["close"], config.RSI_PERIOD)
            ma    = calc_ma(df["close"],  config.MA_PERIOD)

            # Validate RSI and MA computed correctly
            if rsi != rsi or ma != ma:   # NaN check
                log.warning(f"[{tag}] RSI/MA returned NaN — skipping RSI/MA logic this cycle (stop-loss/take-profit still checked every cycle regardless)")
                stop_event.wait(timeout=config.POLL_SECONDS)
                continue

            holding      = coin_in_position[ex_name][symbol]
            candles_list = df["close"].tolist()

            # Reset error tracking on success
            last_error_msg = ""
            error_count    = 0

            log.info(f"[{tag}] ${price:.6f}  RSI={rsi}  MA={ma:.6f}  {'HOLDING' if holding else 'waiting'}")

            # ── Manual pause check (from /pause command) ──────────────────
            if manual_pause.is_set() and not holding:
                log.info(f"[{tag}] ⛔ Manually paused — skipping buy signals")
                stop_event.wait(timeout=config.POLL_SECONDS)
                continue

            # ── Drawdown check — tiered circuit breaker ───────────────────
            dd_status = check_drawdown(ex_name, pool_usdt[ex_name])

            if dd_status["close_all"]:
                _trigger_emergency_close_all(ex_name, mode)
                stop_event.wait(timeout=config.POLL_SECONDS)
                continue

            if not holding and dd_status["paused"]:
                log.info(f"[{tag}] ⛔ Buys paused — drawdown level: {dd_status['level']}")
                stop_event.wait(timeout=config.POLL_SECONDS)
                continue

            if dd_status["level"] == "normal":
                with _emergency_handled_lock:
                    _emergency_handled.discard(ex_name)

            # ── Risk exit checks on open position ─────────────────────────
            if holding:
                buy_price = coin_buy_price[ex_name][symbol]
                should_exit, exit_reason = check_position(ex_name, symbol, price, buy_price)
                if should_exit:
                    pnl = round((price - buy_price) / buy_price * coin_buy_spent[ex_name][symbol], 4)
                    tg_risk_exit(ex_name, symbol, exit_reason, price, buy_price, pnl, mode)
                    place_sell(ex_name, exchange, symbol, price, mode)
                    stop_event.wait(timeout=config.POLL_SECONDS)
                    continue

            # ── ATR volatility sizing ─────────────────────────────────────
            atr = 0.0
            if getattr(config, "VOLATILITY_SIZING", False) and "high" in df.columns:
                try:
                    atr = calc_atr(
                        df["high"].tolist(), df["low"].tolist(),
                        df["close"].tolist(),
                        getattr(config, "VOLATILITY_ATR_PERIOD", 14)
                    )
                except Exception:
                    pass

            # ── BUY ───────────────────────────────────────────────────────
            if not holding:
                pool_type  = get_my_pool_type()
                params     = get_pool_params(pool_type)
                pool_label = f"[{pool_type.upper()}]"

                # ── Strategy router — picks mean-reversion or breakout based
                # on the CURRENT regime. Only the two strongest trending
                # regimes use breakout logic; everything else keeps the
                # existing RSI+MA mean-reversion behaviour unchanged. This
                # is the only thing that changes — every filter, risk
                # control, and approval step downstream is identical
                # regardless of which strategy produced the signal.
                from breakout_strategy import get_active_strategy, evaluate_breakout_buy, evaluate_dipbuy_buy
                regime_name = "SIDEWAYS"
                try:
                    from adaptive_intelligence import get_intelligence
                    intel = get_intelligence()
                    if intel:
                        regime_name = intel.current_regime
                except Exception:
                    pass

                active_strategy = get_active_strategy(regime_name)

                if active_strategy == "breakout":
                    breakout_result = evaluate_breakout_buy(df, donchian_period=20, pool_type=pool_type)
                    rsi_buy = breakout_result["signal"]
                    if rsi_buy:
                        log.info(f"[{tag}] {pool_label} 📈 BREAKOUT mode ({regime_name}): "
                                f"{breakout_result['reason']}")
                elif active_strategy == "dip_buy":
                    # Use the regime's OWN calibrated RSI threshold (e.g.
                    # BEAR_WEAK's 30), not the static normal/aggressive
                    # params["rsi_buy"] (35/42) — those were tuned for
                    # mean-reversion in a flat/bullish market, not for
                    # picking bottoms in a weak downtrend.
                    from adaptive_intelligence import REGIME_STRATEGIES
                    regime_rsi_buy = REGIME_STRATEGIES.get(regime_name, {}).get("rsi_buy", 30)
                    dipbuy_result = evaluate_dipbuy_buy(df, rsi, rsi_buy_threshold=regime_rsi_buy,
                                                        pool_type=pool_type)
                    rsi_buy = dipbuy_result["signal"]
                    if rsi_buy:
                        log.info(f"[{tag}] {pool_label} 🔻 DIP-BUY mode ({regime_name}): "
                                f"{dipbuy_result['reason']}")
                else:
                    rsi_buy = rsi < params["rsi_buy"] and price > ma

                if rsi_buy:
                    # Run adaptive strategy engine (filters + calibrated TP/SL)
                    news_sent = "unknown"
                    ai_result = None

                    # Get AI decision first if enabled
                    if config.AI_ENABLED and ai_mode != "full":
                        ai_result = ai_analyst.analyse(
                            symbol, "BUY", price, rsi, ma, candles_list, True, ai_mode)
                        news_sent = ai_result.get("news_sentiment", "unknown")

                    engine_result = evaluate_signal(
                        action="BUY", symbol=symbol, price=price,
                        rsi=rsi, ma=ma, df=df, exchange=exchange,
                        ex_name=ex_name, pool=pool_usdt[ex_name],
                        news_sentiment=news_sent,
                        pool_type=pool_type, config=config,
                    )

                    # Apply calibrated TP/SL from engine — breakout trades
                    # use a wider ATR-based stop instead of the engine's
                    # mean-reversion calibration, since a tight stop would
                    # exit a genuine trend on normal volatility.
                    import config as _cfg
                    if active_strategy == "breakout":
                        from breakout_strategy import atr_trailing_stop
                        atr_stop_price = atr_trailing_stop(df, price, "long", 14, 2.5)
                        atr_stop_pct   = max(0.01, (price - atr_stop_price) / price)
                        sl_ceiling     = getattr(config, "MAX_STOP_LOSS_PCT", 0.04)
                        _cfg.STOP_LOSS_PCT   = min(atr_stop_pct, sl_ceiling)
                        _cfg.TAKE_PROFIT_PCT = engine_result["take_profit"]
                    else:
                        _cfg.STOP_LOSS_PCT   = engine_result["stop_loss"]
                        _cfg.TAKE_PROFIT_PCT = engine_result["take_profit"]

                    if engine_result["approved"]:
                        if ai_result and not ai_result.get("approved", True):
                            tg_ai_veto(ex_name, symbol, "BUY",
                                      ai_result["confidence"],
                                      ai_result["reason"],
                                      news_sent)
                        else:
                            # ── Hybrid gate: is this trade actually a better use
                            # of this capital than just staking it? See
                            # hybrid_allocator.py. A no-op unless both
                            # HYBRID_OPTIMIZER_ENABLED and STAKING_ENABLED are on.
                            hybrid = evaluate_hybrid_gate(
                                ex_name, exchange, symbol, "spot_long",
                                engine_result["take_profit"], _cfg.STOP_LOSS_PCT,
                                getattr(config, "MAX_HOLD_HOURS", 48) or 24,
                                mode, config,
                            )
                            if not hybrid["proceed"]:
                                log.info(f"[{tag}] {pool_label} ⛔ Hybrid gate — staking "
                                        f"beats this trade's edge: {hybrid['reason']}")
                            else:
                                if ai_result:
                                    tg_ai_approve(ex_name, symbol, "BUY",
                                                 ai_result["confidence"],
                                                 ai_result["reason"], news_sent)
                                strategy_label = {"breakout": "breakout", "dip_buy": "dip-buy"}.get(active_strategy, "mean-rev")
                                log.info(f"[{tag}] {pool_label} 🟢 BUY "
                                        f"({strategy_label}) "
                                        f"TP={engine_result['take_profit']:.1%} "
                                        f"SL={_cfg.STOP_LOSS_PCT:.1%} "
                                        f"pos=${engine_result['position_size']:.2f}")
                                place_buy(ex_name, exchange, symbol, price, mode,
                                         pool_type=pool_type, strategy_type=active_strategy)
                    else:
                        log.info(f"[{tag}] {pool_label} ⛔ Engine filtered: "
                                f"{engine_result['filters_failed']}")
                elif ai_mode == "full" and config.AI_ENABLED:
                    ai_result = ai_analyst.analyse(
                        symbol, "BUY", price, rsi, ma, candles_list, False, ai_mode)
                    if ai_result.get("approved"):
                        place_buy(ex_name, exchange, symbol, price, mode,
                                 pool_type=pool_type)
                else:
                    if active_strategy == "breakout":
                        log.info(f"[{tag}] {pool_label} ⏸  No breakout buy signal (RSI={rsi:.1f})")
                    elif active_strategy == "dip_buy":
                        # dipbuy_result/regime_rsi_buy were computed above in
                        # the router block when active_strategy == "dip_buy",
                        # so they're guaranteed to exist here.
                        log.info(f"[{tag}] {pool_label} ⏸  No dip-buy signal "
                                f"({dipbuy_result['reason']})")
                    elif rsi >= params["rsi_buy"]:
                        log.info(f"[{tag}] {pool_label} ⏸  No RSI buy signal "
                                f"(RSI={rsi:.1f}, need <{params['rsi_buy']})")
                    else:
                        log.info(f"[{tag}] {pool_label} ⏸  No RSI buy signal "
                                f"(RSI={rsi:.1f} is oversold, but price ${price:.6f} is "
                                f"below MA ${ma:.6f} — mean-reversion strategy waits for "
                                f"an uptrend dip, not a downtrend)")

            # ── SELL ──────────────────────────────────────────────────────
            else:
                pool_type      = coin_pool_type[ex_name].get(symbol, "normal")
                params         = get_pool_params(pool_type)
                pool_label     = f"[{pool_type.upper()}]"

                # A position exits using whichever strategy OPENED it, not
                # whatever the current regime happens to be — the regime
                # may have already shifted again since entry, but the exit
                # rule should stay consistent with the entry rule that was
                # actually used.
                position_strategy = coin_strategy_type[ex_name].get(symbol, "mean_reversion")

                if position_strategy == "breakout":
                    buy_price = coin_buy_price[ex_name].get(symbol, price)
                    from breakout_strategy import evaluate_breakout_sell
                    breakout_exit = evaluate_breakout_sell(df, buy_price, donchian_period=20,
                                                           pool_type=pool_type)
                    rsi_sell = breakout_exit["signal"]
                    if rsi_sell:
                        log.info(f"[{tag}] {pool_label} 📉 BREAKOUT exit "
                                f"({breakout_exit['exit_type']}): {breakout_exit['reason']}")
                elif position_strategy == "dip_buy":
                    buy_price = coin_buy_price[ex_name].get(symbol, price)
                    from breakout_strategy import evaluate_dipbuy_sell
                    from adaptive_intelligence import REGIME_STRATEGIES
                    bw = REGIME_STRATEGIES.get("BEAR_WEAK", {})
                    dipbuy_exit = evaluate_dipbuy_sell(
                        df, rsi, buy_price,
                        rsi_sell_threshold=bw.get("rsi_sell", 58),
                        stop_loss_pct=bw.get("stop_loss_pct", 0.06),
                    )
                    rsi_sell = dipbuy_exit["signal"]
                    if rsi_sell:
                        log.info(f"[{tag}] {pool_label} 📉 DIP-BUY exit "
                                f"({dipbuy_exit['exit_type']}): {dipbuy_exit['reason']}")
                else:
                    rsi_sell = rsi > params["rsi_sell"] and price < ma

                if rsi_sell or ai_mode == "full":
                    news_sent = "unknown"
                    if config.AI_ENABLED:
                        ai_result = ai_analyst.analyse(
                            symbol, "SELL", price, rsi, ma, candles_list,
                            rsi_sell, ai_mode)
                        news_sent = ai_result.get("news_sentiment", "unknown")
                        if ai_result.get("approved", True):
                            tg_ai_approve(ex_name, symbol, "SELL",
                                         ai_result["confidence"],
                                         ai_result["reason"], news_sent)
                            place_sell(ex_name, exchange, symbol, price, mode)
                        else:
                            tg_ai_veto(ex_name, symbol, "SELL",
                                      ai_result["confidence"],
                                      ai_result["reason"], news_sent)
                    elif rsi_sell:
                        if position_strategy == "breakout":
                            strat_tag = "breakout"
                        elif position_strategy == "dip_buy":
                            strat_tag = "dip-buy"
                        else:
                            strat_tag = f"RSI={rsi:.1f}>{params['rsi_sell']}"
                        log.info(f"[{tag}] {pool_label} 🔴 SELL ({strat_tag})")
                        place_sell(ex_name, exchange, symbol, price, mode)
                else:
                    log.info(f"[{tag}] {pool_label} ⏸  Holding")

            # ── FUTURES SHORT — independent of the spot BUY/SELL logic
            # above (see futures_manager.py). Always manages any already-
            # open short first (TP/SL/max-hold/signal-flip); only
            # considers opening a NEW short when spot isn't already long
            # this coin, using the exact same bearish RSI+MA read that
            # would trigger a spot SELL — spot and futures never take
            # opposing bets on the same coin at the same time. No-ops
            # instantly (no network calls) on any exchange/coin this
            # isn't enabled for.
            try:
                manage_open_short(ex_name, exchange, symbol, price, rsi, ma, mode, config)
                if not holding and not has_open_short(ex_name, symbol):
                    if rsi > params["rsi_sell"] and price < ma:
                        evaluate_and_open_short(ex_name, exchange, symbol, price, rsi, ma,
                                                pool_usdt[ex_name], mode, config)
            except Exception as e:
                log.debug(f"[{tag}] Futures step skipped: {e}")

        except Exception as e:
            err_str = str(e)
            log.error(f"[{tag}] Error: {err_str}")

            # ── Rate limit: back off longer and don't spam Telegram ────────
            if "429" in err_str or "Too many requests" in err_str.lower() or "rate limit" in err_str.lower():
                backoff = 30
                log.warning(f"[{tag}] Rate limited — backing off {backoff}s")
                stop_event.wait(timeout=backoff)
                continue

            # ── Categorise error for suppression matching ───────────────────
            # Exact string matching fails for connection/timeout errors since
            # they embed socket IDs, ports, or timestamps that differ each time
            # even when the underlying cause is identical. Match by category
            # instead so repeated KuCoin connection drops are correctly grouped.
            err_lower = err_str.lower()
            if "read timed out" in err_lower or "timeout" in err_lower:
                err_category = f"{type(e).__name__}:timeout"
            elif "connection aborted" in err_lower or "connection reset" in err_lower or "connectionreset" in err_lower:
                err_category = f"{type(e).__name__}:connection_reset"
            elif "connection" in err_lower:
                err_category = f"{type(e).__name__}:connection_other"
            else:
                err_category = err_str   # fall back to exact match for anything else

            # ── Suppress repeated errors of the same category from spamming ─
            if err_category == last_error_msg:
                error_count += 1
                if error_count <= MAX_SILENT_ERRS:
                    tg_error(ex_name, symbol, err_str)
                elif error_count == MAX_SILENT_ERRS + 1:
                    tg_error(ex_name, symbol, f"Repeated error suppressed (seen {error_count}x): {err_str}")
                # beyond that, stay silent until error clears
            else:
                last_error_msg = err_category
                error_count    = 1
                tg_error(ex_name, symbol, err_str)

        stop_event.wait(timeout=config.POLL_SECONDS)


# ══════════════════════════════════════════════════════════════════════════════
#  HEARTBEAT + DAILY REPORT THREADS
# ══════════════════════════════════════════════════════════════════════════════

def liveness_pinger(mode, stop_event):
    """
    Writes a liveness file every 2 minutes — much tighter than the 30-minute
    heartbeat. This is the actual signal an external watchdog process
    checks; if this file stops updating, the bot has frozen or crashed
    even if individual threads are still technically alive but stuck.
    """
    from pathlib import Path
    import json as _json

    liveness_path = Path("logs/liveness.json")
    liveness_path.parent.mkdir(exist_ok=True)

    def _write():
        try:
            with open(liveness_path, "w") as f:
                _json.dump({
                    "last_ping":    datetime.now().isoformat(),
                    "mode":         mode,
                    "trade_count":  trade_count,
                    "total_pnl":    total_pnl,
                    "pool_usdt":    dict(pool_usdt),
                    "pid":          os.getpid(),
                    "active_coins": {ex: len(coins) for ex, coins in active_coins.items()},
                }, f)
        except Exception as e:
            log.warning(f"[LIVENESS] Could not write liveness file: {e}")

    _write()   # write immediately on startup — a watchdog should be able to
               # tell "just launched" apart from "crashed with no signal ever"
    while not stop_event.wait(timeout=120):
        _write()


def heartbeat_worker(mode, stop_event):
    while not stop_event.wait(timeout=30 * 60):
        if not getattr(config, "HEARTBEAT_VISIBLE_BY_DEFAULT", True):
            # Scheduled heartbeat silenced via config — /heartbeat in
            # telegram_commands.py still works on demand regardless of
            # this flag, since it's a separate code path that always sends.
            continue
        try:
            open_pos = {}
            tiers    = {}
            for ex in EXCHANGES:
                # get_current_tier() returns a direct reference into the
                # shared config.SCALING_TIERS list — exchanges on the same
                # tier (e.g. all three on "Starter") get back the SAME dict
                # object. Copy it before mutating, otherwise writing
                # "coins" here overwrites it for every exchange sharing
                # that tier, and the last exchange processed wins for all
                # of them (this was the cause of every exchange showing
                # the same coin count in the heartbeat).
                tier = dict(get_current_tier(ex))
                with active_coins_lock:
                    # get_current_tier() only returns the tier's CEILING
                    # (max_coins) — it has no concept of how many coins are
                    # actually being watched right now. Reading the real,
                    # live active_coins length here (same source /heartbeat
                    # already uses correctly) is what actually fixes the
                    # discrepancy users would otherwise see between the
                    # on-demand and scheduled heartbeat: e.g. Kraken showing
                    # "15 coins" (the ceiling) when only 11 qualifying pairs
                    # actually exist right now.
                    tier["coins"] = len(active_coins.get(ex, []))
                tiers[ex] = tier
            for ex_name, exchange in EXCHANGES.items():
                for sym, holding in coin_in_position[ex_name].items():
                    if holding:
                        try:
                            cur = exchange.get_price(sym)
                            upnl = round(coin_holdings[ex_name][sym] * cur - coin_buy_spent[ex_name][sym], 4)
                            open_pos[f"{sym} [{ex_name.upper()}]"] = upnl
                        except Exception:
                            pass
            tg_heartbeat(pool_usdt, open_pos, trade_count, total_pnl, tiers, mode)
        except Exception as e:
            log.warning(f"[HEARTBEAT] {e}")


def daily_report_worker(mode, stop_event):
    global daily_trades, daily_reset_date
    while not stop_event.is_set():
        now      = datetime.now()
        tomorrow = now.replace(hour=0, minute=0, second=5, microsecond=0)
        if tomorrow <= now:
            tomorrow += timedelta(days=1)
        secs  = (tomorrow - now).total_seconds()
        slept = 0
        while slept < secs and not stop_event.is_set():
            stop_event.wait(timeout=min(60, secs - slept))
            slept += 60
        if stop_event.is_set():
            break
        with daily_lock:
            report_date      = daily_reset_date.strftime("%Y-%m-%d")
            snap             = list(daily_trades)
            daily_trades     = []
            daily_reset_date = datetime.now().date()

        # Reset monthly trades at start of new month
        current_month = datetime.now().strftime("%Y-%m")
        with monthly_lock:
            global monthly_reset_month
            if current_month != monthly_reset_month:
                monthly_trades.clear()
                monthly_reset_month = current_month
        tiers = {ex: get_current_tier(ex) for ex in EXCHANGES}
        log.info(f"[DAILY] Report for {report_date} ({len(snap)} trades)")
        tg_daily_report(report_date, snap, pool_usdt, tiers, mode)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  STARTUP SELF-CHECK — find orphaned positions after a crash/power failure
# ══════════════════════════════════════════════════════════════════════════════

# Anything below this USDT value is treated as exchange dust (residual
# fractions left over from rounding, fee deductions, etc.) rather than a
# real orphaned position worth tracking or alerting on.
ORPHAN_DUST_THRESHOLD_USDT = 1.0


def startup_balance_check(mode: str) -> dict:
    """
    Runs once at startup, AFTER coin discovery has populated active_coins,
    BEFORE any coin_worker threads start. Purpose: in live mode, the bot's
    notion of "what positions are open" lives entirely in memory
    (coin_in_position, coin_buy_price, coin_buy_spent). If the bot crashes
    or the machine loses power, that memory is gone on restart — but
    anything the bot had bought is still sitting on the exchange, with
    nothing watching it: no stop-loss, no take-profit, no /status entry,
    nothing. This function exists specifically to catch that.

    HOW IT WORKS:
      1. For every exchange that implements get_all_balances() (currently
         KuCoin — see exchanges.py), fetch every nonzero balance.
      2. For each balance, skip USDT itself, skip anything below
         ORPHAN_DUST_THRESHOLD_USDT once converted to a USD value, and
         skip anything that's about to be in active_coins anyway (which
         already gets a worker thread and is tracked normally).
      3. Anything left over is an ORPHAN — a coin the exchange shows a
         real balance for that the bot was not about to track. These get:
           a. ADOPTED into coin_in_position / coin_holdings so a worker
              thread will actually manage them going forward (apply
              stop-loss/take-profit, respond to /sell, show up in
              /status) — using the CURRENT market price as an estimated
              entry price, since the real entry price was lost along
              with the rest of the crashed process's memory. This is
              clearly an estimate, not a fact — see the loud disclosure
              in the Telegram alert and the "_estimated_entry" flag set
              on every adopted position, which /status and /diag should
              both surface so this is never mistaken for a known number.
           b. Reported loudly via Telegram and the return value below, so
              you know this happened and can verify it against your own
              records if the estimated entry price matters to you.

      In PAPER mode this is a no-op (paper positions only ever exist in
      memory to begin with — there's no real exchange balance to recover).

    Returns:
        {
          "checked_exchanges": [...],
          "skipped_exchanges":  [(ex_name, reason), ...],
          "orphans_found":      [(ex_name, coin, balance, est_price, est_usd_value), ...],
        }
    """
    result = {"checked_exchanges": [], "skipped_exchanges": [], "orphans_found": []}

    if mode == "paper":
        log.info("[SELFCHECK] Paper mode — no real exchange balances to reconcile, skipping")
        return result

    log.info("[SELFCHECK] Running startup self-check for orphaned positions...")

    for ex_name, exchange in EXCHANGES.items():
        with active_coins_lock:
            expected_symbols = set(active_coins.get(ex_name, []))
        expected_coins = {sym.split("-")[0] for sym in expected_symbols}

        try:
            balances = exchange.get_all_balances()
        except NotImplementedError:
            reason = (f"get_all_balances() not implemented for '{ex_name}' — "
                     f"cannot verify no positions are orphaned on this exchange")
            log.warning(f"[SELFCHECK] ⚠️ {reason}")
            result["skipped_exchanges"].append((ex_name, reason))
            continue
        except Exception as e:
            reason = f"Balance check failed: {e}"
            log.error(f"[SELFCHECK] ⚠️ {ex_name}: {reason}")
            result["skipped_exchanges"].append((ex_name, reason))
            continue

        result["checked_exchanges"].append(ex_name)

        for coin, balance in balances.items():
            if coin in ("USDT", "USD", "USDC") or balance <= 0:
                continue
            if coin in expected_coins:
                continue   # already about to be tracked normally — not an orphan

            symbol = f"{coin}-USDT"
            try:
                price = exchange.get_price(symbol)
            except Exception as e:
                log.warning(f"[SELFCHECK] {ex_name}:{coin} — found balance "
                           f"{balance} but couldn't price it ({e}); reporting "
                           f"without auto-adopting since we can't compute a "
                           f"safe estimated entry value")
                result["orphans_found"].append((ex_name, coin, balance, None, None))
                continue

            usd_value = balance * price
            if usd_value < ORPHAN_DUST_THRESHOLD_USDT:
                log.info(f"[SELFCHECK] {ex_name}:{coin} balance {balance} "
                        f"(${usd_value:.4f}) — below dust threshold, ignoring")
                continue

            # ── Adopt: make this a real tracked position going forward ─────
            with pool_locks[ex_name]:
                coin_holdings[ex_name][symbol]      = balance
                coin_in_position[ex_name][symbol]   = True
                coin_buy_price[ex_name][symbol]     = price   # ESTIMATE — see disclosure below
                coin_buy_spent[ex_name][symbol]     = usd_value
                coin_pool_type[ex_name][symbol]     = "normal"
                coin_strategy_type[ex_name][symbol] = "orphan_recovery"

            with active_coins_lock:
                if symbol not in active_coins[ex_name]:
                    active_coins[ex_name].append(symbol)

            on_buy(ex_name, symbol, price)   # register with risk_manager same as a normal buy

            log.warning(f"[SELFCHECK] 🔶 ORPHAN ADOPTED — {ex_name}:{coin} "
                       f"balance {balance:.8f} (~${usd_value:.2f} @ estimated "
                       f"entry ${price:.6f}) — now actively tracked")
            result["orphans_found"].append((ex_name, coin, balance, price, usd_value))

    if result["orphans_found"] or result["skipped_exchanges"]:
        _tg_orphan_report(result, mode)
    else:
        log.info("[SELFCHECK] ✅ No orphaned positions found — all exchange "
                "balances accounted for")

    return result


def _tg_orphan_report(result: dict, mode: str):
    """Telegram alert summarizing the startup self-check — only sent when
    there's something worth flagging (orphans found, or an exchange that
    couldn't be checked at all)."""
    lines = ["🔍 <b>Startup Self-Check</b>\n━━━━━━━━━━━━━━━━"]

    priced_orphans   = [o for o in result["orphans_found"] if o[3] is not None]
    unpriced_orphans = [o for o in result["orphans_found"] if o[3] is None]

    if priced_orphans:
        lines.append("⚠️ <b>Orphaned positions found and ADOPTED:</b>")
        for ex_name, coin, balance, price, usd_value in priced_orphans:
            lines.append(
                f"  🔶 {coin} [{ex_name.upper()}]: {balance:.6f} (~${usd_value:.2f})\n"
                f"      Estimated entry: <b>${price:.6f}</b> (current market price — "
                f"the real buy price was lost when the bot's memory reset; this "
                f"is an estimate, not a fact)"
            )
        lines.append(
            "\nThese are now actively tracked — stop-loss/take-profit will "
            "apply going forward, and they'll show up in /status. If you "
            "know the real entry price for any of these, it's worth noting "
            "that the P&L on the eventual sell will be measured against the "
            "ESTIMATED price above, not your real one."
        )

    if unpriced_orphans:
        lines.append("\n⚠️ <b>Balances found but could NOT be priced/adopted:</b>")
        for ex_name, coin, balance, _, _ in unpriced_orphans:
            lines.append(f"  ❓ {coin} [{ex_name.upper()}]: {balance:.6f} — check this manually")

    if result["skipped_exchanges"]:
        lines.append("\n⚠️ <b>Exchanges that could NOT be checked:</b>")
        for ex_name, reason in result["skipped_exchanges"]:
            lines.append(f"  • {ex_name.upper()}: {reason}")
        lines.append(
            "\nNo guarantee these exchanges don't have an orphaned position "
            "right now — verify manually until get_all_balances() is added "
            "for them in exchanges.py."
        )

    lines.append(f"\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    tg_send("\n".join(lines), "general")


def run():
    # symbols is now auto-discovered per exchange — no static list needed
    mode     = "paper" if config.PAPER_TRADING else "live"
    mode_tag = "📄 PAPER" if config.PAPER_TRADING else "💰 LIVE"

    if not EXCHANGES:
        log.error("No exchanges configured! Fill in credentials in config.py")
        return

    log.info("=" * 60)
    log.info(f"  Multi-Exchange Bot  |  {mode_tag} MODE")
    log.info(f"  Exchanges : {list(EXCHANGES.keys())}")
    reserve   = getattr(config, "LISTING_RESERVE_USDT", 0.0)
    tradeable = config.PAPER_STARTING_USDT - reserve
    log.info(f"  Pool      : ${config.PAPER_STARTING_USDT:.2f} USDT total")
    log.info(f"             ${tradeable:.2f} tradeable + ${reserve:.2f} listing reserve")
    log.info(f"  Telegram  : {'ENABLED' if config.TELEGRAM_ENABLED else 'disabled'}")
    log.info(f"  AI        : {'ENABLED — ' + config.AI_MODE if config.AI_ENABLED else 'disabled'}")
    log.info("=" * 60)

    # ── Discover coins per exchange based on starting tier ─────────────────
    tiers_info = {}
    for ex_name, exchange in EXCHANGES.items():
        tier      = get_current_tier(ex_name)
        log.info(f"[{ex_name.upper()}] Pool ${pool_usdt[ex_name]:.2f} → {tier['label']} tier "
                 f"(${tier['max_per_trade']}/trade, up to {tier['max_coins']} coins)")

        coins = get_top_coins(
            ex_name, tier["max_coins"],
            config.MIN_VOLUME_USDT, config.EXCLUDE_KEYWORDS,
            use_news_scoring=getattr(config, "NEWS_COIN_RANKING", True),
            use_correlation_filter=getattr(config, "CORRELATION_AWARE_SELECTION", False)
        )
        with active_coins_lock:
            active_coins[ex_name] = coins

        tiers_info[ex_name] = {
            "label":       tier["label"],
            "max_per_trade": tier["max_per_trade"],
            "max_coins":   tier["max_coins"],
            "coins":       len(coins),
        }

    # ── Self-check: any coin on the exchange the bot isn't about to watch? ──
    # Runs after discovery (so active_coins is populated to diff against)
    # but before tg_start/coin workers, so any adopted orphan is already
    # tracked and gets a worker thread like any normal position.
    startup_balance_check(mode)

    # ── Attach WebSocket price feeds (KuCoin/Binance/Kraken/Bybit/OKX/Gate.io) ──
    # Must happen after active_coins is populated so each feed subscribes to
    # the exact symbols the bot will trade on that exchange.
    for ex_name, exchange in EXCHANGES.items():
        syms = list(active_coins.get(ex_name, []))
        if syms:
            exchange.attach_ws_feed(syms)

    tg_start(list(EXCHANGES.keys()), mode, tiers_info)

    stop_event = threading.Event()
    threads    = []

    # Background workers
    threading.Thread(target=liveness_pinger,     args=(mode, stop_event), daemon=True).start()
    threading.Thread(target=heartbeat_worker,    args=(mode, stop_event), daemon=True).start()
    threading.Thread(target=daily_report_worker, args=(mode, stop_event), daemon=True).start()
    threading.Thread(target=scaling_monitor,     args=(mode, stop_event), daemon=True).start()

    if getattr(config, "STAKING_ENABLED", False):
        threading.Thread(
            target=staking_worker,
            args=(EXCHANGES, pool_usdt, pool_locks, config, mode, stop_event),
            daemon=True, name="staking_worker",
        ).start()
        log.info(f"  Staking        : ACTIVE — idle capital checked every "
                f"{getattr(config, 'STAKING_CHECK_INTERVAL_SECS', 1800)}s "
                f"(min APR {getattr(config, 'STAKING_MIN_APR', 0.03):.1%})")

    if getattr(config, "FUTURES_ENABLED", False):
        futures_exchanges = [ex for ex in EXCHANGES
                             if ex in getattr(config, "FUTURES_SUPPORTED_EXCHANGES", set())
                             and config.EXCHANGES.get(ex, {}).get("futures_enabled", False)]
        log.info(f"  Futures        : ACTIVE (1x, shorts only) — {futures_exchanges or 'none opted in yet'}")

    if getattr(config, "AUTO_UPDATE_ENABLED", False):
        from auto_updater import update_check_worker
        threading.Thread(
            target=update_check_worker,
            args=(config, stop_event, tg_send if config.TELEGRAM_ENABLED else None),
            daemon=True, name="auto_updater",
        ).start()
        log.info(f"  Auto-update     : ACTIVE — checking {config.AUTO_UPDATE_REMOTE}/"
                f"{config.AUTO_UPDATE_BRANCH} every {config.AUTO_UPDATE_CHECK_INTERVAL_SECS}s")

    # Price feed — reduces KuCoin API calls by ~90%
    all_symbols   = [s for coins in active_coins.values() for s in coins]
    price_source  = getattr(config, "PRICE_SOURCE", "coingecko")
    if price_source != "kucoin":
        threading.Thread(
            target=price_updater_worker,
            args=(all_symbols, price_source, stop_event),
            daemon=True, name="price_feed"
        ).start()
        log.info(f"  Price feed      : {price_source.upper()} (reduces KuCoin rate limit load)")

    # Deposit monitor + auto-converter
    dep_mon   = DepositMonitor(EXCHANGES, pool_usdt, pool_locks)
    converter = AutoConverter(EXCHANGES, pool_usdt, pool_locks)
    threading.Thread(target=dep_mon.run,   args=(stop_event,), daemon=True, name="deposit_monitor").start()
    threading.Thread(target=converter.run, args=(stop_event,), daemon=True, name="auto_converter").start()
    log.info("  Deposit monitor : ACTIVE (checks every 10 min)")
    log.info(f"  Auto-convert    : ACTIVE (BTC/BCH/XRP → USDT on 15th & 30th, 80%)")

    # Telegram command handler (lets you send commands to the bot)
    if config.TELEGRAM_ENABLED:
        cmd_handler = TelegramCommandHandler(
            config        = config,
            exchanges     = EXCHANGES,
            pool_usdt     = pool_usdt,
            pool_locks    = pool_locks,
            coin_in_position  = coin_in_position,
            coin_buy_price    = coin_buy_price,
            coin_buy_spent    = coin_buy_spent,
            coin_holdings     = coin_holdings,
            active_coins      = active_coins,
            active_coins_lock = active_coins_lock,
            trade_count_ref   = [trade_count],
            total_pnl_ref     = [total_pnl],
            total_fees_ref    = [total_fees],
            daily_trades      = daily_trades,
            daily_lock        = daily_lock,
            monthly_trades    = monthly_trades,
            monthly_lock      = monthly_lock,
            stop_callback     = lambda: stop_event.set(),
            pause_flag        = manual_pause,
            sell_callback     = lambda ex, exo, sym, px, m: place_sell(ex, exo, sym, px, m),
            mode_ref          = [mode],
            coin_strategy_type= coin_strategy_type,
        )
        threading.Thread(
            target=cmd_handler.run,
            args=(stop_event,),
            daemon=True,
            name="telegram_commands"
        ).start()
        log.info("  Telegram cmds  : ACTIVE — send /help to your bot")
        hunter = NewListingHunter(
            EXCHANGES, pool_usdt, pool_locks,
            coin_holdings, coin_in_position,
            coin_buy_price, coin_buy_spent,
            stats_lock, trade_count, total_pnl,
            daily_lock, daily_trades,
        )
        threading.Thread(target=hunter.run, args=(stop_event,), daemon=True, name="listing_hunter").start()
        log.info("  Listing hunter  : ACTIVE (checks every 30 min — auto-buys new KuCoin listings)")

    # Adaptive Intelligence — regime detection + self-learning
    intelligence = AdaptiveIntelligence(
        config     = config,
        exchanges  = EXCHANGES,
        pool_usdt  = pool_usdt,
        tg_send_fn = tg_send if config.TELEGRAM_ENABLED else None,
    )
    set_intelligence(intelligence)
    threading.Thread(
        target=intelligence.run,
        args=(stop_event,),
        daemon=True,
        name="adaptive_intelligence",
    ).start()
    log.info("  Adaptive AI     : ACTIVE — regime detection + self-learning")

    # Deep market study — weekly analysis + approval-gated proposals
    study = MarketStudy(
        monthly_trades = monthly_trades,
        monthly_lock   = monthly_lock,
        config         = config,
        approval_gate  = approval_gate_module,
    )
    threading.Thread(target=study.run, args=(stop_event,),
                    daemon=True, name="market_study").start()
    log.info("  Market study    : ACTIVE — weekly deep analysis, Y/N approval required")
    if getattr(config, "AI_ENABLED", False) and getattr(config, "STRATEGY_AUTO_UPDATE", True):
        strategy_opt = StrategyOptimizer(
            monthly_trades = monthly_trades,
            monthly_lock   = monthly_lock,
            pool_usdt      = pool_usdt,
            config         = config,
        )
        threading.Thread(
            target=strategy_opt.run,
            args=(stop_event,),
            daemon=True,
            name="strategy_optimizer",
        ).start()
        log.info("  Strategy opt   : ACTIVE — AI reviews strategy on 1st of each month")

    # Launch coin threads per exchange — staggered to avoid rate limits
    for ex_name, exchange in EXCHANGES.items():
        with active_coins_lock:
            coins = list(active_coins[ex_name])
        for i, symbol in enumerate(coins):
            t = threading.Thread(
                target=coin_worker,
                args=(ex_name, exchange, symbol, mode, stop_event),
                daemon=True, name=f"{ex_name}:{symbol}",
            )
            t.start()
            threads.append(t)
            # Stagger by 3s per thread — 19 coins = 57s to fully spin up
            # This prevents all threads hitting the API simultaneously
            time.sleep(3)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping...")
        stop_event.set()
        for t in threads:
            t.join(timeout=5)
        log.info(f"Done. Trades={trade_count}  P&L={'+'if total_pnl>=0 else ''}${total_pnl:.4f}")
        tg_stop(trade_count, total_pnl, pool_usdt, mode)


if __name__ == "__main__":
    run()

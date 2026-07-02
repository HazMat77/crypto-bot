"""
Period Reports
================
Daily / monthly / yearly trading summaries built from the durable trade
ledger (trade_ledger.py). Unlike the bot's in-memory daily_trades /
monthly_trades lists (used by the /daily, /weekly, /monthly Telegram
commands), the ledger survives restarts and covers any past period —
which is what makes a real yearly report possible at all.

Used by telegram_commands.py (/yearly) and gui_dashboard.py (Reports page).
"""

import logging
from collections import defaultdict
from datetime import date

from trade_ledger import load_trades

log = logging.getLogger(__name__)

PERIODS = ("daily", "monthly", "yearly")


def _period_bounds(period: str, when: date = None):
    when = when or date.today()
    if period == "daily":
        start = end = when
        label = when.strftime("%Y-%m-%d")
    elif period == "monthly":
        start = when.replace(day=1)
        end   = when
        label = when.strftime("%B %Y")
    elif period == "yearly":
        start = when.replace(month=1, day=1)
        end   = when
        label = when.strftime("%Y")
    else:
        raise ValueError(f"Unknown period: {period!r} — expected one of {PERIODS}")
    return start.isoformat(), end.isoformat(), label


def build_report(period: str, when: date = None, starting_pool: float = 100.0) -> dict:
    """
    Returns a summary dict for the given period ("daily" | "monthly" |
    "yearly"), computed fresh from the durable trade ledger every call —
    so it's always accurate even across bot restarts.
    """
    start, end, label = _period_bounds(period, when)
    trades = load_trades(start, end)

    num   = len(trades)
    wins  = [t for t in trades if (t.get("pnl_net") or 0) >= 0]
    gross = sum(t.get("pnl_gross", 0) or 0 for t in trades)
    fees  = sum(t.get("fees", 0)      or 0 for t in trades)
    net   = sum(t.get("pnl_net", 0)   or 0 for t in trades)
    win_rate = (len(wins) / num * 100) if num else 0.0
    roi      = (net / starting_pool * 100) if starting_pool else 0.0

    by_coin = defaultdict(lambda: {"n": 0, "gross": 0.0, "fees": 0.0, "net": 0.0})
    for t in trades:
        s = by_coin[t.get("coin", "?")]
        s["n"]     += 1
        s["gross"] += t.get("pnl_gross", 0) or 0
        s["fees"]  += t.get("fees", 0)      or 0
        s["net"]   += t.get("pnl_net", 0)   or 0
    top_coins = sorted(by_coin.items(), key=lambda kv: kv[1]["net"], reverse=True)

    best  = max(trades, key=lambda t: t.get("pnl_net", 0) or 0) if trades else None
    worst = min(trades, key=lambda t: t.get("pnl_net", 0) or 0) if trades else None

    return {
        "period":      period,
        "label":       label,
        "start":       start,
        "end":         end,
        "num_trades":  num,
        "wins":        len(wins),
        "losses":      num - len(wins),
        "win_rate":    win_rate,
        "gross_pnl":   gross,
        "fees":        fees,
        "net_pnl":     net,
        "roi_pct":     roi,
        "by_coin":     dict(top_coins),
        "best_trade":  best,
        "worst_trade": worst,
    }


_PERIOD_TITLE = {"daily": "🌙 Daily Report", "monthly": "📅 Monthly Report", "yearly": "🗓 Yearly Report"}


def format_report_text(report: dict, mode_label: str = "") -> str:
    """Formats a build_report() dict as Telegram HTML, matching the
    style of the existing /daily and /monthly commands."""
    title = _PERIOD_TITLE.get(report["period"], "Report")
    header = f"{title} — {report['label']}\n━━━━━━━━━━━━━━━━\n"
    if mode_label:
        header += f"Mode: {mode_label}\n\n"

    if report["num_trades"] == 0:
        return f"{header}No trades completed in this period."

    sign     = "+" if report["net_pnl"] >= 0 else ""
    sign_roi = "+" if report["roi_pct"] >= 0 else ""
    arrow    = "📈" if report["net_pnl"] >= 0 else "📉"

    top = list(report["by_coin"].items())[:5]
    coin_lines = ""
    for k, s in top:
        sg = "+" if s["gross"] >= 0 else ""
        sn = "+" if s["net"]   >= 0 else ""
        coin_lines += (f"  • {k} ({s['n']} trade{'s' if s['n'] != 1 else ''})\n"
                      f"      Gross {sg}${s['gross']:.4f}  Fee -${s['fees']:.4f}  "
                      f"Net <b>{sn}${s['net']:.4f}</b>\n")

    best, worst = report["best_trade"], report["worst_trade"]
    best_line  = (f"🏆 Best trade:  {best['coin']} net +${best.get('pnl_net', 0):.4f}\n"
                 if best else "")
    worst_line = (f"💔 Worst trade: {worst['coin']} net ${worst.get('pnl_net', 0):.4f}\n\n"
                 if worst else "")

    return (
        f"{header}"
        f"📊 <b>Totals:</b>\n"
        f"  Trades:   {report['num_trades']}\n"
        f"  Win rate: {report['win_rate']:.0f}% ({report['wins']} wins / {report['losses']} losses)\n\n"
        f"💵 Gross P&L:  {'+' if report['gross_pnl'] >= 0 else ''}${report['gross_pnl']:.4f}\n"
        f"💸 Total fees: -${report['fees']:.4f}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{arrow} <b>Net: {sign}${report['net_pnl']:.4f} USDT</b>\n"
        f"📊 <b>ROI: {sign_roi}{report['roi_pct']:.2f}%</b>\n\n"
        f"{best_line}{worst_line}"
        f"📌 <b>Top coins:</b>\n{coin_lines}"
    )

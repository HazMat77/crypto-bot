"""
Tax Reporting Export
======================
Generates a realized-gains CSV (cost basis, proceeds, gain/loss, holding
term) from the durable trade ledger (trade_ledger.py) — covering the
bot's full trading history, not just today/this month.

FIFO note: this bot only ever holds ONE open position per (exchange,
symbol) at a time (see coin_in_position gating in bot.py) — it never
buys more of a coin it's already holding. That means every ledger entry
already IS a single, complete, FIFO-ordered tax lot on its own: there is
no partial-fill or multiple-open-lot scenario to match up, unlike a
general brokerage-import tool that has to reconstruct lot order from a
messier trade history. This exporter is a straight pass-through of the
ledger into standard tax-lot columns, not a lot-matching engine.

NOT TAX ADVICE. Short/long-term classification uses the common US
366-day threshold as a simplification — verify against your own
jurisdiction's rules and consult a tax professional before filing.
Futures P&L in many jurisdictions (e.g. US Section 1256 contracts) is
taxed under different rules than spot capital gains entirely; this
export does not attempt to apply those — it labels the row's `side` so
you (or your accountant) can route spot and futures rows differently.

Usage:
    from tax_export import export_tax_csv
    export_tax_csv("tax_report_2025.csv", start_date="2025-01-01", end_date="2025-12-31")
"""

import csv
import logging
from datetime import datetime, timedelta

from trade_ledger import load_trades

log = logging.getLogger(__name__)

LONG_TERM_DAYS = 366   # US simplification — see module docstring


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def build_tax_rows(start_date: str = None, end_date: str = None) -> list:
    """
    Returns a list of dicts, one per closed trade, in standard tax-lot
    shape. Rows with no known acquisition date (entry_time missing —
    e.g. a position the startup self-checker adopted after a crash, with
    no real entry recorded) are still included, with "Date Acquired" and
    "Term" left blank rather than guessed at — silently fabricating an
    acquisition date would be worse than admitting it's unknown.
    """
    trades = load_trades(start_date, end_date)
    rows = []

    for t in trades:
        entry_dt = _parse_iso(t.get("entry_time"))
        exit_dt  = _parse_iso(t.get("exit_time"))

        if entry_dt and exit_dt:
            held_days = (exit_dt - entry_dt).days
            term = "Long-term" if held_days >= LONG_TERM_DAYS else "Short-term"
        else:
            held_days = None
            term = ""

        proceeds   = t.get("proceeds", 0.0) or 0.0
        cost_basis = t.get("spent", 0.0) or 0.0
        fees       = t.get("fees", 0.0) or 0.0
        gain_loss  = t.get("pnl_net", round(proceeds - cost_basis - fees, 8))

        rows.append({
            "Description":      f"{t.get('coin', '?')} ({t.get('side', 'spot_long')})",
            "Exchange":         t.get("exchange", ""),
            "Date Acquired":    entry_dt.strftime("%Y-%m-%d") if entry_dt else "",
            "Date Sold":        exit_dt.strftime("%Y-%m-%d") if exit_dt else "",
            "Days Held":        held_days if held_days is not None else "",
            "Term":             term,
            "Proceeds (USDT)":  round(proceeds, 2),
            "Cost Basis (USDT)": round(cost_basis, 2),
            "Fees (USDT)":      round(fees, 2),
            "Gain/Loss (USDT)": round(gain_loss, 2),
            "Mode":             t.get("mode", ""),
        })

    return rows


def export_tax_csv(output_path: str, start_date: str = None, end_date: str = None) -> dict:
    """
    Writes the tax-lot CSV to `output_path`. Returns a small summary dict
    (row count, total realized gain/loss, short vs long term totals) so a
    caller (e.g. a Telegram command) can show a quick preview without
    re-reading the file.
    """
    rows = build_tax_rows(start_date, end_date)

    if not rows:
        return {"rows": 0, "path": output_path, "total_gain_loss": 0.0,
                "short_term_total": 0.0, "long_term_total": 0.0, "unclassified_total": 0.0}

    fieldnames = ["Description", "Exchange", "Date Acquired", "Date Sold", "Days Held",
                 "Term", "Proceeds (USDT)", "Cost Basis (USDT)", "Fees (USDT)",
                 "Gain/Loss (USDT)", "Mode"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    total          = sum(r["Gain/Loss (USDT)"] for r in rows)
    short_total    = sum(r["Gain/Loss (USDT)"] for r in rows if r["Term"] == "Short-term")
    long_total     = sum(r["Gain/Loss (USDT)"] for r in rows if r["Term"] == "Long-term")
    unclassified   = sum(r["Gain/Loss (USDT)"] for r in rows if r["Term"] == "")

    log.info(f"[TAX] Exported {len(rows)} rows to {output_path} — "
            f"total gain/loss ${total:.2f}")

    return {
        "rows":               len(rows),
        "path":               output_path,
        "total_gain_loss":    round(total, 2),
        "short_term_total":   round(short_total, 2),
        "long_term_total":    round(long_total, 2),
        "unclassified_total": round(unclassified, 2),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export realized-gains tax report from the trade ledger")
    parser.add_argument("--output", default="tax_report.csv")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    args = parser.parse_args()

    summary = export_tax_csv(args.output, args.start, args.end)
    print(f"Exported {summary['rows']} trades to {summary['path']}")
    print(f"Total realized gain/loss: ${summary['total_gain_loss']:.2f}")
    print(f"  Short-term: ${summary['short_term_total']:.2f}")
    print(f"  Long-term:  ${summary['long_term_total']:.2f}")
    if summary["unclassified_total"]:
        print(f"  Unclassified (missing dates): ${summary['unclassified_total']:.2f}")

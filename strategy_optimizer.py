"""
AI Strategy Optimizer
======================
Runs once a month automatically. Uses Claude/Grok to:

  1. Review last 30 days of actual trade performance
  2. Analyse current market conditions via news sources
  3. Run backtests on current vs alternative parameters
  4. Ask AI to reason about what should change and why
  5. Write updated config.py values with explanations
  6. Back up old config before any changes
  7. Send full report to Telegram

This keeps the bot self-adapting to market cycles without
manual intervention. All changes are logged and reversible.

How it works:
  - Monthly trigger fires on the 1st of each month
  - Reviews daily_trades from past 30 days
  - Runs grid search on recent data
  - Sends performance + market context to AI
  - AI suggests parameter adjustments
  - Changes applied only if AI confidence >= threshold
  - Full audit trail in logs/strategy_updates/
"""

import os
import json
import shutil
import logging
import threading
import requests
from datetime import datetime, timedelta, date
from pathlib import Path

log = logging.getLogger(__name__)

STRATEGY_LOG_DIR = Path("logs/strategy_updates")
STRATEGY_LOG_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
#  PERFORMANCE ANALYSER
# ══════════════════════════════════════════════════════════════════════════════

def analyse_monthly_performance(monthly_trades: list) -> dict:
    """
    Summarise the past month's trades into metrics the AI can reason about.
    """
    if not monthly_trades:
        return {"error": "No trades in past month"}

    wins         = [t for t in monthly_trades if t.get("pnl_net", 0) >= 0]
    losses       = [t for t in monthly_trades if t.get("pnl_net", 0) <  0]
    total_net    = sum(t.get("pnl_net",   0) for t in monthly_trades)
    total_fees   = sum(t.get("fees",      0) for t in monthly_trades)
    total_gross  = sum(t.get("pnl_gross", 0) for t in monthly_trades)
    win_rate     = len(wins) / len(monthly_trades) * 100 if monthly_trades else 0

    avg_win      = (sum(t.get("pnl_net", 0) for t in wins)   / len(wins))   if wins   else 0
    avg_loss     = (sum(t.get("pnl_net", 0) for t in losses) / len(losses)) if losses else 0

    # Exit reason breakdown
    exit_reasons = {}
    for t in monthly_trades:
        r = t.get("exit_reason", "unknown")
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # Coin performance
    coin_perf = {}
    for t in monthly_trades:
        coin = t.get("coin", "?")
        coin_perf.setdefault(coin, {"trades": 0, "net": 0.0})
        coin_perf[coin]["trades"] += 1
        coin_perf[coin]["net"]    += t.get("pnl_net", 0)

    best_coin  = max(coin_perf.items(), key=lambda x: x[1]["net"])[0]  if coin_perf else "?"
    worst_coin = min(coin_perf.items(), key=lambda x: x[1]["net"])[0]  if coin_perf else "?"

    return {
        "total_trades":   len(monthly_trades),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate_pct":   round(win_rate, 1),
        "total_net_usdt": round(total_net, 4),
        "total_gross":    round(total_gross, 4),
        "total_fees":     round(total_fees, 4),
        "avg_win":        round(avg_win, 4),
        "avg_loss":       round(avg_loss, 4),
        "profit_factor":  round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 99,
        "exit_reasons":   exit_reasons,
        "best_coin":      best_coin,
        "worst_coin":     worst_coin,
        "coin_breakdown": coin_perf,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  BACKTESTER INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

def run_quick_backtest(symbol: str = "BTC-USDT", days: int = 60) -> dict:
    """Run a quick backtest on recent data to validate current settings."""
    try:
        from backtest import get_data, Backtester, GridSearchOptimizer
        import config

        df = get_data(symbol, "15min", days)
        if df.empty:
            return {"error": "No data available for backtest"}

        # Test current settings
        current = Backtester(
            symbol          = symbol,
            df              = df,
            starting_usdt   = 100.0,
            trade_size      = 10.0,
            rsi_buy         = config.RSI_BUY,
            rsi_sell        = config.RSI_SELL,
            ma_period       = config.MA_PERIOD,
            stop_loss_pct   = getattr(config, "STOP_LOSS_PCT",   0.06),
            take_profit_pct = getattr(config, "TAKE_PROFIT_PCT", 0.04),
        ).run()

        # Quick optimise to find potentially better settings
        opt     = GridSearchOptimizer(symbol, days=days, grid={
            "rsi_buy":         [30, 35, 40, 45],
            "rsi_sell":        [55, 60, 65, 70],
            "ma_period":       [10, 20, 30],
            "stop_loss_pct":   [0.04, 0.06, 0.08],
            "take_profit_pct": [0.03, 0.04, 0.06],
        })
        optimal = opt.run(top_n=1)
        best    = optimal[0] if optimal else None

        return {
            "current_settings": {
                "rsi_buy":         config.RSI_BUY,
                "rsi_sell":        config.RSI_SELL,
                "ma_period":       config.MA_PERIOD,
                "stop_loss_pct":   getattr(config, "STOP_LOSS_PCT",   0.06),
                "take_profit_pct": getattr(config, "TAKE_PROFIT_PCT", 0.04),
            },
            "current_backtest": {
                "win_rate":     current.get("win_rate_pct",   current.get("win_rate", 0)),
                "roi_pct":      current.get("roi_pct", 0),
                "max_drawdown": current.get("max_drawdown", 0),
                "total_trades": current.get("total_trades", 0),
            },
            "suggested_settings": best["params"]       if best else None,
            "suggested_backtest": {
                "win_rate":     best["test"].get("win_rate", 0),
                "roi_pct":      best["test"].get("roi_pct",  0),
                "max_drawdown": best["test"].get("max_drawdown", 0),
            } if best else None,
        }

    except Exception as e:
        log.error(f"[STRATEGY] Backtest failed: {e}")
        return {"error": str(e)}


def run_walk_forward_backtest(symbol: str = "BTC-USDT", days: int = 270) -> dict:
    """
    Heavier, more rigorous alternative to run_quick_backtest — uses
    WalkForwardOptimizer's multi-window rolling validation instead of a
    single 70/30 split. Tests each parameter set across several
    consecutive unseen periods rather than just one, which catches
    overfitting that a single split can miss.

    This runs ~4x more backtests than the quick version and needs 270+
    days of history, so it's intended for a slower cadence (the
    quarterly review, not every single monthly cycle) — see
    QUARTERLY_WALKFORWARD_ENABLED in config.py.
    """
    try:
        from backtest import WalkForwardOptimizer
        import config

        opt = WalkForwardOptimizer(symbol=symbol, days=days, n_windows=5)
        result = opt.run(top_n=1)

        if "error" in result or not result.get("top_params"):
            return {"error": result.get("error", "No robust parameters found")}

        best = result["top_params"][0]
        return {
            "current_settings": {
                "rsi_buy":         config.RSI_BUY,
                "rsi_sell":        config.RSI_SELL,
                "ma_period":       config.MA_PERIOD,
                "stop_loss_pct":   getattr(config, "STOP_LOSS_PCT",   0.06),
                "take_profit_pct": getattr(config, "TAKE_PROFIT_PCT", 0.04),
            },
            "suggested_settings":  best["params"],
            "windows_tested":      best["windows_tested"],
            "n_windows":           result["n_windows"],
            "consistency":         best["consistency"],
            "mean_oos_score":      best["mean_oos_score"],
        }
    except Exception as e:
        log.error(f"[STRATEGY] Walk-forward backtest failed: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
#  MARKET CONTEXT FETCHER
# ══════════════════════════════════════════════════════════════════════════════

def get_market_context_for_ai() -> str:
    """Fetch current market context to give AI full situational awareness."""
    try:
        from news_aggregator import fetch_market_news, get_market_context
        news    = fetch_market_news()
        ctx     = get_market_context()
        btc_dom = ctx.get("btc_dominance", 0)
        mkt_chg = ctx.get("market_change_24h", 0)
        trending = ", ".join(ctx.get("trending_coingecko", [])[:5])

        # Trim news to avoid huge prompt
        news_trimmed = "\n".join(news.split("\n")[:30])

        return (
            f"BTC Dominance: {btc_dom}%\n"
            f"Market 24h change: {mkt_chg:+.1f}%\n"
            f"Trending coins: {trending}\n\n"
            f"Recent headlines:\n{news_trimmed}"
        )
    except Exception as e:
        log.warning(f"[STRATEGY] Market context fetch failed: {e}")
        return "Market context unavailable"


# ══════════════════════════════════════════════════════════════════════════════
#  AI STRATEGY REVIEW
# ══════════════════════════════════════════════════════════════════════════════

def ask_ai_for_strategy_update(
    performance:    dict,
    backtest_data:  dict,
    market_context: str,
    current_config: dict,
    config,
) -> dict:
    """
    Send full performance + market data to AI and ask for strategy recommendations.
    Returns dict with suggested parameter changes and reasoning.
    """
    month = datetime.now().strftime("%B %Y")

    prompt = f"""You are an expert quantitative crypto trading strategist conducting a monthly strategy review.

Analyse the bot's performance over the past month and recommend parameter adjustments.

═══ CURRENT CONFIGURATION ═══
RSI_BUY:          {current_config.get('RSI_BUY', 35)}
RSI_SELL:         {current_config.get('RSI_SELL', 65)}
MA_PERIOD:        {current_config.get('MA_PERIOD', 20)}
STOP_LOSS_PCT:    {current_config.get('STOP_LOSS_PCT', 0.06)} ({current_config.get('STOP_LOSS_PCT', 0.06)*100:.0f}%)
TAKE_PROFIT_PCT:  {current_config.get('TAKE_PROFIT_PCT', 0.04)} ({current_config.get('TAKE_PROFIT_PCT', 0.04)*100:.0f}%)
TRAILING_STOP_PCT:{current_config.get('TRAILING_STOP_PCT', 0.03)}
MAX_HOLD_HOURS:   {current_config.get('MAX_HOLD_HOURS', 48)}
CANDLE_INTERVAL:  {current_config.get('CANDLE_INTERVAL', '15min')}
MIN_TRADE_USDT:   {current_config.get('MIN_TRADE_USDT', 5)}

═══ LAST 30 DAYS PERFORMANCE ═══
Total trades:     {performance.get('total_trades', 0)}
Win rate:         {performance.get('win_rate_pct', 0)}%
Net P&L:          ${performance.get('total_net_usdt', 0):+.4f} USDT
Gross P&L:        ${performance.get('total_gross', 0):+.4f} USDT
Total fees paid:  ${performance.get('total_fees', 0):.4f} USDT
Avg win:          ${performance.get('avg_win', 0):+.4f}
Avg loss:         ${performance.get('avg_loss', 0):.4f}
Profit factor:    {performance.get('profit_factor', 0):.2f}
Exit reasons:     {json.dumps(performance.get('exit_reasons', {}))}
Best coin:        {performance.get('best_coin', '?')}
Worst coin:       {performance.get('worst_coin', '?')}

═══ BACKTEST ON RECENT DATA ═══
Current settings ROI:    {backtest_data.get('current_backtest', {}).get('roi_pct', 'N/A')}%
Current settings WR:     {backtest_data.get('current_backtest', {}).get('win_rate', 'N/A')}%
Suggested settings:      {json.dumps(backtest_data.get('suggested_settings', {}))}
Suggested settings ROI:  {backtest_data.get('suggested_backtest', {}).get('roi_pct', 'N/A') if backtest_data.get('suggested_backtest') else 'N/A'}%

═══ CURRENT MARKET CONDITIONS ({month}) ═══
{market_context}

═══ YOUR TASK ═══
Based on ALL the above data:
1. Is the current strategy performing well or poorly?
2. Are the current RSI thresholds appropriate for current market conditions?
3. Should stop-loss / take-profit be tightened or loosened?
4. What specific parameter changes do you recommend?

Important rules:
- Only suggest changes that are clearly justified by the data
- Prefer small incremental adjustments over drastic changes
- If performance is acceptable (win rate >55%, positive ROI), suggest minimal changes
- If market is trending strongly, widen RSI range; if choppy, tighten it
- Never suggest RSI_BUY >= RSI_SELL
- RSI_BUY must be between 25-50; RSI_SELL between 50-75
- STOP_LOSS_PCT must be between 0.02-0.10 (HARD CEILING — never exceed 10% loss); TAKE_PROFIT_PCT between 0.02-0.25 (target 25% on strong setups)

Respond ONLY with valid JSON (no markdown):
{{
  "assessment": "brief 2-3 sentence assessment of last month",
  "market_regime": "trending_bull" | "trending_bear" | "sideways" | "volatile",
  "confidence": 0-100,
  "changes_recommended": true | false,
  "parameters": {{
    "RSI_BUY":          <number or null if no change>,
    "RSI_SELL":         <number or null if no change>,
    "MA_PERIOD":        <number or null if no change>,
    "STOP_LOSS_PCT":    <number or null if no change>,
    "TAKE_PROFIT_PCT":  <number or null if no change>,
    "TRAILING_STOP_PCT":<number or null if no change>,
    "MAX_HOLD_HOURS":   <number or null if no change>
  }},
  "reasoning": "detailed explanation of each suggested change",
  "risk_level": "conservative" | "balanced" | "aggressive"
}}"""

    # Route to Claude or Grok
    try:
        provider = getattr(config, "AI_PROVIDER", "claude").lower()

        if provider == "grok":
            resp = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {config.GROK_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": "grok-3",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 600},
                timeout=45,
            )
            raw = resp.json()["choices"][0]["message"]["content"].strip()
        else:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": config.AI_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6",
                      "max_tokens": 600,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=45,
            )
            data = resp.json()
            raw  = "".join(b.get("text", "") for b in data.get("content", []))

        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(raw)

    except Exception as e:
        log.error(f"[STRATEGY] AI review failed: {e}")
        return {"error": str(e), "changes_recommended": False}


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG UPDATER
# ══════════════════════════════════════════════════════════════════════════════

def apply_strategy_update(ai_response: dict, config, min_confidence: int = 65) -> bool:
    """
    Apply AI-recommended parameter changes to config.py.
    Creates a backup first. Returns True if changes were applied.
    """
    if not ai_response.get("changes_recommended", False):
        log.info("[STRATEGY] AI recommends no changes this month")
        return False

    confidence = ai_response.get("confidence", 0)
    if confidence < min_confidence:
        log.info(f"[STRATEGY] AI confidence {confidence}% below threshold "
                f"{min_confidence}% — no changes applied")
        return False

    params = ai_response.get("parameters", {})
    if not params:
        return False

    # Filter to only non-null suggestions
    changes = {k: v for k, v in params.items() if v is not None}
    if not changes:
        log.info("[STRATEGY] AI returned no parameter changes")
        return False

    # ── Backup current config ──────────────────────────────────────────────
    config_path  = Path("config.py")
    backup_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path  = STRATEGY_LOG_DIR / f"config_backup_{backup_stamp}.py"

    try:
        shutil.copy(config_path, backup_path)
        log.info(f"[STRATEGY] Config backed up to {backup_path}")
    except Exception as e:
        log.error(f"[STRATEGY] Backup failed: {e}")
        return False

    # ── Read current config ────────────────────────────────────────────────
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        log.error(f"[STRATEGY] Could not read config.py: {e}")
        return False

    # ── Apply each change ──────────────────────────────────────────────────
    applied = []
    import re

    for param, new_value in changes.items():
        # Validate ranges
        valid = True
        if param == "RSI_BUY"    and not (25 <= new_value <= 50):  valid = False
        if param == "RSI_SELL"   and not (50 <= new_value <= 75):  valid = False
        # STOP_LOSS_PCT has its own hard ceiling (4% max loss, user requirement)
        # enforced here independent of AI suggestions, regardless of prompt compliance
        if param == "STOP_LOSS_PCT" or param == "NORMAL_STOP_LOSS" or param == "AGGRESSIVE_STOP_LOSS":
            sl_ceiling = getattr(config, "MAX_STOP_LOSS_PCT", 0.04)
            if not (0.01 <= new_value <= sl_ceiling): valid = False
        elif param in ("TAKE_PROFIT_PCT","TRAILING_STOP_PCT"):
            if not (0.01 <= new_value <= 0.15): valid = False
        if param == "MAX_HOLD_HOURS" and not (4 <= new_value <= 168): valid = False

        if not valid:
            log.warning(f"[STRATEGY] {param}={new_value} out of safe range — skipped")
            continue

        # Get old value for logging
        old_val = getattr(config, param, None)

        # Replace in config file
        pattern     = rf"^({re.escape(param)}\s*=\s*)(.+?)(\s*(?:#.*)?)$"
        replacement = rf"\g<1>{new_value}\g<3>"
        new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

        if new_content != content:
            content = new_content
            applied.append(f"{param}: {old_val} → {new_value}")
            log.info(f"[STRATEGY] Updated {param}: {old_val} → {new_value}")

    if not applied:
        log.info("[STRATEGY] No config lines were changed")
        return False

    # ── Write updated config ───────────────────────────────────────────────
    update_header = (
        f"\n# ── Strategy auto-updated {datetime.now().strftime('%Y-%m-%d')} ──\n"
        f"# AI assessment: {ai_response.get('assessment', '')[:100]}\n"
        f"# Market regime: {ai_response.get('market_regime', 'unknown')}\n"
        f"# Changes: {', '.join(applied)}\n"
    )

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content + update_header)

        from config_live import reload_config
        if reload_config():
            log.info(f"[STRATEGY] Config updated AND live-reloaded. Changes: {applied}")
        else:
            log.error(f"[STRATEGY] Config written to disk but reload FAILED — "
                     f"bot still running on old settings. Changes: {applied}")
        return True
    except Exception as e:
        log.error(f"[STRATEGY] Write failed: {e}")
        # Restore backup
        shutil.copy(backup_path, config_path)
        log.info("[STRATEGY] Config restored from backup")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM REPORT
# ══════════════════════════════════════════════════════════════════════════════

def send_strategy_report(performance: dict, ai_response: dict,
                          applied: bool, changes: list, config):
    """Send the monthly strategy review report to Telegram."""
    if not getattr(config, "TELEGRAM_ENABLED", False):
        return

    month     = datetime.now().strftime("%B %Y")
    wr        = performance.get("win_rate_pct", 0)
    net       = performance.get("total_net_usdt", 0)
    sign      = "+" if net >= 0 else ""
    arrow     = "📈" if net >= 0 else "📉"
    regime    = ai_response.get("market_regime", "unknown").replace("_", " ").title()
    assessment= ai_response.get("assessment", "No assessment available")
    reasoning = ai_response.get("reasoning",  "No reasoning provided")[:300]

    if applied:
        changes_str = "\n".join(f"  • {c}" for c in changes)
        update_line = f"✅ <b>Config updated:</b>\n{changes_str}"
    else:
        conf = ai_response.get("confidence", 0)
        if not ai_response.get("changes_recommended"):
            update_line = "✅ No changes needed — strategy performing well"
        else:
            update_line = f"⏸ Changes withheld — confidence {conf}% below threshold"

    msg = (
        f"🧠 <b>Monthly Strategy Review — {month}</b>\n━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>Last 30 Days:</b>\n"
        f"  Trades:   {performance.get('total_trades', 0)}\n"
        f"  Win rate: {wr}%\n"
        f"  {arrow} Net P&L: {sign}${net:.4f} USDT\n\n"
        f"🌍 <b>Market Regime:</b> {regime}\n\n"
        f"🤖 <b>AI Assessment:</b>\n{assessment}\n\n"
        f"💡 <b>Reasoning:</b>\n{reasoning}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{update_line}\n\n"
        f"📁 Backup saved in logs/strategy_updates/\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID,
                  "text": msg, "parse_mode": "HTML"},
            timeout=15,
        )
        log.info("[STRATEGY] Report sent to Telegram")
    except Exception as e:
        log.warning(f"[STRATEGY] Telegram send failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

def run_monthly_review(monthly_trades: list, pool_usdt: dict, config):
    """
    Full monthly strategy review pipeline:
    1. Analyse performance
    2. Run backtest with current + optimised settings
    3. Get market context
    4. Ask AI for strategy recommendations
    5. Apply changes (if confident enough)
    6. Send Telegram report
    7. Log everything
    """
    month = datetime.now().strftime("%Y-%m")
    log.info(f"[STRATEGY] ═══ Monthly strategy review starting — {month} ═══")

    # ── Step 1: Performance analysis ──────────────────────────────────────
    performance = analyse_monthly_performance(monthly_trades)
    if "error" in performance:
        log.warning(f"[STRATEGY] {performance['error']} — skipping review")
        return

    log.info(f"[STRATEGY] Performance: {performance['total_trades']} trades, "
            f"{performance['win_rate_pct']}% WR, "
            f"${performance['total_net_usdt']:+.4f} net")

    # ── Step 2: Quick backtest ─────────────────────────────────────────────
    log.info("[STRATEGY] Running backtest comparison...")
    backtest_data = run_quick_backtest("BTC-USDT", days=60)

    # ── Step 2b: Quarterly deeper validation (every 3rd month only) ────────
    # The full multi-window walk-forward is ~4x heavier than the quick
    # check above, so it runs on a slower cadence rather than every month.
    now_month = datetime.now().month
    if (getattr(config, "QUARTERLY_WALKFORWARD_ENABLED", True)
            and now_month % 3 == 0):
        log.info("[STRATEGY] Quarterly check — running full multi-window walk-forward...")
        walkforward_data = run_walk_forward_backtest("BTC-USDT", days=270)
        if "error" not in walkforward_data:
            log.info(f"[STRATEGY] Walk-forward result held up in "
                    f"{walkforward_data['windows_tested']}/{walkforward_data['n_windows']-1} "
                    f"windows (consistency={walkforward_data['consistency']:.0%})")
            # Only let the walk-forward result override the quick backtest's
            # suggestion if it actually held up across MOST windows — a
            # parameter set that only worked in 1 of 4 windows is not "more
            # robust", it's the same overfitting problem the quick check
            # already has, just discovered the hard way.
            if walkforward_data["consistency"] >= 0.75:
                backtest_data["suggested_settings"] = walkforward_data["suggested_settings"]
                backtest_data["suggested_backtest"] = {
                    "win_rate":     None,   # multi-window doesn't have one single win_rate figure
                    "roi_pct":      None,
                    "max_drawdown": None,
                }
                backtest_data["walkforward_consistency"] = walkforward_data["consistency"]
                log.info("[STRATEGY] Walk-forward result is consistent enough to "
                        "override the quick-check suggestion")
        else:
            log.warning(f"[STRATEGY] Quarterly walk-forward skipped: {walkforward_data['error']}")

    # ── Step 3: Market context ────────────────────────────────────────────
    log.info("[STRATEGY] Fetching market context...")
    market_context = get_market_context_for_ai()

    # ── Step 4: Current config snapshot ───────────────────────────────────
    current_config = {
        "RSI_BUY":          config.RSI_BUY,
        "RSI_SELL":         config.RSI_SELL,
        "MA_PERIOD":        config.MA_PERIOD,
        "STOP_LOSS_PCT":    getattr(config, "STOP_LOSS_PCT",    0.06),
        "TAKE_PROFIT_PCT":  getattr(config, "TAKE_PROFIT_PCT",  0.04),
        "TRAILING_STOP_PCT":getattr(config, "TRAILING_STOP_PCT",0.03),
        "MAX_HOLD_HOURS":   getattr(config, "MAX_HOLD_HOURS",   48),
        "CANDLE_INTERVAL":  config.CANDLE_INTERVAL,
        "MIN_TRADE_USDT":   config.MIN_TRADE_USDT,
    }

    # ── Step 5: Ask AI ────────────────────────────────────────────────────
    log.info("[STRATEGY] Consulting AI for strategy recommendations...")
    ai_response = ask_ai_for_strategy_update(
        performance, backtest_data, market_context, current_config, config
    )

    if "error" in ai_response:
        log.error(f"[STRATEGY] AI review failed: {ai_response['error']}")
        send_strategy_report(performance, {"assessment": "AI unavailable",
                             "changes_recommended": False}, False, [], config)
        return

    log.info(f"[STRATEGY] AI response: regime={ai_response.get('market_regime')} "
            f"confidence={ai_response.get('confidence')}% "
            f"changes={ai_response.get('changes_recommended')}")

    # ── Step 6: Request approval instead of auto-applying ────────────────
    from approval_gate import request_approval
    import config as cfg

    current_config_snapshot = {
        "RSI_BUY":          cfg.RSI_BUY,
        "RSI_SELL":         cfg.RSI_SELL,
        "NORMAL_STOP_LOSS": getattr(cfg,"NORMAL_STOP_LOSS",  0.06),
        "NORMAL_TAKE_PROFIT":getattr(cfg,"NORMAL_TAKE_PROFIT",0.04),
    }

    params_proposed = ai_response.get("parameters", {})
    clean_proposed  = {k:v for k,v in params_proposed.items()
                      if v is not None and k in current_config_snapshot}

    if clean_proposed and ai_response.get("changes_recommended"):
        what_learned = (
            f"I reviewed the last 30 days of trading performance:\n"
            f"  • Trades:      {performance.get('total_trades',0)}\n"
            f"  • Win rate:    {performance.get('win_rate_pct',0):.1f}%\n"
            f"  • Net P&L:     ${performance.get('total_net_usdt',0):+.4f} USDT\n"
            f"  • Exit causes: {json.dumps(performance.get('exit_reasons',{}))}\n\n"
            f"AI Assessment:\n{ai_response.get('assessment','')}\n\n"
            f"Market Regime: <b>{ai_response.get('market_regime','unknown').replace('_',' ').title()}</b>"
        )
        why_change = ai_response.get("reasoning", "AI recommends parameter adjustments based on performance data.")

        def _request_monthly():
            result = request_approval(
                change_type  = "monthly_review",
                title        = f"Monthly AI Strategy Review — {month}",
                what_learned = what_learned,
                why_change   = why_change,
                proposed     = clean_proposed,
                current      = current_config_snapshot,
                confidence   = ai_response.get("confidence", 70),
                config       = cfg,
                timeout_hours = 48,
            )
            if result == "approved":
                apply_strategy_update(ai_response, cfg,
                                     getattr(cfg,"STRATEGY_UPDATE_MIN_CONFIDENCE",70))
                log.info("[STRATEGY] ✅ Monthly review changes applied")
            else:
                log.info(f"[STRATEGY] Monthly review {result} — no changes")

        import threading
        threading.Thread(target=_request_monthly, daemon=True,
                        name="monthly_approval").start()

    applied         = False
    applied_changes = [f"{k} → {v}" for k,v in clean_proposed.items()]

    # ── Step 7: Save detailed log ─────────────────────────────────────────
    log_entry = {
        "month":          month,
        "timestamp":      datetime.now().isoformat(),
        "performance":    performance,
        "backtest":       backtest_data,
        "config_before":  current_config,
        "ai_response":    ai_response,
        "changes_applied":applied_changes,
        "auto_applied":   applied,
    }
    log_file = STRATEGY_LOG_DIR / f"review_{month}.json"
    try:
        with open(log_file, "w") as f:
            json.dump(log_entry, f, indent=2, default=str)
        log.info(f"[STRATEGY] Full review saved to {log_file}")
    except Exception as e:
        log.warning(f"[STRATEGY] Could not save review log: {e}")

    # ── Step 8: Telegram report ───────────────────────────────────────────
    send_strategy_report(performance, ai_response, applied, applied_changes, config)
    log.info("[STRATEGY] ═══ Monthly review complete ═══")


# ══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND THREAD
# ══════════════════════════════════════════════════════════════════════════════

class StrategyOptimizer:
    """
    Background thread that triggers the monthly review on the 1st of each month.
    Waits until midnight on the 1st, runs the review, then sleeps until next month.
    """

    def __init__(self, monthly_trades: list, monthly_lock: threading.Lock,
                 pool_usdt: dict, config):
        self.monthly_trades = monthly_trades
        self.monthly_lock   = monthly_lock
        self.pool_usdt      = pool_usdt
        self.config         = config
        self.last_run_month = None

    def run(self, stop_event: threading.Event):
        log.info("[STRATEGY] Optimizer thread started — will review on 1st of each month")

        while not stop_event.is_set():
            now         = datetime.now()
            current_month = now.strftime("%Y-%m")

            # Trigger on the 1st of each month, after midnight
            if (now.day == 1 and
                now.hour >= 0 and
                self.last_run_month != current_month and
                getattr(self.config, "AI_ENABLED", False)):

                self.last_run_month = current_month
                log.info(f"[STRATEGY] 🗓️  1st of month detected — starting review")

                with self.monthly_lock:
                    trades_snapshot = list(self.monthly_trades)

                try:
                    run_monthly_review(trades_snapshot, self.pool_usdt, self.config)
                except Exception as e:
                    log.error(f"[STRATEGY] Review failed: {e}")

            # Sleep 1 hour between checks (don't need to check every second)
            stop_event.wait(timeout=3600)

        log.info("[STRATEGY] Optimizer thread stopped")

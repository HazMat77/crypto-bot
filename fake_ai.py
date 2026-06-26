"""
Fake AI Analyst — Paper Trading Simulation
===========================================
Simulates AI trading decisions locally with zero API calls.
Used when PAPER_TRADING=True and AI_ENABLED=True so you can
test the full AI workflow before spending money on the real API.

Mimics the same output format as ai_analyst.py:
  { "decision", "confidence", "reason", "approved" }

Logic: uses RSI levels, MA position, price momentum and a small
random factor to simulate realistic AI behaviour including vetoes,
confident approvals, and borderline calls — just like the real thing.
"""

import random
import logging

log = logging.getLogger(__name__)


def analyse(
    symbol:       str,
    action:       str,
    price:        float,
    rsi:          float,
    ma:           float,
    candles:      list,
    rsi_signal:   bool,
    mode:         str,
) -> dict:
    """
    Simulate an AI trading decision based on market data.
    No API calls — fully local.
    """
    coin = symbol.split("-")[0]

    # ── Price momentum (last 5 candles) ───────────────────────────────────
    if len(candles) >= 5:
        momentum = (candles[-1] - candles[-5]) / candles[-5] * 100
    else:
        momentum = 0.0

    # ── Price vs MA gap ───────────────────────────────────────────────────
    ma_gap_pct = ((price - ma) / ma * 100) if ma > 0 else 0

    # ── Simulate AI confidence scoring ────────────────────────────────────
    confidence = 50   # start neutral

    if action == "BUY":
        # RSI strength — lower RSI = more oversold = higher confidence
        if rsi < 30:
            confidence += 25
        elif rsi < 35:
            confidence += 18
        elif rsi < 40:
            confidence += 10
        elif rsi < 45:
            confidence += 5
        else:
            confidence -= 10    # RSI not very oversold

        # Price above MA is bullish
        if ma_gap_pct > 0.5:
            confidence += 10
        elif ma_gap_pct > 0:
            confidence += 5
        else:
            confidence -= 8     # price below MA on a buy = caution

        # Positive momentum supports buy
        if momentum > 1.0:
            confidence += 8
        elif momentum > 0:
            confidence += 4
        elif momentum < -2.0:
            confidence -= 12    # strong downward momentum = risky buy
        elif momentum < 0:
            confidence -= 5

        # Choppy/sideways: if RSI is between 40-55 with tiny MA gap
        if 40 <= rsi <= 55 and abs(ma_gap_pct) < 0.3:
            confidence -= 15    # sideways market — AI vetoes more

    elif action == "SELL":
        # RSI strength — higher RSI = more overbought = higher confidence
        if rsi > 70:
            confidence += 25
        elif rsi > 65:
            confidence += 18
        elif rsi > 60:
            confidence += 10
        elif rsi > 55:
            confidence += 5
        else:
            confidence -= 10

        # Price below MA supports sell
        if ma_gap_pct < -0.5:
            confidence += 10
        elif ma_gap_pct < 0:
            confidence += 5
        else:
            confidence -= 8     # price still above MA on a sell = caution

        # Negative momentum supports sell
        if momentum < -1.0:
            confidence += 8
        elif momentum < 0:
            confidence += 4
        elif momentum > 2.0:
            confidence -= 12    # strong upward momentum = hold longer
        elif momentum > 0:
            confidence -= 5

        # Choppy market veto
        if 45 <= rsi <= 60 and abs(ma_gap_pct) < 0.3:
            confidence -= 15

    # ── Add realistic noise (±8%) ─────────────────────────────────────────
    confidence += random.randint(-8, 8)
    confidence  = max(0, min(100, confidence))

    # ── Generate reason ───────────────────────────────────────────────────
    reason = _generate_reason(action, rsi, ma_gap_pct, momentum, confidence)

    # ── Decision ──────────────────────────────────────────────────────────
    from config import AI_CONFIDENCE_MIN
    approved = (confidence >= AI_CONFIDENCE_MIN)
    decision = action if approved else "HOLD"

    log.info(f"[FAKE AI {coin}] {action} → {decision} | {confidence}% | {reason}")

    return {
        "decision":   decision,
        "confidence": confidence,
        "reason":     reason,
        "approved":   approved,
        "_simulated": True,    # flag so logs can show it's simulated
    }


def _generate_reason(action, rsi, ma_gap_pct, momentum, confidence):
    """Pick a realistic-sounding reason based on the market conditions."""
    if action == "BUY":
        if confidence >= 75:
            if rsi < 35:
                return "Strong oversold RSI with bullish MA alignment"
            elif momentum > 1:
                return "Positive momentum confirms oversold bounce opportunity"
            else:
                return "RSI and MA alignment indicate solid entry point"
        elif confidence >= 55:
            if ma_gap_pct > 0:
                return "Mild oversold signal with price holding above MA"
            else:
                return "RSI suggests potential reversal but momentum unclear"
        else:
            if momentum < -1:
                return "Downward momentum too strong — waiting for clearer signal"
            elif 40 <= rsi <= 55:
                return "Sideways market detected — RSI not convincingly oversold"
            else:
                return "Signal lacks confirmation — insufficient conviction to buy"

    else:  # SELL
        if confidence >= 75:
            if rsi > 65:
                return "Strong overbought RSI with bearish MA divergence"
            elif momentum < -1:
                return "Negative momentum confirms overbought sell signal"
            else:
                return "RSI overbought and price losing MA support"
        elif confidence >= 55:
            if ma_gap_pct < 0:
                return "Mild overbought with price breaking below MA"
            else:
                return "RSI elevated but momentum not decisively bearish"
        else:
            if momentum > 1:
                return "Upward momentum still present — premature to sell"
            elif 45 <= rsi <= 60:
                return "Market too choppy — holding position for clearer exit"
            else:
                return "Sell signal weak — risk/reward not favourable"

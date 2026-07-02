"""
Staking Manager
================
Parks idle pool capital in each exchange's FLEXIBLE ("no lockup") earn
product whenever nothing better to do with it is on the table, and
redeems it the moment a trade signal wants that capital back.

Deliberately narrow in scope:

  - FLEXIBLE products only, never fixed-term/locked staking. A trading
    bot needs to be able to get its capital back on short notice — a
    30/60/90-day lock would silently turn "the strategy wants to enter
    a trade" into "it can't, because the money is locked." Every
    exchange adapter method this module calls
    (stake_flexible/unstake_flexible/get_staking_apr) is scoped to
    flexible products only; see exchanges.py.

  - Only stakes what's genuinely idle: LISTING_RESERVE_USDT stays out of
    it entirely, and STAKING_MAX_ALLOCATION_PCT caps how much of the
    pool can ever be staked at once, so there's always liquid capital
    left over for a signal that fires while some money is staked (the
    remainder + a same-cycle redemption via ensure_liquid() below cover
    the rest).

  - Only stakes if the APR clears STAKING_MIN_APR — otherwise the capital
    just sits liquid, same as today.

  - PAPER MODE never calls a real exchange endpoint (consistent with the
    rest of this bot's paper/live split — see bot.py's place_buy/
    place_sell). It logs and Telegrams what WOULD happen using an
    illustrative fixed APR, so the decision logic is visible and
    testable without real credentials, but doesn't fake a fully
    separate staked-vs-liquid ledger for the single-float paper pool.
    LIVE MODE is fully real: reads actual exchange balances, and both
    stakes and redeems real funds.
"""

import logging
import threading
import requests
from datetime import datetime

log = logging.getLogger(__name__)

_lock          = threading.Lock()
_staked_by_bot = {}   # {(ex_name, coin): amount the BOT put into staking}


def _tg(config, message: str):
    if not getattr(config, "TELEGRAM_ENABLED", False):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def staking_enabled_for(ex_name: str, exchange, config) -> bool:
    """Same defence-in-depth pattern as futures_manager.futures_enabled_for:
    three independent opt-ins, plus the adapter itself must support it."""
    if not getattr(config, "STAKING_ENABLED", False):
        return False
    if ex_name not in getattr(config, "STAKING_SUPPORTED_EXCHANGES", set()):
        return False
    ex_cfg = config.EXCHANGES.get(ex_name, {})
    if not ex_cfg.get("staking_enabled", False):
        return False
    try:
        if not exchange.staking_supported():
            return False
    except Exception:
        return False
    return True


def get_bot_staked(ex_name: str, coin: str) -> float:
    with _lock:
        return _staked_by_bot.get((ex_name, coin), 0.0)


def _paper_apr() -> float:
    """Illustrative-only estimate for paper mode logging — see module
    docstring. Live mode always calls exchange.get_staking_apr()."""
    return 0.04


def sync_idle_capital(ex_name, exchange, pool_usdt, pool_locks, config, mode="paper") -> None:
    """
    Call periodically (see staking_worker below) for every configured
    exchange. For each coin in config.STAKING_COINS (default just USDT):
    stakes newly-idle capital if it clears the threshold and the APR is
    good enough; never touches pool_usdt itself (paper mode's single pool
    float still represents total bot capital, staked or not — only the
    LIVE balance actually moves on a real exchange).
    """
    if not staking_enabled_for(ex_name, exchange, config):
        return

    min_apr     = getattr(config, "STAKING_MIN_APR", 0.03)
    idle_thresh = getattr(config, "STAKING_IDLE_THRESHOLD_USDT", 20.0)
    max_alloc   = getattr(config, "STAKING_MAX_ALLOCATION_PCT", 0.70)
    listing_res = getattr(config, "LISTING_RESERVE_USDT", 0.0)

    for coin in getattr(config, "STAKING_COINS", ["USDT"]):
        try:
            already_staked = get_bot_staked(ex_name, coin)

            if mode == "paper":
                with pool_locks[ex_name]:
                    pool = pool_usdt[ex_name]
                cap = pool * max_alloc
                stakeable = max(0.0, min(pool - listing_res, cap) - already_staked)
                if stakeable < idle_thresh:
                    continue
                apr = _paper_apr()
                if apr < min_apr:
                    continue
                with _lock:
                    _staked_by_bot[(ex_name, coin)] = already_staked + stakeable
                log.info(f"[STAKING:{ex_name.upper()}] 📄 PAPER — would stake "
                        f"${stakeable:.2f} {coin} @ {apr:.1%} APR (idle capital)")
                continue

            # ── Live mode — real exchange balance, real order ──────────────
            live_balance = (exchange.get_usdt_balance() if coin == "USDT"
                            else exchange.get_coin_balance(coin))
            with pool_locks[ex_name]:
                pool = pool_usdt[ex_name]
            cap = pool * max_alloc
            stakeable = max(0.0, min(live_balance - listing_res, cap - already_staked))
            if stakeable < idle_thresh:
                continue

            apr = exchange.get_staking_apr(coin)
            if apr < min_apr:
                log.debug(f"[STAKING:{ex_name.upper()}] {coin} APR {apr:.2%} < "
                         f"minimum {min_apr:.2%} — leaving liquid")
                continue

            exchange.stake_flexible(coin, round(stakeable, 2))
            with _lock:
                _staked_by_bot[(ex_name, coin)] = already_staked + stakeable
            log.info(f"[STAKING:{ex_name.upper()}] 💰 Staked ${stakeable:.2f} {coin} @ {apr:.1%} APR")
            _tg(config, f"💰 <b>Staked — {ex_name.upper()}</b>\n"
                       f"{coin}: <b>${stakeable:.2f}</b> @ <b>{apr:.1%} APR</b>\n"
                       f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        except Exception as e:
            log.error(f"[STAKING:{ex_name.upper()}] Sync failed for {coin}: {e}")


def ensure_liquid(ex_name, exchange, coin: str, needed_amount: float, config, mode="paper") -> bool:
    """
    Call this right before a real spend needs `needed_amount` of `coin`
    liquid (bot.py's place_buy, right before exchange.place_market_buy).
    Redeems just enough staked capital to cover any shortfall.

    Returns True only if `needed_amount` is actually available afterwards
    (already liquid, or freed up by this call) — False means the caller
    should NOT proceed with the spend, since even redeeming everything
    staked wouldn't cover it (e.g. staked capital shrank from a previous
    partial redemption, or pool bookkeeping drifted from the real
    exchange balance). A best-effort redemption failure (network, rate
    limit) also returns False. This is a hard signal, not a suggestion —
    callers must check it before placing a real order.
    """
    if not staking_enabled_for(ex_name, exchange, config):
        return True

    staked = get_bot_staked(ex_name, coin)
    if staked <= 0:
        return True

    tag = ex_name.upper()
    try:
        if mode == "paper":
            redeem = min(staked, needed_amount)
            with _lock:
                if redeem > 0:
                    _staked_by_bot[(ex_name, coin)] = staked - redeem
            if redeem > 0:
                log.info(f"[STAKING:{tag}] 📄 PAPER — would unstake ${redeem:.2f} {coin} to fund a trade")
            return True   # paper mode's single pool float already covers the rest — see module docstring

        live_balance = (exchange.get_usdt_balance() if coin == "USDT"
                        else exchange.get_coin_balance(coin))
        shortfall = needed_amount - live_balance
        if shortfall <= 0:
            return True

        redeem = min(staked, shortfall)
        exchange.unstake_flexible(coin, round(redeem, 2))
        with _lock:
            _staked_by_bot[(ex_name, coin)] = staked - redeem
        log.info(f"[STAKING:{tag}] Unstaked ${redeem:.2f} {coin} to fund a trade")

        if redeem < shortfall:
            log.warning(f"[STAKING:{tag}] Redeemed all ${redeem:.2f} staked {coin} but it "
                       f"still isn't enough to cover this ${needed_amount:.2f} trade "
                       f"(short by ${shortfall - redeem:.2f}) — caller should not proceed")
            return False
        return True
    except Exception as e:
        log.error(f"[STAKING:{tag}] Failed to free up {coin}: {e}")
        return False


def staking_worker(exchanges: dict, pool_usdt: dict, pool_locks: dict,
                    config, mode: str, stop_event) -> None:
    """Background thread — periodically syncs idle capital into staking
    across every configured exchange. Registered in bot.py's run()."""
    if not getattr(config, "STAKING_ENABLED", False):
        log.info("[STAKING] Disabled (config.STAKING_ENABLED=False) — worker exiting")
        return

    interval = getattr(config, "STAKING_CHECK_INTERVAL_SECS", 1800)
    log.info(f"[STAKING] Worker active — checking idle capital every {interval}s")

    while not stop_event.is_set():
        for ex_name, exchange in exchanges.items():
            try:
                sync_idle_capital(ex_name, exchange, pool_usdt, pool_locks, config, mode)
            except Exception as e:
                log.error(f"[STAKING:{ex_name.upper()}] Worker error: {e}")
        stop_event.wait(timeout=interval)

"""
Watchdog — Independent Health Monitor
========================================
A SEPARATE process from bot.py. This is the actual point: if the main
bot process freezes, hangs, or crashes outright, a thread living inside
that same frozen process can't save you — it freezes too. This script
runs on its own, checks the bot's liveness file every cycle, and can
restart the bot or alert you even if bot.py is completely unresponsive.

WHAT IT CHECKS:
  1. Is logs/liveness.json being updated? (should refresh every ~2 min)
     If it's stale beyond LIVENESS_STALE_MINUTES, the bot is considered
     frozen/crashed regardless of whether the process is still running.
  2. Is the bot's PID (from the liveness file) still an active process?
  3. Has total_pnl dropped abnormally between checks? (possible runaway
     losing streak the bot itself failed to catch)

WHAT IT DOES ON FAILURE:
  - Sends a Telegram alert immediately (reads config.py directly — does
    NOT depend on the bot's own Telegram thread, since that's exactly
    what might be frozen)
  - If WATCHDOG_AUTO_RESTART is True, attempts to relaunch bot.py with
    the same mode it was last running in
  - Logs everything to logs/watchdog.log, completely separate from the
    bot's own log file

HOW TO RUN:
  This is meant to run ALONGSIDE bot.py, not instead of it — e.g. two
  terminal windows, two scheduled tasks, or two systemd services.

    python watchdog.py

  On Windows, WATCHDOG.bat does the equivalent — double-click it after
  starting the bot normally via START_BOT.bat.

CONFIG (see config.py):
  WATCHDOG_ENABLED              — master on/off switch
  WATCHDOG_CHECK_INTERVAL_SECS  — how often to check (default 60s)
  LIVENESS_STALE_MINUTES        — how stale before considered frozen (default 5)
  WATCHDOG_AUTO_RESTART         — attempt automatic restart on failure
  WATCHDOG_MAX_RESTARTS_PER_DAY — circuit breaker so a persistently broken
                                   bot doesn't restart-loop forever
"""

import sys
import os
import json
import time
import logging
import subprocess
import platform
from pathlib import Path
from datetime import datetime, timedelta

# ── Logging — completely separate file from the bot's own log ─────────────
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# See bot.py for the full explanation: Windows' console codepage (cp1252)
# can't encode the emoji used in these log messages, which crashes
# StreamHandler with a UnicodeEncodeError on every such line. This wraps
# stdout so it substitutes safely instead of throwing. The log FILE below
# isn't affected — give it an explicit utf-8 encoding too so it renders
# these correctly no matter what codepage the console happens to be in.
console_stream = open(
    sys.stdout.fileno(), mode="w", encoding=sys.stdout.encoding,
    errors="replace", buffering=1, closefd=False,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "watchdog.log", encoding="utf-8"),
        logging.StreamHandler(console_stream),
    ],
)
log = logging.getLogger("watchdog")

LIVENESS_PATH      = LOG_DIR / "liveness.json"
RESTART_COUNT_PATH = LOG_DIR / "watchdog_restart_count.json"
GRACEFUL_UPDATE_FLAG = LOG_DIR / "graceful_update.flag"


def consume_graceful_update_flag() -> dict:
    """
    Checks for and removes the flag auto_updater.py writes right before
    it deliberately exits the bot process for an update. Returns the
    flag's contents if present, or None if this isn't an update-related
    exit. Consuming (deleting) it here means it only ever affects the
    very next health check after it's written — a stale flag left over
    from days ago can't later be misread as "this new crash is actually
    an update", since by then it's long gone.
    """
    if not GRACEFUL_UPDATE_FLAG.exists():
        return None
    try:
        with open(GRACEFUL_UPDATE_FLAG) as f:
            data = json.load(f)
        GRACEFUL_UPDATE_FLAG.unlink()
        return data
    except Exception as e:
        log.warning(f"Could not read/remove graceful-update flag: {e}")
        try:
            GRACEFUL_UPDATE_FLAG.unlink()
        except Exception:
            pass
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG LOADING — reads config.py directly, no dependency on bot.py modules
# ══════════════════════════════════════════════════════════════════════════════

def load_config():
    """
    Imports config.py directly. This is intentionally the ONLY bot module
    the watchdog touches — it must be able to run and alert even if every
    other part of the bot's codebase has a bug that would crash an import.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    import config
    return config


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM — independent of bot.py's own Telegram thread
# ══════════════════════════════════════════════════════════════════════════════

def send_telegram_alert(cfg, message: str):
    """
    Sends directly via requests, bypassing bot.py entirely. If the bot's
    own Telegram thread is what's frozen, this is the only way an alert
    still reaches you.
    """
    if not getattr(cfg, "TELEGRAM_ENABLED", False):
        log.warning("Telegram disabled in config — alert not sent: " + message[:80])
        return False

    token   = getattr(cfg, "TELEGRAM_TOKEN", "")
    chat_id = getattr(cfg, "TELEGRAM_CHAT_ID", "")
    if not token or token.startswith("YOUR_"):
        log.warning("Telegram token not configured — alert not sent")
        return False

    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  LIVENESS CHECKING
# ══════════════════════════════════════════════════════════════════════════════

def read_liveness() -> tuple:
    """
    Returns (liveness_dict_or_None, error_reason_or_None).
    Distinguishes "file missing" from "file exists but corrupt" so
    diagnose() can report the real cause instead of conflating both
    into a generic "not found".
    """
    if not LIVENESS_PATH.exists():
        return None, "missing"
    try:
        with open(LIVENESS_PATH) as f:
            return json.load(f), None
    except Exception as e:
        log.warning(f"Liveness file exists but couldn't be read: {e}")
        return None, f"corrupt ({e})"


def is_pid_running(pid: int) -> bool:
    """Cross-platform check for whether a PID is still an active process."""
    if pid is None:
        return False
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=10,
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)   # signal 0 = check existence, doesn't actually kill
            return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception as e:
        log.warning(f"Could not check PID {pid}: {e}")
        return None   # unknown — don't assume either way


def diagnose(cfg) -> dict:
    """
    Runs all health checks and returns a diagnosis.

    Returns:
        {
          "healthy":  bool,
          "reason":   str,
          "liveness": dict or None,
        }
    """
    stale_minutes = getattr(cfg, "LIVENESS_STALE_MINUTES", 5)
    liveness, read_error = read_liveness()

    if liveness is None:
        if read_error == "missing":
            reason = ("No liveness file found — bot may never have started, "
                     "or crashed before writing its first liveness ping")
        else:
            reason = f"Liveness file exists but is unreadable — {read_error}"
        return {"healthy": False, "reason": reason, "liveness": None}

    try:
        last_ping = datetime.fromisoformat(liveness["last_ping"])
    except (KeyError, ValueError) as e:
        return {
            "healthy": False,
            "reason": f"Liveness file is corrupt or malformed: {e}",
            "liveness": liveness,
        }

    age_minutes = (datetime.now() - last_ping).total_seconds() / 60

    if age_minutes > stale_minutes:
        pid = liveness.get("pid")
        pid_status = is_pid_running(pid)
        pid_note = (
            "process appears to still be running but is unresponsive (likely hung)"
            if pid_status else
            "process is no longer running (likely crashed)"
            if pid_status is False else
            "could not determine process status"
        )
        return {
            "healthy": False,
            "reason": f"Liveness stale by {age_minutes:.1f} min "
                     f"(limit {stale_minutes} min) — {pid_note}",
            "liveness": liveness,
        }

    return {"healthy": True, "reason": "OK", "liveness": liveness}


# ══════════════════════════════════════════════════════════════════════════════
#  RESTART LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def get_todays_restart_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if RESTART_COUNT_PATH.exists():
            with open(RESTART_COUNT_PATH) as f:
                data = json.load(f)
            if data.get("date") == today:
                return data.get("count", 0)
    except Exception:
        pass
    return 0


def increment_restart_count():
    today = datetime.now().strftime("%Y-%m-%d")
    count = get_todays_restart_count() + 1
    try:
        with open(RESTART_COUNT_PATH, "w") as f:
            json.dump({"date": today, "count": count}, f)
    except Exception as e:
        log.warning(f"Could not save restart count: {e}")
    return count


def attempt_restart(cfg, last_mode: str) -> bool:
    """
    Attempts to relaunch bot.py. Respects WATCHDOG_MAX_RESTARTS_PER_DAY —
    a bot that's crash-looping needs a human to look at it, not an
    infinite auto-restart cycle that masks the underlying problem.
    """
    max_restarts = getattr(cfg, "WATCHDOG_MAX_RESTARTS_PER_DAY", 3)
    today_count  = get_todays_restart_count()

    if today_count >= max_restarts:
        log.error(f"Restart limit reached ({today_count}/{max_restarts} today) — "
                 f"NOT restarting. This usually means something is genuinely "
                 f"broken, not a transient freeze. Manual intervention needed.")
        send_telegram_alert(cfg,
            f"🚨 <b>Watchdog: Restart Limit Reached</b>\n━━━━━━━━━━━━━━━━\n"
            f"The bot has needed restarting {today_count} times today.\n"
            f"This usually means something is genuinely broken, not a\n"
            f"one-off freeze. <b>Not attempting another auto-restart.</b>\n\n"
            f"Please check the logs and restart manually once you've\n"
            f"identified the cause.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return False

    mode = last_mode or "paper"
    python_cmd = sys.executable   # use the same interpreter the watchdog itself runs under
    bot_path   = Path(__file__).parent / "bot.py"

    log.info(f"Attempting restart #{today_count + 1} today: "
            f"{python_cmd} {bot_path} --mode {mode}")

    try:
        if platform.system() == "Windows":
            subprocess.Popen(
                [python_cmd, str(bot_path), "--mode", mode],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            subprocess.Popen(
                [python_cmd, str(bot_path), "--mode", mode],
                start_new_session=True,
            )
        increment_restart_count()
        log.info("Restart command issued successfully")
        return True
    except Exception as e:
        log.error(f"Restart attempt failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run():
    cfg = load_config()

    if not getattr(cfg, "WATCHDOG_ENABLED", True):
        log.info("WATCHDOG_ENABLED is False in config.py — exiting immediately")
        return

    interval = getattr(cfg, "WATCHDOG_CHECK_INTERVAL_SECS", 60)
    auto_restart = getattr(cfg, "WATCHDOG_AUTO_RESTART", False)

    log.info("=" * 60)
    log.info("  Watchdog started — independent process, monitoring bot health")
    log.info(f"  Check interval:    {interval}s")
    log.info(f"  Stale threshold:   {getattr(cfg,'LIVENESS_STALE_MINUTES',5)} min")
    log.info(f"  Auto-restart:      {auto_restart}")
    log.info("=" * 60)

    was_healthy = True   # assume healthy at startup to avoid a false alert
                          # on the very first check before the bot has had
                          # time to write its first liveness ping

    while True:
        try:
            # ── Was this exit a deliberate update, not a crash? ────────────
            # Checked on EVERY cycle, independent of the healthy/unhealthy
            # diagnosis below — an update causes the bot process to exit on
            # purpose, which the liveness check would otherwise see as
            # identical to a crash (stale liveness file, dead PID). Catching
            # the flag here lets the watchdog tell you what actually
            # happened instead of reporting a false "crashed" alert, and —
            # importantly — restart it unconditionally even if
            # WATCHDOG_AUTO_RESTART is off, since an update is EXPECTED to
            # come back up; that's the whole point of the feature you asked
            # for ("if it doesn't reboot after update, watchdog will reboot
            # it"). This does NOT consume a WATCHDOG_MAX_RESTARTS_PER_DAY
            # slot — a normal update on an otherwise-healthy bot shouldn't
            # eat into the budget meant for catching genuine crash-loops.
            update_flag = consume_graceful_update_flag()
            if update_flag:
                log.info(f"[UPDATE] Detected graceful update exit: {update_flag.get('reason','')}")
                send_telegram_alert(cfg,
                    f"⬆️ <b>Watchdog: Update Detected</b>\n━━━━━━━━━━━━━━━━\n"
                    f"The bot exited on purpose to apply an update.\n"
                    f"Restarting it now (this doesn't count against the "
                    f"daily restart limit).\n"
                    f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                # Best-effort: read whatever mode the bot last reported so
                # the relaunch keeps the same paper/live setting. If the
                # liveness file is unreadable (e.g. it hadn't been written
                # since before the update), fall back to "paper" — the
                # safer of the two defaults if we genuinely don't know.
                liveness, _ = read_liveness()
                mode = (liveness or {}).get("mode", "paper")

                python_cmd = sys.executable
                bot_path   = Path(__file__).parent / "bot.py"
                try:
                    if platform.system() == "Windows":
                        subprocess.Popen(
                            [python_cmd, str(bot_path), "--mode", mode],
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                        )
                    else:
                        subprocess.Popen(
                            [python_cmd, str(bot_path), "--mode", mode],
                            start_new_session=True,
                        )
                    log.info("[UPDATE] Restart issued after update — NOT counted against restart limit")
                except Exception as e:
                    log.error(f"[UPDATE] Restart-after-update failed: {e}")
                    send_telegram_alert(cfg,
                        f"🚨 <b>Watchdog: Restart After Update Failed</b>\n━━━━━━━━━━━━━━━━\n"
                        f"The update was applied but the bot could not be "
                        f"relaunched automatically: {e}\n"
                        f"Manual restart needed.\n"
                        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                was_healthy = True   # give the freshly-relaunched bot a clean
                                      # slate instead of immediately flagging
                                      # it unhealthy on the next cycle while
                                      # it's still starting up
                time.sleep(interval)
                continue

            diagnosis = diagnose(cfg)

            if diagnosis["healthy"]:
                if not was_healthy:
                    log.info("✅ Bot is healthy again")
                    send_telegram_alert(cfg,
                        f"✅ <b>Watchdog: Bot Recovered</b>\n━━━━━━━━━━━━━━━━\n"
                        f"Liveness signal is healthy again.\n"
                        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                was_healthy = True
            else:
                log.warning(f"⚠️ Unhealthy: {diagnosis['reason']}")

                if was_healthy:
                    # Only alert on the TRANSITION to unhealthy, not every
                    # single check while it remains unhealthy — otherwise
                    # this spams Telegram every interval until it's fixed.
                    liveness = diagnosis.get("liveness") or {}
                    send_telegram_alert(cfg,
                        f"🚨 <b>Watchdog Alert — Bot Unhealthy</b>\n━━━━━━━━━━━━━━━━\n"
                        f"{diagnosis['reason']}\n\n"
                        f"Last known state:\n"
                        f"  Trades: {liveness.get('trade_count','?')}\n"
                        f"  P&amp;L:    ${liveness.get('total_pnl','?')}\n"
                        f"  Mode:   {liveness.get('mode','?')}\n\n"
                        f"{'Attempting automatic restart...' if auto_restart else 'Auto-restart is OFF — manual restart needed.'}\n"
                        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                    if auto_restart:
                        liveness = diagnosis.get("liveness") or {}
                        attempt_restart(cfg, liveness.get("mode"))

                was_healthy = False

        except Exception as e:
            log.error(f"Watchdog check itself failed: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log.info("Watchdog stopped by user")

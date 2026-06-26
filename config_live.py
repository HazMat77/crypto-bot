"""
Live Config Reloader
======================
THE PROBLEM THIS FIXES:
  Every module does `import config` once at startup. Python caches that
  module object in sys.modules — re-writing config.py on disk afterward
  (which /aggressive, /safe, regime auto-adaptation, the monthly AI
  optimizer, and the weekly market study all do) has ZERO effect on the
  already-running process. Every "✅ Changes Applied" message was true
  about the file on disk, but false about what the bot was actually doing.

THE FIX:
  After ANY code path writes to config.py, it must call reload_config()
  below. This does importlib.reload(config) IN PLACE, which mutates the
  SAME module object every other file already imported and is holding a
  reference to — so config.RSI_BUY everywhere in bot.py, strategy_engine.py,
  risk_manager.py etc. instantly reflects the new value, with zero changes
  needed at any of the 50+ existing access points across the codebase.

USAGE:
  # Anywhere that just wrote new values into config.py:
  from config_live import reload_config
  reload_config()
  # That's it — every `config.XXX` read anywhere in the running bot now
  # sees the new value on its very next access.
"""

import logging
import threading

log = logging.getLogger(__name__)

_reload_lock = threading.Lock()


def reload_config():
    """
    Reloads the config module in place. Must be called immediately after
    any write to config.py for that change to actually take effect in the
    running bot. Thread-safe — multiple self-tuning systems can call this
    concurrently without corrupting each other's writes.
    """
    with _reload_lock:
        try:
            import config
            import importlib
            importlib.reload(config)
            log.info("[CONFIG_LIVE] ✅ config module reloaded — new values are now live "
                     "for every thread without restarting the bot")
            return True
        except Exception as e:
            log.error(f"[CONFIG_LIVE] ❌ Reload failed — bot is still running on OLD "
                     f"settings despite config.py having been changed on disk: {e}")
            return False


def verify_live_matches_disk() -> dict:
    """
    Diagnostic helper — compares what's in the live config module
    against what's actually written in config.py on disk right now.
    If these differ, the running bot is NOT using the settings you
    think it's using. Used by /diag to surface this exact failure mode.
    """
    import re
    try:
        import config
        with open("config.py", "r", encoding="utf-8") as f:
            disk_content = f.read()

        mismatches = []
        # Check the handful of values that self-tuning commands actually write
        watched_keys = [
            "RSI_BUY", "RSI_SELL", "NORMAL_RSI_BUY", "NORMAL_RSI_SELL",
            "AGGRESSIVE_RSI_BUY", "AGGRESSIVE_RSI_SELL",
            "STOP_LOSS_PCT", "NORMAL_STOP_LOSS", "AGGRESSIVE_STOP_LOSS",
            "TAKE_PROFIT_PCT", "NORMAL_TAKE_PROFIT", "AGGRESSIVE_TAKE_PROFIT",
            "AGGRESSIVE_POOL_PCT", "ENGINE_CONFIDENCE_MIN",
        ]
        for key in watched_keys:
            m = re.search(rf"^{re.escape(key)}\s*=\s*([0-9.]+)", disk_content, re.MULTILINE)
            if not m:
                continue
            disk_value = float(m.group(1))
            live_value = getattr(config, key, None)
            if live_value is not None and abs(float(live_value) - disk_value) > 1e-9:
                mismatches.append({
                    "key": key,
                    "live_value": live_value,
                    "disk_value": disk_value,
                })

        return {
            "in_sync": len(mismatches) == 0,
            "mismatches": mismatches,
        }
    except Exception as e:
        return {"in_sync": None, "error": str(e)}

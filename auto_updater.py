"""
Auto-Updater
=============
Checks the bot's git remote for new commits and, if found, pulls the
update and exits cleanly so the watchdog (or your process supervisor)
relaunches bot.py on the new code.

THIS MODULE NEVER RESTARTS THE PROCESS ITSELF — it only pulls the new
code and exits. The actual relaunch is the watchdog's job (watchdog.py),
which is a SEPARATE process and can therefore restart bot.py even though
bot.py's own process has just exited. This split exists for the exact
same reason watchdog.py itself is a separate process from bot.py: a
process can't reliably relaunch itself from inside its own exit path.

HOW IT DECIDES THERE'S AN UPDATE:
  1. `git fetch` the configured remote/branch (read-only, no changes yet)
  2. Compare the local HEAD commit hash against the remote's HEAD
  3. If they differ, there's an update available

WHAT HAPPENS WHEN AN UPDATE IS FOUND (per your config — see
AUTO_UPDATE_REQUIRE_APPROVAL in config.py):
  - AUTO_UPDATE_REQUIRE_APPROVAL = False (your current setting): pulls
    immediately in both paper AND live mode, sends a Telegram notice
    after the fact, then exits so the watchdog relaunches it. There is
    NO waiting period and NO Y/N gate before this happens — by design,
    per your explicit choice. If you want a review window before updates
    take effect (especially in live mode), set this to True instead.
  - AUTO_UPDATE_REQUIRE_APPROVAL = True: sends a Y/N approval request
    (same mechanism as approval_gate.py) and only pulls after you reply Y.

SAFETY NOTES:
  - Refuses to pull if there are uncommitted local changes to tracked
    files (e.g. you hand-edited config.py and haven't committed it) —
    a `git pull` over local edits can silently discard them or fail with
    a merge conflict mid-update. bot_secrets.py and logs/ are gitignored
    and untouched by this either way, since they're never tracked.
  - Writes a "graceful_update" flag file BEFORE exiting, which the
    watchdog reads once to distinguish "I exited on purpose for an
    update" from "I crashed" — so a normal update doesn't consume one of
    WATCHDOG_MAX_RESTARTS_PER_DAY's restart slots, and so the Telegram
    message you get says "updating" instead of "crashed".
  - If `git` isn't available, or this directory isn't a git repo (e.g.
    you're running from the .zip directly instead of a git clone), the
    update check logs once and does nothing — it never errors the bot.

CONFIG (see config.py):
  AUTO_UPDATE_ENABLED            — master on/off switch
  AUTO_UPDATE_CHECK_INTERVAL_SECS— how often to check (default 1 hour)
  AUTO_UPDATE_REMOTE             — git remote name (default "origin")
  AUTO_UPDATE_BRANCH             — branch to track (default "main")
  AUTO_UPDATE_REQUIRE_APPROVAL   — wait for Y/N instead of pulling immediately
"""

import subprocess
import logging
import json
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

GRACEFUL_UPDATE_FLAG = Path("logs/graceful_update.flag")


def _run_git(args: list, cwd: Path) -> tuple:
    """Runs a git command, returns (success, stdout_or_stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args, cwd=str(cwd),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except FileNotFoundError:
        return False, "git is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return False, "git command timed out"
    except Exception as e:
        return False, str(e)


def is_git_repo(repo_dir: Path) -> bool:
    ok, _ = _run_git(["rev-parse", "--is-inside-work-tree"], repo_dir)
    return ok


def has_local_changes(repo_dir: Path) -> bool:
    """
    True if there are uncommitted changes to TRACKED files. Untracked
    files (bot_secrets.py, logs/, __pycache__/ — all gitignored) don't
    count, since `git status --porcelain` only lists tracked-file
    modifications and untracked files separately; we only check the
    former here on purpose.
    """
    ok, out = _run_git(["diff", "--name-only", "HEAD"], repo_dir)
    if not ok:
        return False   # can't tell — caller treats this as "don't know, proceed cautiously"
    return bool(out.strip())


def check_for_update(cfg, repo_dir: Path = None) -> dict:
    """
    Fetches the remote and compares commit hashes. Does NOT pull —
    read-only check. Returns:
      {
        "update_available": bool,
        "local_commit":  str or None,
        "remote_commit": str or None,
        "reason":        str   (only set when update_available is False
                                 or the check itself couldn't run)
      }
    """
    repo_dir = repo_dir or Path(__file__).parent
    remote   = getattr(cfg, "AUTO_UPDATE_REMOTE", "origin")
    branch   = getattr(cfg, "AUTO_UPDATE_BRANCH", "main")

    if not is_git_repo(repo_dir):
        return {"update_available": False, "local_commit": None,
                "remote_commit": None,
                "reason": "Not a git repository — auto-update requires "
                         "the bot to be a git clone, not a standalone .zip extract"}

    ok, _ = _run_git(["fetch", remote, branch], repo_dir)
    if not ok:
        return {"update_available": False, "local_commit": None,
                "remote_commit": None,
                "reason": f"git fetch failed — likely no network or remote '{remote}' unreachable"}

    ok_local, local_commit = _run_git(["rev-parse", "HEAD"], repo_dir)
    ok_remote, remote_commit = _run_git(["rev-parse", f"{remote}/{branch}"], repo_dir)

    if not (ok_local and ok_remote):
        return {"update_available": False, "local_commit": None,
                "remote_commit": None, "reason": "Could not read local/remote commit hashes"}

    if local_commit == remote_commit:
        return {"update_available": False, "local_commit": local_commit,
                "remote_commit": remote_commit, "reason": "Already up to date"}

    return {"update_available": True, "local_commit": local_commit,
            "remote_commit": remote_commit, "reason": ""}


def _mark_graceful_update(reason: str):
    """
    Writes a flag the watchdog checks once on its next health read to
    know this exit was a deliberate update, not a crash. The watchdog
    is responsible for deleting this file after reading it — see
    watchdog.py's consume_graceful_update_flag().
    """
    GRACEFUL_UPDATE_FLAG.parent.mkdir(exist_ok=True)
    try:
        with open(GRACEFUL_UPDATE_FLAG, "w") as f:
            json.dump({"time": datetime.now().isoformat(), "reason": reason}, f)
    except Exception as e:
        log.warning(f"[UPDATE] Could not write graceful-update flag: {e}")


def perform_update(cfg, repo_dir: Path = None, tg_send_fn=None) -> bool:
    """
    Pulls the update and prepares for a clean exit. Returns True if the
    pull succeeded (caller should exit the process right after this
    returns True so the watchdog relaunches on the new code); False if
    anything stopped the pull (caller should NOT exit — keep running on
    the current code).
    """
    repo_dir = repo_dir or Path(__file__).parent
    remote   = getattr(cfg, "AUTO_UPDATE_REMOTE", "origin")
    branch   = getattr(cfg, "AUTO_UPDATE_BRANCH", "main")

    if has_local_changes(repo_dir):
        msg = ("⚠️ <b>Auto-Update Skipped</b>\n━━━━━━━━━━━━━━━━\n"
               "An update is available, but this machine has uncommitted "
               "local changes to tracked files (e.g. a hand-edited config.py). "
               "Pulling now risks losing those edits or hitting a merge conflict.\n\n"
               "Commit or stash your local changes, then the next check will "
               "pull normally.")
        log.warning("[UPDATE] Skipped — uncommitted local changes present")
        if tg_send_fn:
            tg_send_fn(msg)
        return False

    ok, out = _run_git(["pull", remote, branch], repo_dir)
    if not ok:
        log.error(f"[UPDATE] git pull failed: {out}")
        if tg_send_fn:
            tg_send_fn(
                f"❌ <b>Auto-Update Failed</b>\n━━━━━━━━━━━━━━━━\n"
                f"git pull failed:\n<code>{out[:300]}</code>\n\n"
                f"Bot continues running on the current version."
            )
        return False

    log.info(f"[UPDATE] Pulled successfully: {out}")
    _mark_graceful_update(f"Updated via auto-updater: {out[:200]}")

    if tg_send_fn:
        try:
            from version import __version__ as new_version
        except Exception:
            new_version = "unknown"
        tg_send_fn(
            f"⬆️ <b>Auto-Update Applied</b>\n━━━━━━━━━━━━━━━━\n"
            f"Pulled the latest version from {remote}/{branch}.\n"
            f"New version: <b>v{new_version}</b>\n\n"
            f"Restarting now to load the new code — the watchdog will bring "
            f"it back up automatically. You may see a brief 'unhealthy' "
            f"alert during the restart window; that's expected.\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    return True


def update_check_worker(cfg, stop_event, tg_send_fn=None):
    """
    Background thread: periodically checks for and applies updates.
    Runs inside bot.py's process. On a successful pull, this terminates
    the WHOLE PROCESS via os._exit() rather than returning or raising —
    a plain return/exception from inside a non-main thread does NOT stop
    the process (Python just ends that one thread silently), and os._exit
    is the only way a non-main thread can force an immediate, full-process
    exit. This deliberately skips normal cleanup (atexit, finally blocks)
    so the watchdog sees a clean process exit right away and relaunches
    on the new code, rather than waiting on other threads that might be
    mid-network-call to wind down first.
    """
    import os

    interval = getattr(cfg, "AUTO_UPDATE_CHECK_INTERVAL_SECS", 3600)

    if not getattr(cfg, "AUTO_UPDATE_ENABLED", False):
        log.info("[UPDATE] AUTO_UPDATE_ENABLED is False — auto-updater not running")
        return

    log.info(f"[UPDATE] Auto-updater active — checking every {interval}s")

    while not stop_event.wait(timeout=interval):
        try:
            result = check_for_update(cfg)
            if not result["update_available"]:
                if result["reason"] and result["reason"] != "Already up to date":
                    log.debug(f"[UPDATE] {result['reason']}")
                continue

            log.info(f"[UPDATE] Update available: {result['local_commit'][:8]} "
                     f"-> {result['remote_commit'][:8]}")

            if getattr(cfg, "AUTO_UPDATE_REQUIRE_APPROVAL", False):
                import approval_gate
                outcome = approval_gate.request_approval(
                    change_type   = "auto_update",
                    title         = "Auto-Update Available",
                    what_learned  = f"A new commit is available on "
                                    f"{getattr(cfg,'AUTO_UPDATE_REMOTE','origin')}/"
                                    f"{getattr(cfg,'AUTO_UPDATE_BRANCH','main')}.",
                    why_change    = "Pulling updates the bot's code to the latest "
                                    "pushed version.",
                    proposed      = {"commit": result["remote_commit"][:8]},
                    current       = {"commit": result["local_commit"][:8]},
                    confidence    = 100,
                    config        = cfg,
                )
                if outcome != "approved":
                    log.info(f"[UPDATE] Update not applied (outcome: {outcome})")
                    continue

            applied = perform_update(cfg, tg_send_fn=tg_send_fn)
            if applied:
                log.info("[UPDATE] Exiting process now so the watchdog relaunches on new code")
                os._exit(0)   # see docstring — must be os._exit, not sys.exit/return,
                               # to actually terminate the process from this thread

        except Exception as e:
            log.error(f"[UPDATE] Update check failed: {e}")

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

WHAT HAPPENS WHEN AN UPDATE IS FOUND (per AUTO_UPDATE_MODE in config.py):
  - "notify_only" (DEFAULT): never pulls on its own, in either paper or
    live mode. Sends one Telegram notice per new commit and waits — the
    person running the bot applies it themselves via /update whenever
    it's convenient, or by running `git pull` by hand. Nothing changes
    on disk and nothing restarts until they take that action.
  - "require_approval": sends a Y/N approval request (same mechanism as
    monthly strategy reviews) and pulls only after an explicit Y reply.
  - "auto_apply": pulls immediately the moment an update is found, in
    BOTH paper and live mode, with no review window — notifies only
    after the pull, then restarts. Highest risk: a single push takes
    effect on every running bot in this mode within
    AUTO_UPDATE_CHECK_INTERVAL_SECS. Intended only for a bot you alone run.

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
  AUTO_UPDATE_ENABLED            — master on/off switch for CHECKING (default True)
  AUTO_UPDATE_CHECK_INTERVAL_SECS— how often to check (default 1 hour)
  AUTO_UPDATE_REMOTE             — git remote name (default "origin")
  AUTO_UPDATE_BRANCH             — branch to track (default "main")
  AUTO_UPDATE_MODE               — "notify_only" (default) | "require_approval" | "auto_apply"
"""

import re
import shutil
import subprocess
import logging
import json
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

GRACEFUL_UPDATE_FLAG = Path("logs/graceful_update.flag")

# Files/dirs a zip-based update must never touch — the local, per-machine
# state that a fresh zip extract from GitHub would otherwise clobber.
# Mirrors what git's own update path already leaves alone for free
# (bot_secrets.py/logs/ are gitignored; config.py is handled separately
# via backup+restore since it IS tracked).
_ZIP_UPDATE_PRESERVE = {
    "config.py", "bot_secrets.py", "logs", "python_path.txt",
    ".git", "venv", ".venv", "__pycache__",
}


def _github_repo_slug(cfg) -> str:
    return getattr(cfg, "AUTO_UPDATE_GITHUB_REPO", "HazMat77/crypto-bot")


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
    True if there are uncommitted changes to TRACKED files, EXCLUDING
    config.py. config.py is tracked (it ships with default values) but
    the GUI now writes exchange credentials, AI settings, and bot
    settings directly into it via settings_writer.py — so on any machine
    that's used the GUI, config.py always shows as locally modified.
    Without excluding it here, that one file would permanently block
    every future update for every GUI user. config.py itself is handled
    separately in perform_update() (backed up and restored around the
    pull) so local settings survive regardless.
    """
    ok, out = _run_git(["diff", "--name-only", "HEAD", "--", ".", ":!config.py"], repo_dir)
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
        return _check_for_update_zip(cfg)

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


# ══════════════════════════════════════════════════════════════════════════════
#  ZIP FALLBACK — for installs that are a plain .zip extract, not a git
#  clone. Same public interface (check_for_update/perform_update return the
#  same dict shape / True-False), so callers (GUI, /update command) never
#  need to know which path is actually in use.
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_remote_version(cfg) -> str:
    """Fetches version.py's __version__ straight off GitHub (raw file, no
    git/API auth needed) for the configured branch. Returns None on any
    failure — network down, repo renamed, etc."""
    import requests
    repo   = _github_repo_slug(cfg)
    branch = getattr(cfg, "AUTO_UPDATE_BRANCH", "main")
    url    = f"https://raw.githubusercontent.com/{repo}/{branch}/version.py"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', resp.text)
        return m.group(1) if m else None
    except Exception as e:
        log.warning(f"[UPDATE] Could not fetch remote version.py: {e}")
        return None


def _local_version() -> str:
    try:
        from version import __version__
        return __version__
    except Exception:
        return None


def _check_for_update_zip(cfg) -> dict:
    local_version  = _local_version()
    remote_version = _fetch_remote_version(cfg)

    if not remote_version:
        return {"update_available": False, "local_commit": local_version,
                "remote_commit": None,
                "reason": "Could not reach GitHub to check for updates "
                         "(no network, or repo unreachable)"}
    if not local_version:
        return {"update_available": False, "local_commit": None,
                "remote_commit": remote_version,
                "reason": "Could not read local version.py"}

    if local_version == remote_version:
        return {"update_available": False, "local_commit": local_version,
                "remote_commit": remote_version, "reason": "Already up to date"}

    return {"update_available": True, "local_commit": local_version,
            "remote_commit": remote_version, "reason": ""}


def perform_update_zip(cfg, repo_dir: Path) -> tuple:
    """
    Downloads the configured branch's zipball from GitHub, extracts it,
    and copies files over repo_dir — skipping anything in
    _ZIP_UPDATE_PRESERVE (config.py, bot_secrets.py, logs/, etc.) so local
    settings and credentials survive untouched. Returns (success, message).
    """
    import requests
    import zipfile
    import tempfile

    repo   = _github_repo_slug(cfg)
    branch = getattr(cfg, "AUTO_UPDATE_BRANCH", "main")
    url    = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"

    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        return False, f"Download failed: {e}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "update.zip"
        zip_path.write_bytes(resp.content)

        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_path)
        except Exception as e:
            return False, f"Could not extract update archive: {e}"

        # GitHub zipballs extract into a single top-level dir named
        # "{repo-name}-{branch}" — find it rather than hardcoding it.
        extracted_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
        if not extracted_dirs:
            return False, "Update archive was empty after extraction"
        src_root = extracted_dirs[0]

        try:
            for item in src_root.iterdir():
                if item.name in _ZIP_UPDATE_PRESERVE:
                    continue
                dest = repo_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
        except Exception as e:
            return False, f"Update downloaded but copying files failed: {e}"

    return True, "Updated successfully"


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

    if not is_git_repo(repo_dir):
        ok, out = perform_update_zip(cfg, repo_dir)
        if not ok:
            log.error(f"[UPDATE] zip update failed: {out}")
            if tg_send_fn:
                tg_send_fn(
                    f"❌ <b>Auto-Update Failed</b>\n━━━━━━━━━━━━━━━━\n"
                    f"{out}\n\nBot continues running on the current version."
                )
            return False
        log.info(f"[UPDATE] zip update applied: {out}")
        _mark_graceful_update(f"Updated via auto-updater (zip): {out}")
        if tg_send_fn:
            _notify_update_applied(tg_send_fn, remote, branch)
        return True

    if has_local_changes(repo_dir):
        msg = ("⚠️ <b>Auto-Update Skipped</b>\n━━━━━━━━━━━━━━━━\n"
               "An update is available, but this machine has uncommitted "
               "local changes to a tracked file other than config.py. "
               "Pulling now risks losing those edits or hitting a merge conflict.\n\n"
               "Commit or stash your local changes, then the next check will "
               "pull normally.")
        log.warning("[UPDATE] Skipped — uncommitted local changes present")
        if tg_send_fn:
            tg_send_fn(msg)
        return False

    # config.py is tracked but GUI-written (see has_local_changes' docstring)
    # — back it up and restore it after the pull so a user's exchange/AI/bot
    # settings survive regardless of what changed in config.py upstream.
    config_path   = repo_dir / "config.py"
    config_backup = config_path.read_text() if config_path.exists() else None

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

    if config_backup is not None:
        config_path.write_text(config_backup)

    log.info(f"[UPDATE] Pulled successfully: {out}")
    _mark_graceful_update(f"Updated via auto-updater: {out[:200]}")

    if tg_send_fn:
        _notify_update_applied(tg_send_fn, remote, branch)
    return True


def _notify_update_applied(tg_send_fn, remote: str, branch: str):
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
    from pathlib import Path
    import json as _json

    interval = getattr(cfg, "AUTO_UPDATE_CHECK_INTERVAL_SECS", 3600)
    mode     = getattr(cfg, "AUTO_UPDATE_MODE", "notify_only")

    if not getattr(cfg, "AUTO_UPDATE_ENABLED", False):
        log.info("[UPDATE] AUTO_UPDATE_ENABLED is False — auto-updater not running")
        return

    log.info(f"[UPDATE] Auto-updater active — checking every {interval}s, mode={mode}")

    # Tracks the remote commit hash we last sent a notify_only Telegram
    # message for, so a pending update doesn't re-notify every single
    # interval until the person actually acts on it — one notification per
    # new commit, not one per hour it sits unaddressed.
    last_notified_path = Path("logs/last_notified_commit.txt")

    def _already_notified(commit: str) -> bool:
        try:
            return last_notified_path.exists() and last_notified_path.read_text().strip() == commit
        except Exception:
            return False

    def _mark_notified(commit: str):
        try:
            last_notified_path.parent.mkdir(exist_ok=True)
            last_notified_path.write_text(commit)
        except Exception as e:
            log.warning(f"[UPDATE] Could not save notified-commit marker: {e}")

    while not stop_event.wait(timeout=interval):
        try:
            result = check_for_update(cfg)
            if not result["update_available"]:
                if result["reason"] and result["reason"] != "Already up to date":
                    log.debug(f"[UPDATE] {result['reason']}")
                continue

            remote_commit = result["remote_commit"]
            log.info(f"[UPDATE] Update available: {result['local_commit'][:8]} "
                     f"-> {remote_commit[:8]}")

            # ── notify_only (default): never pulls on its own, ever ───────
            # Tells the person an update exists and exactly how to apply it
            # whenever suits them — via /update in Telegram, or `git pull`
            # by hand. Nothing on disk changes and nothing restarts unless
            # they take that action themselves.
            if mode == "notify_only":
                if _already_notified(remote_commit):
                    continue   # already told them about this exact commit — don't repeat
                if tg_send_fn:
                    tg_send_fn(
                        f"🆕 <b>Update Available</b>\n━━━━━━━━━━━━━━━━\n"
                        f"A new version is available on "
                        f"{getattr(cfg,'AUTO_UPDATE_REMOTE','origin')}/"
                        f"{getattr(cfg,'AUTO_UPDATE_BRANCH','main')}.\n\n"
                        f"Current: <code>{result['local_commit'][:8]}</code>\n"
                        f"New:     <code>{remote_commit[:8]}</code>\n\n"
                        f"Nothing has changed yet — your bot keeps running "
                        f"exactly as it is. When it's convenient, send "
                        f"<b>/update</b> to apply it, or run <code>git pull</code> "
                        f"yourself on the machine. You'll only see this "
                        f"notice once for this version."
                    )
                _mark_notified(remote_commit)
                log.info(f"[UPDATE] notify_only — told the user, taking no further action "
                        f"until they send /update")
                continue

            # ── require_approval: same Y/N gate as strategy reviews ────────
            if mode == "require_approval":
                import approval_gate
                outcome = approval_gate.request_approval(
                    change_type   = "auto_update",
                    title         = "Auto-Update Available",
                    what_learned  = f"A new commit is available on "
                                    f"{getattr(cfg,'AUTO_UPDATE_REMOTE','origin')}/"
                                    f"{getattr(cfg,'AUTO_UPDATE_BRANCH','main')}.",
                    why_change    = "Pulling updates the bot's code to the latest "
                                    "pushed version.",
                    proposed      = {"commit": remote_commit[:8]},
                    current       = {"commit": result["local_commit"][:8]},
                    confidence    = 100,
                    config        = cfg,
                )
                if outcome != "approved":
                    log.info(f"[UPDATE] Update not applied (outcome: {outcome})")
                    continue

            # ── auto_apply (and require_approval after a Y) — pulls now ────
            applied = perform_update(cfg, tg_send_fn=tg_send_fn)
            if applied:
                log.info("[UPDATE] Exiting process now so the watchdog relaunches on new code")
                os._exit(0)   # see docstring — must be os._exit, not sys.exit/return,
                               # to actually terminate the process from this thread

        except Exception as e:
            log.error(f"[UPDATE] Update check failed: {e}")

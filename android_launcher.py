"""
android_launcher.py — entry point called by BotService.kt via Chaquopy.

Flow on every app launch:
  1. Inject no-op bootstrap (skip runtime pip-install calls).
  2. Copy bundled .py files from Chaquopy's read-only dir to filesDir/bot/.
  3. Auto-update: hit GitHub API → if the android branch has a newer commit,
     download the zip and hot-swap all .py files (bot_secrets.py preserved).
  4. Set HAZMAT_ANDROID env var so bot code adapts to mobile.
  5. Start Flask dashboard on 127.0.0.1:8501 (blocks until process exits).
"""

import os
import sys
import shutil
import types
import threading
import importlib.util

REPO   = "HazMat77/crypto-bot"
BRANCH = "android"
SHA_FILE = ".android_update_sha"


def start(files_dir: str) -> None:
    bot_dir = os.path.join(files_dir, "bot")

    _mock_bootstrap()
    _sync_bundled_files(bot_dir)

    os.chdir(bot_dir)
    if bot_dir not in sys.path:
        sys.path.insert(0, bot_dir)

    os.environ["HAZMAT_ANDROID"] = "1"
    os.environ["HAZMAT_BOT_DIR"] = bot_dir
    os.makedirs(os.path.join(bot_dir, "logs"), exist_ok=True)

    # Auto-update in background so the dashboard starts immediately
    threading.Thread(target=_auto_update, args=(bot_dir,), daemon=True,
                     name="auto-updater").start()

    from android_dashboard import run as run_dashboard
    run_dashboard(host="127.0.0.1", port=8501)


# ── Auto-update ───────────────────────────────────────────────────────────────

def _auto_update(bot_dir: str) -> None:
    """
    Check GitHub for a newer commit on the android branch.
    If found: download the zip, extract .py files, store the new SHA.
    bot_secrets.py is never touched.
    """
    try:
        import requests, zipfile, io, json as _json

        api_url  = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
        resp     = requests.get(api_url, timeout=15)
        resp.raise_for_status()
        latest   = resp.json()["sha"]

        sha_path = os.path.join(bot_dir, SHA_FILE)
        current  = open(sha_path).read().strip() if os.path.exists(sha_path) else ""

        if latest == current:
            print(f"[AutoUpdate] Already up to date ({latest[:7]})")
            return

        print(f"[AutoUpdate] Downloading update {current[:7] or 'initial'} → {latest[:7]} …")
        zip_url  = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
        data     = requests.get(zip_url, timeout=120)
        data.raise_for_status()

        updated  = 0
        prefix   = f"crypto-bot-{BRANCH}/"   # zip root folder name
        with zipfile.ZipFile(io.BytesIO(data.content)) as zf:
            for name in zf.namelist():
                if not name.startswith(prefix):
                    continue
                rel  = name[len(prefix):]
                base = os.path.basename(rel)
                if not base.endswith(".py") or base == "bot_secrets.py":
                    continue
                dst = os.path.join(bot_dir, base)
                with zf.open(name) as src_f:
                    open(dst, "wb").write(src_f.read())
                updated += 1

        open(sha_path, "w").write(latest)
        print(f"[AutoUpdate] ✅ Updated {updated} files (restart app to apply)")

        # Signal dashboard that an update is ready
        flag = os.path.join(bot_dir, "logs", ".update_ready")
        open(flag, "w").write(latest)

    except Exception as exc:
        print(f"[AutoUpdate] ⚠ {exc}")


def trigger_update(bot_dir: str) -> str:
    """Called by /api/update endpoint — runs update synchronously, returns status."""
    try:
        import requests, zipfile, io

        api_url = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
        latest  = requests.get(api_url, timeout=15).json()["sha"]

        sha_path = os.path.join(bot_dir, SHA_FILE)
        current  = open(sha_path).read().strip() if os.path.exists(sha_path) else ""
        if latest == current:
            return f"Already up to date ({latest[:7]})"

        zip_url = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
        data    = requests.get(zip_url, timeout=120)
        data.raise_for_status()

        updated = 0
        prefix  = f"crypto-bot-{BRANCH}/"
        with zipfile.ZipFile(io.BytesIO(data.content)) as zf:
            for name in zf.namelist():
                if not name.startswith(prefix):
                    continue
                base = os.path.basename(name)
                if not base.endswith(".py") or base == "bot_secrets.py":
                    continue
                with zf.open(name) as src_f:
                    open(os.path.join(bot_dir, base), "wb").write(src_f.read())
                updated += 1

        open(sha_path, "w").write(latest)
        flag = os.path.join(bot_dir, "logs", ".update_ready")
        open(flag, "w").write(latest)
        return f"Updated {updated} files to {latest[:7]} — restart app to apply"

    except Exception as exc:
        return f"Update failed: {exc}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_bootstrap() -> None:
    mock = types.ModuleType("bootstrap")
    mock.ensure_installed = lambda **kwargs: None
    sys.modules["bootstrap"] = mock


def _sync_bundled_files(bot_dir: str) -> None:
    """Copy APK-bundled .py files to writable storage (first-run / fallback)."""
    spec = importlib.util.find_spec("android_dashboard")
    if spec is None or spec.origin is None:
        raise RuntimeError("android_dashboard.py missing from APK — rebuild.")

    src_dir = os.path.dirname(spec.origin)
    os.makedirs(bot_dir, exist_ok=True)

    for fname in os.listdir(src_dir):
        if not fname.endswith(".py"):
            continue
        dst = os.path.join(bot_dir, fname)
        # Never overwrite real secrets; skip if already updated from GitHub
        if fname == "bot_secrets.py" and os.path.exists(dst):
            continue
        if not os.path.exists(dst):          # only copy if not already there
            shutil.copy2(os.path.join(src_dir, fname), dst)

    st_src = os.path.join(src_dir, ".streamlit")
    st_dst = os.path.join(bot_dir, ".streamlit")
    if os.path.isdir(st_src) and not os.path.exists(st_dst):
        shutil.copytree(st_src, st_dst)

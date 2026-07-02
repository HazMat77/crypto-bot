"""
Dependency Bootstrap
======================
Runtime safety net so third-party packages install automatically the
moment any entry point (bot.py, gui_dashboard.py, backtest.py,
watchdog.py, dashboard.py) is run directly with `python <file>.py` —
not just when INSTALL.bat / INSTALL_LINUX.sh was run first.

Import names differ from their pip package names for a few packages
(python-kucoin -> kucoin, beautifulsoup4 -> bs4), so those are mapped
explicitly rather than guessed.

Usage — call this before importing anything that isn't in the stdlib:
    import bootstrap
    bootstrap.ensure_installed()               # core packages only
    bootstrap.ensure_installed(optional=True)  # + ccxt/matplotlib/streamlit/plotly
    bootstrap.ensure_installed(gui=True)       # + customtkinter
"""

import importlib
import subprocess
import sys

_PIP_NAME = {
    "kucoin":    "python-kucoin",
    "bs4":       "beautifulsoup4",
    "websocket": "websocket-client",
}

_CORE     = ["kucoin", "pandas", "numpy", "requests", "bs4", "websocket"]
_OPTIONAL = ["ccxt", "matplotlib", "streamlit", "plotly"]
_GUI      = ["customtkinter"]

_checked = set()  # avoid re-checking the same set within one process


def _missing(names):
    missing = []
    for name in names:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(_PIP_NAME.get(name, name))
    return missing


def ensure_installed(optional: bool = False, gui: bool = False) -> None:
    """Installs any missing packages via pip. Never raises — a failed
    auto-install just leaves the original ImportError to surface
    naturally wherever the missing package is actually used.

    Deliberately NOT run with --quiet or captured output: a silent pip
    install that takes a minute or two (slow network, or the first big
    package like pandas/numpy) looks completely indistinguishable from
    a frozen/crashed process — the whole point of printing here is so
    whatever console launched this (a .bat/.sh window, a terminal) shows
    real progress instead of going blank until it's done."""
    names = tuple(_CORE + (_OPTIONAL if optional else []) + (_GUI if gui else []))
    if names in _checked:
        return

    missing = _missing(names)
    if not missing:
        _checked.add(names)
        return

    pkgs = list(missing)
    if "python-kucoin" in pkgs:
        pkgs[pkgs.index("python-kucoin")] = "python-kucoin==2.1.3"

    print(f"[bootstrap] Installing missing dependencies: {', '.join(pkgs)}")
    print("[bootstrap] This can take a minute or two on first run — please wait, "
         "do not close this window.")

    cmd = [sys.executable, "-m", "pip", "install", *pkgs]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        # Externally-managed environment (PEP 668, common on newer
        # Debian/Ubuntu) — retry allowing a system-wide install.
        print("[bootstrap] Initial install failed — retrying with --break-system-packages...")
        subprocess.run(cmd + ["--break-system-packages"])

    still_missing = _missing(names)
    if still_missing:
        print(f"[bootstrap] Could not auto-install: {', '.join(still_missing)}. "
             f"Install manually: {sys.executable} -m pip install {' '.join(still_missing)}")
    else:
        print("[bootstrap] Done.")
    _checked.add(names)

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
    "kucoin": "python-kucoin",
    "bs4":    "beautifulsoup4",
}

_CORE     = ["kucoin", "pandas", "numpy", "requests", "bs4"]
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
    """Installs any missing packages via pip, quietly. Never raises —
    a failed auto-install just leaves the original ImportError to
    surface naturally wherever the missing package is actually used."""
    names = tuple(_CORE + (_OPTIONAL if optional else []) + (_GUI if gui else []))
    if names in _checked:
        return

    missing = _missing(names)
    if not missing:
        _checked.add(names)
        return

    print(f"[bootstrap] Installing missing dependencies: {', '.join(missing)}")
    pkgs = list(missing)
    if "python-kucoin" in pkgs:
        pkgs[pkgs.index("python-kucoin")] = "python-kucoin==2.1.3"

    cmd = [sys.executable, "-m", "pip", "install", "--quiet", *pkgs]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Externally-managed environment (PEP 668, common on newer
        # Debian/Ubuntu) — retry allowing a system-wide install.
        subprocess.run(cmd + ["--break-system-packages"],
                       capture_output=True, text=True)

    still_missing = _missing(names)
    if still_missing:
        print(f"[bootstrap] Could not auto-install: {', '.join(still_missing)}. "
             f"Install manually: {sys.executable} -m pip install {' '.join(still_missing)}")
    _checked.add(names)

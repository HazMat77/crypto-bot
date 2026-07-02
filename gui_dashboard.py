"""
HazMat Crypto Bot — Modern GUI Dashboard
==========================================
Dark-themed desktop dashboard styled after software-suite control panels
(top tab bar, rounded stat cards) rather than a typical sidebar app.
Built on CustomTkinter (a rounded-corner skin over stdlib tkinter).

Run: python gui_dashboard.py
Or:  Double-click START_BOT.bat → [3] GUI Dashboard
"""

import os, sys, glob, threading, importlib, re, json, subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, date
from pathlib import Path

import bootstrap
bootstrap.ensure_installed(gui=True)

import customtkinter as ctk

import settings_writer
from settings_writer import EXCHANGE_DISPLAY, EXCHANGE_FIELDS

# ── Palette ────────────────────────────────────────────────────────────────
# AMD Adrenaline-style palette — near-black background, AMD red as the
# primary accent (active tab, primary buttons, highlights). This exact
# palette is mirrored in dashboard.py's .streamlit theme + CSS so the
# desktop GUI and the web/Android GUI look like the same application.
C = {
    "bg":       "#0a0a0a",
    "surface":  "#171717",
    "border":   "#2b2b2b",
    "hover":    "#271616",
    "text":     "#f2f2f2",
    "muted":    "#9a9a9a",
    "green":    "#3fb950",
    "red":      "#e8262b",
    "accent":   "#ed1c24",
    "yellow":   "#d29922",
    "purple":   "#bc8cff",
    "orange":   "#ffa657",
    "teal":     "#39d353",
    "white":    "#ffffff",
}

# Fire gradient (deep red -> bright yellow-orange) for the "HazMat" wordmark.
FIRE_COLORS = ["#7f0000", "#b3001b", "#e8451e", "#ff6f00", "#ff9e00", "#ffc300"]

def _font(win_name, other_name, size, weight=None):
    """
    Cross-platform font picker. Windows gets win_name (e.g. Segoe UI).
    macOS gets other_name as given (e.g. SF Pro). Linux gets a sensible
    default instead — SF Pro/Segoe UI don't exist there and Tk would
    just silently fall back anyway, so we pick a font that actually
    ships on most Linux desktops.
    """
    if sys.platform == "win32":
        name = win_name
    elif sys.platform == "darwin":
        name = other_name
    else:
        name = "DejaVu Sans" if other_name != "Consolas" else "DejaVu Sans Mono"
    return (name, size, weight) if weight else (name, size)


FONT_MONO  = _font("Cascadia Code", "Consolas", 9)
FONT_UI    = _font("Segoe UI", "SF Pro", 10)
FONT_TITLE = _font("Segoe UI", "SF Pro", 13, "bold")
FONT_NUM   = _font("Segoe UI", "SF Pro", 20, "bold")


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0,2,4))


def _lighten(h, amount=30):
    r, g, b = hex_to_rgb(h)
    r = min(255, r+amount); g = min(255, g+amount); b = min(255, b+amount)
    return f"#{r:02x}{g:02x}{b:02x}"


def _pack_fire_text(parent, text, bg, font, colors=FIRE_COLORS, pady=14):
    """Packs `text` one character at a time, each colored from a fire
    gradient, into `parent` (side='left'). Tkinter labels can't render a
    smooth gradient fill, so this is the practical per-letter stand-in —
    used for the "HazMat" wordmark so it reads as flame-colored."""
    for i, ch in enumerate(text):
        tk.Label(parent, text=ch, bg=bg, fg=colors[i % len(colors)],
                font=font).pack(side="left", pady=pady)


class Card(ctk.CTkFrame):
    """Rounded, bordered panel — the base 'tile' unit of the whole UI,
    styled after control-panel software (AMD Adrenaline, NVIDIA app)
    rather than a flat sidebar-app card."""
    def __init__(self, parent, title="", **kw):
        super().__init__(parent, fg_color=C["surface"], border_color=C["border"],
                        border_width=1, corner_radius=14, **kw)
        if title:
            tk.Label(self, text=title, bg=C["surface"], fg=C["muted"],
                    font=("Segoe UI", 9, "bold") if sys.platform=="win32" else ("SF Pro",9,"bold"),
                    anchor="w").pack(fill="x", padx=14, pady=(12,4))
            ctk.CTkFrame(self, height=1, fg_color=C["border"], corner_radius=0
                        ).pack(fill="x", padx=14)


class MetricTile(ctk.CTkFrame):
    """Stat card with a colored accent bar down the left edge — the
    'gauge tile' look of a hardware control panel."""
    def __init__(self, parent, label, accent=None, **kw):
        super().__init__(parent, fg_color=C["surface"], border_color=C["border"],
                        border_width=1, corner_radius=14, **kw)
        self._accent = ctk.CTkFrame(self, width=4, fg_color=accent or C["border"],
                                    corner_radius=0)
        self._accent.pack(side="left", fill="y")
        body = tk.Frame(self, bg=C["surface"])
        body.pack(side="left", fill="both", expand=True)

        tk.Label(body, text=label, bg=C["surface"], fg=C["muted"],
                font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9)
                ).pack(anchor="w", padx=12, pady=(10,2))
        self.value_lbl = tk.Label(body, text="—", bg=C["surface"],
                                  fg=C["text"], font=FONT_NUM)
        self.value_lbl.pack(anchor="w", padx=12)
        self.sub_lbl   = tk.Label(body, text="", bg=C["surface"],
                                  fg=C["muted"],
                                  font=("Segoe UI",8) if sys.platform=="win32" else ("SF Pro",8))
        self.sub_lbl.pack(anchor="w", padx=12, pady=(0,10))

    def set(self, value, sub="", color=None):
        self.value_lbl.configure(text=str(value), fg=color or C["text"])
        self.sub_lbl.configure(text=sub)
        self._accent.configure(fg_color=color or C["border"])


class PillButton(ctk.CTkButton):
    def __init__(self, parent, text, command=None, color=C["accent"], **kw):
        super().__init__(parent, text=text, command=command,
                        fg_color=color, hover_color=_lighten(color),
                        text_color=C["white"], corner_radius=8, height=30,
                        font=("Segoe UI",9,"bold") if sys.platform=="win32" else ("SF Pro",9,"bold"),
                        **kw)
        self._base_color = color


class Dashboard:

    def __init__(self, root):
        self.root = root
        root.title("HazMat Crypto Bot")
        root.geometry("1280x820")
        root.minsize(960, 640)
        self._bot_proc      = None
        self._watchdog_proc = None
        self._build()
        self._start_auto_refresh()

    # ══════════════════════════════════════════════════════════════════════
    #  LAYOUT
    # ══════════════════════════════════════════════════════════════════════

    def _build(self):
        self._build_topbar()
        self._build_tabbar()
        self._build_main()

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=C["surface"], height=56,
                      highlightbackground=C["border"], highlightthickness=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # Logo + title — "HazMat" rendered in a fire gradient, plain white
        # for the rest of the name.
        tk.Label(bar, text="🔥", bg=C["surface"], fg=C["orange"],
                font=("Segoe UI",20) if sys.platform=="win32" else ("SF Pro",20)
                ).pack(side="left", padx=(18,4), pady=14)
        _pack_fire_text(bar, "HazMat", C["surface"], FONT_TITLE)
        tk.Label(bar, text=" Crypto Bot", bg=C["surface"],
                fg=C["white"], font=FONT_TITLE).pack(side="left", pady=14)

        # Status badge
        self.status_badge = tk.Label(bar, text="● LOADING",
                                     bg=C["surface"], fg=C["yellow"],
                                     font=("Segoe UI",9,"bold") if sys.platform=="win32" else ("SF Pro",9,"bold"))
        self.status_badge.pack(side="left", padx=18)

        self.clock_lbl = tk.Label(bar, text="", bg=C["surface"],
                                  fg=C["muted"],
                                  font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9))
        self.clock_lbl.pack(side="left")

        PillButton(bar, "⬆  Updates", self._check_updates, C["purple"]).pack(side="left", padx=(14,0))

        # Right controls
        ctrl = tk.Frame(bar, bg=C["surface"])
        ctrl.pack(side="right", padx=14)

        PillButton(ctrl, "🚀  Start",  self._start_bot, C["teal"]).pack(side="left", padx=3)
        PillButton(ctrl, "⏸  Pause",  self._pause,  C["yellow"]).pack(side="left", padx=3)
        PillButton(ctrl, "▶  Resume", self._resume, C["green"]).pack(side="left",  padx=3)
        PillButton(ctrl, "⟳  Refresh",self._refresh,C["accent"]).pack(side="left",  padx=3)

    def _build_tabbar(self):
        """Horizontal top tab strip (control-panel style) with an accent
        underline on the active tab — replaces the old left sidebar nav."""
        bar = tk.Frame(self.root, bg=C["surface"], height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkFrame(self.root, height=1, fg_color=C["border"], corner_radius=0).pack(fill="x")

        self._nav_tabs = {}
        pages = [
            ("📊", "Overview"),
            ("💰", "Pools"),
            ("📋", "Trades"),
            ("🧾", "Reports"),
            ("📰", "News"),
            ("⚙️", "Config"),
            ("📁", "Logs"),
        ]

        # Fixed (not expand/fill) column widths — CTkButton's canvas-based
        # rendering doesn't negotiate pack()'s expand+fill evenly across
        # many siblings, so a fixed width per tab is what actually renders
        # reliably at every window size.
        for icon, name in pages:
            col = tk.Frame(bar, bg=C["surface"], width=150, height=40)
            col.pack(side="left", padx=2, pady=(6,0))
            col.pack_propagate(False)
            btn = ctk.CTkButton(col, text=f"{icon}  {name}", command=lambda n=name: self._nav(n),
                               fg_color="transparent", hover_color=C["hover"],
                               text_color=C["muted"], corner_radius=8, height=32, width=146,
                               font=("Segoe UI",10) if sys.platform=="win32" else ("SF Pro",10))
            btn.pack()
            underline = ctk.CTkFrame(col, height=3, fg_color="transparent", corner_radius=0)
            underline.pack(fill="x", pady=(4,0))
            self._nav_tabs[name] = (btn, underline)

    def _build_main(self):
        self.main = tk.Frame(self.root, bg=C["bg"])
        self.main.pack(side="left", fill="both", expand=True)

        self._current_page = "Overview"
        self._page_frames  = {}

        for name in ("Overview","Pools","Trades","Reports","News","Config","Logs"):
            f = tk.Frame(self.main, bg=C["bg"])
            self._page_frames[name] = f
            getattr(self, f"_build_{name.lower()}")()

        self._nav("Overview")

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE BUILDERS
    # ══════════════════════════════════════════════════════════════════════

    def _build_overview(self):
        p = self._page_frames["Overview"]

        # Metrics row
        metrics = tk.Frame(p, bg=C["bg"])
        metrics.pack(fill="x", padx=16, pady=(16,8))

        self.t_pool      = MetricTile(metrics, "TOTAL POOL")
        self.t_normal    = MetricTile(metrics, "NORMAL 80%")
        self.t_aggr      = MetricTile(metrics, "AGGRESSIVE 20%")
        self.t_trades    = MetricTile(metrics, "TRADES TODAY")
        self.t_pnl       = MetricTile(metrics, "NET P&L TODAY")
        self.t_gain      = MetricTile(metrics, "% GAIN TODAY")
        self.t_wr        = MetricTile(metrics, "WIN RATE TODAY")
        for t in (self.t_pool, self.t_normal, self.t_aggr,
                  self.t_trades, self.t_pnl, self.t_gain, self.t_wr):
            t.pack(side="left", fill="both", expand=True, padx=4)

        # Lower section
        lower = tk.Frame(p, bg=C["bg"])
        lower.pack(fill="both", expand=True, padx=16, pady=8)

        # Positions card
        pos_card = Card(lower, title="OPEN POSITIONS")
        pos_card.pack(side="left", fill="both", expand=True, padx=(0,4))

        cols = ("Coin","Exchange","Pool","Entry","P&L%")
        self.pos_tree = ttk.Treeview(pos_card, columns=cols,
                                     show="headings", height=8)
        style = ttk.Style()
        style.configure("Pos.Treeview", background=C["surface"],
                       foreground=C["text"], fieldbackground=C["surface"],
                       rowheight=28, font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9))
        style.configure("Pos.Treeview.Heading", background=C["border"],
                       foreground=C["muted"],
                       font=("Segoe UI",8,"bold") if sys.platform=="win32" else ("SF Pro",8,"bold"))
        self.pos_tree.configure(style="Pos.Treeview")
        for c, w in zip(cols, [70,90,100,100,80]):
            self.pos_tree.heading(c, text=c); self.pos_tree.column(c, width=w, anchor="center")
        self.pos_tree.tag_configure("normal", foreground=C["green"])
        self.pos_tree.tag_configure("aggressive", foreground=C["orange"])
        self.pos_tree.pack(fill="both", expand=True, padx=12, pady=8)

        # Signals card
        sig_card = Card(lower, title="RECENT SIGNALS")
        sig_card.pack(side="left", fill="both", expand=True, padx=(4,0))

        self.sig_text = ctk.CTkTextbox(sig_card, fg_color=C["surface"], text_color=C["text"],
                                      font=FONT_MONO, wrap="word", corner_radius=0,
                                      border_width=0, height=220, state="disabled")
        self.sig_text.tag_config("buy",     foreground=C["green"])
        self.sig_text.tag_config("sell",    foreground=C["red"])
        self.sig_text.tag_config("warn",    foreground=C["yellow"])
        self.sig_text.tag_config("engine",  foreground=C["purple"])
        self.sig_text.tag_config("normal",  foreground=C["muted"])
        self.sig_text.pack(fill="both", expand=True, padx=12, pady=8)

    def _build_pools(self):
        p = self._page_frames["Pools"]

        # Pool bar
        bar_card = Card(p, title="POOL ALLOCATION")
        bar_card.pack(fill="x", padx=16, pady=(16,8))
        self.pool_bar = tk.Canvas(bar_card, height=48, bg=C["surface"],
                                  highlightthickness=0)
        self.pool_bar.pack(fill="x", padx=12, pady=8)

        # Details
        detail_row = tk.Frame(p, bg=C["bg"])
        detail_row.pack(fill="both", expand=True, padx=16, pady=8)

        # Normal card
        nc = Card(detail_row, title="🟢  NORMAL POOL  —  80%  —  Conservative")
        nc.pack(side="left", fill="both", expand=True, padx=(0,6))
        self.normal_table = self._make_kv_table(nc)

        # Aggressive card
        ac = Card(detail_row, title="🔴  AGGRESSIVE POOL  —  20%  —  Higher Risk/Reward")
        ac.pack(side="left", fill="both", expand=True, padx=(6,0))
        self.aggr_table = self._make_kv_table(ac)

        # Engine status card
        ec = Card(p, title="⚡  ADAPTIVE STRATEGY ENGINE")
        ec.pack(fill="x", padx=16, pady=8)
        self.engine_table = self._make_kv_table(ec)

    def _build_trades(self):
        p = self._page_frames["Trades"]

        hdr = tk.Frame(p, bg=C["bg"])
        hdr.pack(fill="x", padx=16, pady=(16,8))
        self.trade_summary_lbl = tk.Label(hdr, text="No trades yet",
                                          bg=C["bg"], fg=C["muted"],
                                          font=("Segoe UI",10) if sys.platform=="win32" else ("SF Pro",10))
        self.trade_summary_lbl.pack(side="left")

        card = Card(p, title="COMPLETED TRADES  (today)")
        card.pack(fill="both", expand=True, padx=16, pady=8)

        cols = ("Time","Coin","Exch","Pool","Buy","Sell","Net","Exit")
        self.trade_tree = ttk.Treeview(card, columns=cols,
                                       show="headings", style="Pos.Treeview")
        widths = [65,60,70,90,90,90,80,100]
        for c, w in zip(cols, widths):
            self.trade_tree.heading(c, text=c)
            self.trade_tree.column(c, width=w, anchor="center")
        self.trade_tree.tag_configure("win",  background="#0d2818")
        self.trade_tree.tag_configure("loss", background="#2c0f0f")
        vsb = ttk.Scrollbar(card, command=self.trade_tree.yview)
        self.trade_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.trade_tree.pack(fill="both", expand=True, padx=12, pady=8)

    def _build_reports(self):
        p = self._page_frames["Reports"]
        self._report_period = "daily"

        hdr = tk.Frame(p, bg=C["bg"])
        hdr.pack(fill="x", padx=16, pady=(16,8))
        tk.Label(hdr, text="Trading Reports", bg=C["bg"], fg=C["text"],
                font=FONT_TITLE).pack(side="left")

        self._report_btns = {}
        btn_row = tk.Frame(hdr, bg=C["bg"])
        btn_row.pack(side="right")
        for period, label in (("daily","Daily"),("monthly","Monthly"),("yearly","Yearly")):
            b = PillButton(btn_row, label, lambda pr=period: self._select_report_period(pr),
                          C["accent"] if period == "daily" else C["border"])
            b.pack(side="left", padx=3)
            self._report_btns[period] = b

        self.report_label = tk.Label(p, text="", bg=C["bg"], fg=C["muted"],
                                     font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9))
        self.report_label.pack(anchor="w", padx=16)

        metrics = tk.Frame(p, bg=C["bg"])
        metrics.pack(fill="x", padx=16, pady=8)
        self.r_trades = MetricTile(metrics, "TRADES")
        self.r_wr     = MetricTile(metrics, "WIN RATE")
        self.r_gross  = MetricTile(metrics, "GROSS P&L")
        self.r_fees   = MetricTile(metrics, "FEES")
        self.r_net    = MetricTile(metrics, "NET P&L")
        self.r_roi    = MetricTile(metrics, "ROI")
        for t in (self.r_trades, self.r_wr, self.r_gross, self.r_fees, self.r_net, self.r_roi):
            t.pack(side="left", fill="both", expand=True, padx=4)

        lower = tk.Frame(p, bg=C["bg"])
        lower.pack(fill="both", expand=True, padx=16, pady=8)

        coin_card = Card(lower, title="BY COIN")
        coin_card.pack(side="left", fill="both", expand=True, padx=(0,4))
        cols = ("Coin","Trades","Gross","Fees","Net")
        self.report_tree = ttk.Treeview(coin_card, columns=cols,
                                        show="headings", style="Pos.Treeview")
        for c, w in zip(cols, [90,70,90,90,90]):
            self.report_tree.heading(c, text=c); self.report_tree.column(c, width=w, anchor="center")
        self.report_tree.tag_configure("win",  foreground=C["green"])
        self.report_tree.tag_configure("loss", foreground=C["red"])
        self.report_tree.pack(fill="both", expand=True, padx=12, pady=8)

        best_card = Card(lower, title="BEST / WORST TRADE")
        best_card.pack(side="left", fill="both", expand=True, padx=(4,0))
        self.report_best_table = self._make_kv_table(best_card)

    def _select_report_period(self, period):
        self._report_period = period
        for pr, b in self._report_btns.items():
            color = C["accent"] if pr == period else C["border"]
            b.configure(fg_color=color, hover_color=_lighten(color))
            b._base_color = color
        self._load_report()

    def _load_report(self):
        def _fetch():
            try:
                from reports import build_report
                import config as cfg
                importlib.reload(cfg)
                starting = getattr(cfg, "PAPER_STARTING_USDT", 100.0)
                report = build_report(self._report_period, starting_pool=starting)
                self.root.after(0, lambda: self._render_report(report))
            except Exception as e:
                self.root.after(0, lambda: self.report_label.configure(
                    text=f"Could not load report: {e}", fg=C["red"]))
        threading.Thread(target=_fetch, daemon=True).start()

    def _render_report(self, report):
        self.report_label.configure(
            text=f"{report['label']}  ·  {report['start']} → {report['end']}", fg=C["muted"])

        gross_col = C["green"] if report["gross_pnl"] >= 0 else C["red"]
        net_col   = C["green"] if report["net_pnl"]   >= 0 else C["red"]
        roi_col   = C["green"] if report["roi_pct"]   >= 0 else C["red"]
        wr_col    = C["green"] if report["wins"] >= report["losses"] else C["red"]

        self.r_trades.set(str(report["num_trades"]))
        self.r_wr.set(f"{report['win_rate']:.0f}%", f"{report['wins']}W  {report['losses']}L", wr_col)
        self.r_gross.set(f"{'+' if report['gross_pnl']>=0 else ''}${report['gross_pnl']:.4f}", "", gross_col)
        self.r_fees.set(f"-${report['fees']:.4f}")
        self.r_net.set(f"{'+' if report['net_pnl']>=0 else ''}${report['net_pnl']:.4f}", "net after fees", net_col)
        self.r_roi.set(f"{'+' if report['roi_pct']>=0 else ''}{report['roi_pct']:.2f}%", "", roi_col)

        self.report_tree.delete(*self.report_tree.get_children())
        for coin, s in report["by_coin"].items():
            tag = "win" if s["net"] >= 0 else "loss"
            self.report_tree.insert("", "end", values=(
                coin, s["n"], f"{'+' if s['gross']>=0 else ''}${s['gross']:.4f}",
                f"-${s['fees']:.4f}", f"{'+' if s['net']>=0 else ''}${s['net']:.4f}"), tags=(tag,))

        best, worst = report["best_trade"], report["worst_trade"]
        rows = []
        if best:
            rows.append(("Best trade", f"{best['coin']}  net +${best.get('pnl_net',0):.4f}"))
        if worst:
            rows.append(("Worst trade", f"{worst['coin']}  net ${worst.get('pnl_net',0):.4f}"))
        if not rows:
            rows.append(("—", "No trades in this period"))
        self._fill_kv(self.report_best_table, rows)

    def _build_news(self):
        p = self._page_frames["News"]

        hdr = tk.Frame(p, bg=C["bg"])
        hdr.pack(fill="x", padx=16, pady=(16,8))
        tk.Label(hdr, text="News Sentiment Scores",
                bg=C["bg"], fg=C["text"], font=FONT_TITLE).pack(side="left")
        PillButton(hdr, "⟳  Refresh", self._load_news, C["accent"]).pack(side="right")
        tk.Label(hdr, text="  Combined: The Block · CoinDesk · Blockworks · Cointelegraph · Bloomberg · Forbes · Messari · CoinGecko · CMC",
                bg=C["bg"], fg=C["muted"],
                font=("Segoe UI",8) if sys.platform=="win32" else ("SF Pro",8)
                ).pack(side="left", padx=8)

        card = Card(p)
        card.pack(fill="both", expand=True, padx=16, pady=8)
        self.news_canvas = tk.Canvas(card, bg=C["surface"], highlightthickness=0)
        self.news_canvas.pack(fill="both", expand=True, padx=12, pady=12)

    def _build_config(self):
        p = self._page_frames["Config"]

        hdr = tk.Frame(p, bg=C["bg"])
        hdr.pack(fill="x", padx=16, pady=(16,8))
        tk.Label(hdr, text="Configuration", bg=C["bg"], fg=C["text"],
                font=FONT_TITLE).pack(side="left")

        # Presets
        preset_row = tk.Frame(p, bg=C["bg"])
        preset_row.pack(fill="x", padx=16, pady=4)
        tk.Label(preset_row, text="Quick Presets:", bg=C["bg"],
                fg=C["muted"],
                font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9)
                ).pack(side="left", padx=(0,8))
        PillButton(preset_row, "Conservative", self._preset_conservative, C["accent"]).pack(side="left",  padx=3)
        PillButton(preset_row, "Balanced",     self._preset_balanced,     C["purple"]).pack(side="left",padx=3)
        PillButton(preset_row, "Aggressive",   self._preset_aggressive,   C["orange"]).pack(side="left",padx=3)
        tk.Label(preset_row,
                text="  ← Updates config.py. Restart bot to apply.",
                bg=C["bg"], fg=C["muted"],
                font=("Segoe UI",8) if sys.platform=="win32" else ("SF Pro",8)
                ).pack(side="left")

        # ── Exchange API keys — lets someone who's never opened a .py file
        # enter credentials and pick an exchange straight from the GUI.
        ek_card = Card(p, title="EXCHANGE API KEYS")
        ek_card.pack(fill="x", padx=16, pady=8)

        sel_row = tk.Frame(ek_card, bg=C["surface"])
        sel_row.pack(fill="x", padx=12, pady=(4,4))
        tk.Label(sel_row, text="Exchange:", bg=C["surface"], fg=C["muted"],
                font=FONT_UI).pack(side="left", padx=(0,8))

        self._exch_display_to_key = {v: k for k, v in EXCHANGE_DISPLAY.items()}
        self.exch_key_var = tk.StringVar(value="kucoin")
        self.exch_combobox = ctk.CTkComboBox(
            sel_row, values=list(EXCHANGE_DISPLAY.values()),
            command=self._on_exchange_selected, width=160, height=30, corner_radius=8,
            fg_color=C["bg"], border_color=C["border"], button_color=C["border"],
            button_hover_color=C["hover"], dropdown_fg_color=C["surface"], font=FONT_UI)
        self.exch_combobox.set(EXCHANGE_DISPLAY["kucoin"])
        self.exch_combobox.pack(side="left", padx=(0,16))

        self.exch_enabled_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(sel_row, text="Enabled for trading", variable=self.exch_enabled_var,
                       fg_color=C["accent"], hover_color=_lighten(C["accent"]),
                       text_color=C["text"], font=FONT_UI).pack(side="left", padx=(0,16))

        PillButton(sel_row, "💾  Save Keys", self._save_exchange_credentials, C["green"]).pack(side="left")

        self.exch_fields_frame = tk.Frame(ek_card, bg=C["surface"])
        self.exch_fields_frame.pack(fill="x", padx=12, pady=(4,4))
        self._exch_entries = {}
        self._build_exchange_fields("kucoin")

        tk.Label(ek_card,
                text="Saved keys go to bot_secrets.py (gitignored — never committed to GitHub) "
                    "and the selected exchange is enabled/disabled in config.py. Restart the bot to apply.",
                bg=C["surface"], fg=C["muted"], wraplength=1150, justify="left",
                font=("Segoe UI",8) if sys.platform=="win32" else ("SF Pro",8)
                ).pack(anchor="w", padx=12, pady=(0,10))

        # ── Bot settings — the everyday toggles someone would otherwise
        # have to open config.py to flip.
        bs_card = Card(p, title="BOT SETTINGS")
        bs_card.pack(fill="x", padx=16, pady=8)

        bs_row = tk.Frame(bs_card, bg=C["surface"])
        bs_row.pack(fill="x", padx=12, pady=(4,4))

        self.ai_enabled_var       = tk.BooleanVar(value=True)
        self.telegram_enabled_var = tk.BooleanVar(value=True)
        self.watchdog_restart_var = tk.BooleanVar(value=False)
        self.default_mode_var     = tk.BooleanVar(value=False)   # False=Paper, True=Live

        for var, label in (
            (self.ai_enabled_var,       "AI Analyst"),
            (self.default_mode_var,     "Default mode: LIVE (off = Paper)"),
            (self.telegram_enabled_var, "Telegram Notifications"),
            (self.watchdog_restart_var, "Watchdog Auto-Restart"),
        ):
            ctk.CTkSwitch(bs_row, text=label, variable=var, onvalue=True, offvalue=False,
                        progress_color=C["green"], button_color=C["text"],
                        text_color=C["text"], font=FONT_UI).pack(side="left", padx=(0,20))

        bs_row2 = tk.Frame(bs_card, bg=C["surface"])
        bs_row2.pack(fill="x", padx=12, pady=(8,4))
        tk.Label(bs_row2, text="Paper Starting Pool (USDT):", bg=C["surface"], fg=C["muted"],
                font=FONT_UI).pack(side="left", padx=(0,8))
        self.paper_pool_entry = ctk.CTkEntry(bs_row2, width=100, height=28, corner_radius=6,
                                            fg_color=C["bg"], border_color=C["border"],
                                            text_color=C["text"], font=FONT_MONO)
        self.paper_pool_entry.pack(side="left", padx=(0,20))

        PillButton(bs_row2, "💾  Save Settings", self._save_bot_settings, C["green"]).pack(side="left")

        tk.Label(bs_card,
                text="\"Default mode\" only applies when the bot is launched without an explicit "
                    "mode — the Start button's own Paper/Live prompt always takes priority over it. "
                    "Restart the bot to apply any of these.",
                bg=C["surface"], fg=C["muted"], wraplength=1150, justify="left",
                font=("Segoe UI",8) if sys.platform=="win32" else ("SF Pro",8)
                ).pack(anchor="w", padx=12, pady=(0,10))

        self._load_bot_settings()

        card = Card(p, title="config.py (read-only preview)")
        card.pack(fill="both", expand=True, padx=16, pady=8)

        self.cfg_text = ctk.CTkTextbox(card, fg_color=C["surface"], text_color=C["text"],
                                      font=FONT_MONO, wrap="none", corner_radius=0,
                                      border_width=0, height=220, state="disabled")
        self.cfg_text.tag_config("key",     foreground=C["accent"])
        self.cfg_text.tag_config("val",     foreground=C["green"])
        self.cfg_text.tag_config("section", foreground=C["yellow"])
        self.cfg_text.tag_config("comment", foreground=C["muted"])
        self.cfg_text.pack(fill="both", expand=True, padx=12, pady=8)

    def _build_logs(self):
        p = self._page_frames["Logs"]

        hdr = tk.Frame(p, bg=C["bg"])
        hdr.pack(fill="x", padx=16, pady=(16,8))
        tk.Label(hdr, text="Log Viewer", bg=C["bg"],
                fg=C["text"], font=FONT_TITLE).pack(side="left")

        ctrl = tk.Frame(p, bg=C["bg"])
        ctrl.pack(fill="x", padx=16, pady=4)
        self.log_var = tk.StringVar()
        logs = sorted(glob.glob("logs/bot_*.log"), reverse=True)
        if logs: self.log_var.set(logs[0])

        tk.Label(ctrl, text="File:", bg=C["bg"], fg=C["muted"],
                font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9)
                ).pack(side="left")
        cb = ctk.CTkComboBox(ctrl, variable=self.log_var, values=logs,
                            width=280, height=30, corner_radius=8,
                            fg_color=C["surface"], border_color=C["border"],
                            button_color=C["border"], button_hover_color=C["hover"],
                            dropdown_fg_color=C["surface"], font=FONT_MONO)
        cb.pack(side="left", padx=6)
        PillButton(ctrl, "Load",        self._load_log,  C["accent"]).pack(side="left",  padx=3)
        PillButton(ctrl, "Last 100",    self._tail_log,  C["purple"]).pack(side="left",padx=3)
        PillButton(ctrl, "Auto-follow", self._auto_log,  C["teal"]).pack(side="left",  padx=3)

        card = Card(p)
        card.pack(fill="both", expand=True, padx=16, pady=8)
        self.log_text = ctk.CTkTextbox(card, fg_color="#090d12", text_color="#00d084",
                                      font=FONT_MONO, wrap="none", corner_radius=0,
                                      border_width=0, state="disabled")
        self.log_text.tag_config("ERROR",   foreground=C["red"])
        self.log_text.tag_config("WARNING", foreground=C["yellow"])
        self.log_text.tag_config("BUY",     foreground=C["green"])
        self.log_text.tag_config("SELL",    foreground=C["orange"])
        self.log_text.tag_config("ENGINE",  foreground=C["purple"])
        self.log_text.tag_config("normal",  foreground="#00d084")
        self.log_text.pack(fill="both", expand=True, padx=12, pady=8)
        self._auto_follow = False

    # ══════════════════════════════════════════════════════════════════════
    #  NAVIGATION
    # ══════════════════════════════════════════════════════════════════════

    def _nav(self, page: str):
        for name, (btn, underline) in self._nav_tabs.items():
            active = (name == page)
            btn.configure(fg_color=C["hover"] if active else "transparent",
                         text_color=C["white"] if active else C["muted"])
            underline.configure(fg_color=C["accent"] if active else "transparent")

        for name, frame in self._page_frames.items():
            if name == page:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

        self._current_page = page

        if page == "Pools" and getattr(self, "_last_pool_bar_args", None):
            self.root.after(50, lambda: self._draw_pool_bar(*self._last_pool_bar_args))
        elif page == "News" and getattr(self, "_last_news_scores", None):
            self.root.after(50, lambda: self._draw_news(self._last_news_scores))

    # ══════════════════════════════════════════════════════════════════════
    #  DATA LOADING
    # ══════════════════════════════════════════════════════════════════════

    def _refresh(self):
        threading.Thread(target=self._load_all, daemon=True).start()

    def _load_all(self):
        self._load_config()
        self._load_trades()
        self._load_engine_stats()
        self._load_report()
        self.root.after(0, self._update_clock)

    def _load_config(self):
        try:
            import config as cfg
            importlib.reload(cfg)

            mode    = "📄 PAPER" if cfg.PAPER_TRADING else "💰 LIVE"
            color   = C["yellow"] if cfg.PAPER_TRADING else C["green"]
            self.root.after(0, lambda: self.status_badge.configure(
                text=f"●  {mode}", fg=color))

            pool  = cfg.PAPER_STARTING_USDT
            aggr  = getattr(cfg, "AGGRESSIVE_POOL_PCT", 0.20)
            norm  = 1 - aggr
            res   = getattr(cfg, "LISTING_RESERVE_USDT", 5.0)

            self.root.after(0, lambda: self.t_pool.set(f"${pool:.2f}", "USDT total"))
            self.root.after(0, lambda: self.t_normal.set(f"${pool*norm:.2f}",
                                                         f"{norm*100:.0f}% conservative"))
            self.root.after(0, lambda: self.t_aggr.set(f"${pool*aggr:.2f}",
                                                        f"{aggr*100:.0f}% aggressive",
                                                        C["orange"]))
            # Update pool bar — cached so _nav() can redraw it once the
            # canvas is actually visible/mapped (drawing while the Pools
            # tab is hidden reports a bogus 1px width and draws nothing).
            self._last_pool_bar_args = (pool, pool*norm, pool*aggr, res)
            self.root.after(100, lambda: self._draw_pool_bar(*self._last_pool_bar_args))

            # Normal/aggressive detail tables
            n_data = [
                ("RSI Buy/Sell",  f"{getattr(cfg,'NORMAL_RSI_BUY',35)} / {getattr(cfg,'NORMAL_RSI_SELL',65)}"),
                ("Stop Loss",     f"{getattr(cfg,'NORMAL_STOP_LOSS',0.06)*100:.0f}%"),
                ("Take Profit",   f"{getattr(cfg,'NORMAL_TAKE_PROFIT',0.04)*100:.0f}%"),
                ("Trailing Stop", f"{getattr(cfg,'NORMAL_TRAILING_STOP',0.03)*100:.0f}%"),
                ("Max Hold",      f"{getattr(cfg,'NORMAL_MAX_HOLD_HOURS',48)}h"),
                ("Pool",          f"${pool*norm:.2f} USDT"),
            ]
            a_data = [
                ("RSI Buy/Sell",  f"{getattr(cfg,'AGGRESSIVE_RSI_BUY',42)} / {getattr(cfg,'AGGRESSIVE_RSI_SELL',58)}"),
                ("Stop Loss",     f"{getattr(cfg,'AGGRESSIVE_STOP_LOSS',0.08)*100:.0f}%"),
                ("Take Profit",   f"{getattr(cfg,'AGGRESSIVE_TAKE_PROFIT',0.08)*100:.0f}%"),
                ("Trailing Stop", f"{getattr(cfg,'AGGRESSIVE_TRAILING_STOP',0.04)*100:.0f}%"),
                ("Max Hold",      f"{getattr(cfg,'AGGRESSIVE_MAX_HOLD_HOURS',24)}h"),
                ("Pool",          f"${pool*aggr:.2f} USDT"),
            ]
            self.root.after(0, lambda: self._fill_kv(self.normal_table, n_data))
            self.root.after(0, lambda: self._fill_kv(self.aggr_table,   a_data))

            # Config text
            try:
                with open("config.py", "r", encoding="utf-8") as f:
                    raw = f.read()
                self.root.after(0, lambda: self._render_config(raw))
            except Exception:
                pass

        except Exception as e:
            self.root.after(0, lambda: self.status_badge.configure(
                text=f"● ERROR: {e}", fg=C["red"]))

    def _load_trades(self):
        today    = date.today().strftime("%Y%m%d")
        log_path = f"logs/bot_{today}.log"
        logs     = sorted(glob.glob("logs/bot_*.log"), reverse=True)
        if not Path(log_path).exists() and logs:
            log_path = logs[0]

        trades = []; signals = []
        if Path(log_path).exists():
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "SELL" in line and "Net" in line: trades.append(line.strip())
                    elif "SIGNAL" in line or "ENGINE" in line: signals.append(line.strip())

        wins   = sum(1 for t in trades if "Net +$" in t)
        losses = len(trades) - wins
        wr_str = f"{wins/len(trades)*100:.0f}%" if trades else "—"

        # Parse net P&L from each SELL log line to compute daily ROI
        net_pnl = 0.0
        for t in trades:
            m = re.search(r'Net ([+-])\$([0-9.]+)', t)
            if m:
                net_pnl += (1 if m.group(1) == "+" else -1) * float(m.group(2))

        try:
            import config as _cfg
            importlib.reload(_cfg)
            starting = _cfg.PAPER_STARTING_USDT
        except Exception:
            starting = 100.0
        gain_pct  = (net_pnl / starting * 100) if starting > 0 else 0
        gain_str  = f"{'+' if gain_pct >= 0 else ''}{gain_pct:.2f}%"
        pnl_str   = f"{'+' if net_pnl >= 0 else ''}${net_pnl:.4f}"
        gain_col  = C["green"] if gain_pct >= 0 else C["red"]

        self.root.after(0, lambda: self.t_trades.set(str(len(trades))))
        self.root.after(0, lambda: self.t_pnl.set(pnl_str, "net after fees", gain_col))
        self.root.after(0, lambda: self.t_gain.set(gain_str,
                                                    f"on ${starting:.0f} pool", gain_col))
        self.root.after(0, lambda: self.t_wr.set(wr_str, f"{wins}W  {losses}L",
                                                  C["green"] if wins >= losses else C["red"]))
        self.root.after(0, lambda: self.trade_summary_lbl.configure(
            text=(f"{len(trades)} trades  ·  {wins}W {losses}L  ·  {wr_str} win rate  ·  "
                  f"Net {pnl_str}  ·  ROI {gain_str}")))

        self.root.after(0, lambda: self._populate_signals(signals[-30:]))

    def _load_engine_stats(self):
        try:
            from strategy_engine import _trackers
            if not _trackers:
                return
            # Aggregate across all trackers
            total_trades = sum(len(t.trades) for t in _trackers.values())
            avg_wr  = sum(t.win_rate for t in _trackers.values()) / len(_trackers)
            avg_ev  = sum(t.expectancy for t in _trackers.values()) / len(_trackers)
            avg_pf  = sum(t.profit_factor for t in _trackers.values() if t.profit_factor != float("inf")) or 0

            data = [
                ("Coins tracked",    str(len(_trackers))),
                ("Total trades",     str(total_trades)),
                ("Avg win rate",     f"{avg_wr:.0%}"),
                ("Avg expectancy",   f"${avg_ev:.4f}"),
                ("Avg profit factor",f"{avg_pf:.2f}"),
                ("Status",           "✅ Adapting" if total_trades >= 10 else "⏳ Gathering data"),
            ]
            self.root.after(0, lambda: self._fill_kv(self.engine_table, data))
        except Exception:
            pass

    def _load_news(self):
        def _fetch():
            try:
                from news_aggregator import score_coins_by_news_and_data
                symbols = ["BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT",
                           "ADA-USDT","DOT-USDT","AVAX-USDT","LINK-USDT","LTC-USDT"]
                scores  = score_coins_by_news_and_data(symbols)
                self.root.after(0, lambda: self._draw_news(scores))
            except Exception as e:
                self.root.after(0, lambda: self._draw_news({"_err": str(e)}))
        threading.Thread(target=_fetch, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    #  DRAWING
    # ══════════════════════════════════════════════════════════════════════

    def _draw_pool_bar(self, total, normal, aggr, reserve):
        c = self.pool_bar
        c.delete("all")
        w = c.winfo_width() or 900
        if w < 50: return

        bar_l, bar_r = 12, w - 12
        bar_w = bar_r - bar_l

        # Background
        c.create_rectangle(bar_l, 8, bar_r, 40, fill=C["border"], outline="")

        # Normal
        nm_w = int(bar_w * (normal/total))
        c.create_rectangle(bar_l, 8, bar_l+nm_w, 40, fill=C["green"], outline="")

        # Aggressive
        ag_w = int(bar_w * (aggr/total))
        ag_x = bar_l + nm_w
        c.create_rectangle(ag_x, 8, ag_x+ag_w, 40, fill=C["orange"], outline="")

        # Reserve
        rv_w = int(bar_w * (reserve/total)) if total > 0 else 0
        rv_x = bar_r - rv_w
        c.create_rectangle(rv_x, 8, bar_r, 40, fill=C["yellow"], outline="")

        # Labels
        if nm_w > 80:
            c.create_text(bar_l+nm_w//2, 24, text=f"Normal ${normal:.2f}",
                         fill=C["bg"], font=("Segoe UI",9,"bold") if sys.platform=="win32" else ("SF Pro",9,"bold"))
        if ag_w > 60:
            c.create_text(ag_x+ag_w//2, 24, text=f"Aggr ${aggr:.2f}",
                         fill=C["bg"], font=("Segoe UI",9,"bold") if sys.platform=="win32" else ("SF Pro",9,"bold"))

    def _draw_news(self, scores: dict):
        self._last_news_scores = scores  # so _nav() can redraw once visible/mapped
        c = self.news_canvas
        c.delete("all")
        w = c.winfo_width() or 900
        h = c.winfo_height() or 600

        if "_err" in scores:
            c.create_text(w//2, h//2, text=f"Could not load scores: {scores['_err']}",
                         fill=C["red"],
                         font=("Segoe UI",10) if sys.platform=="win32" else ("SF Pro",10))
            return

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        max_bar = (w - 280) // 2
        y = 20

        for coin, score in sorted_scores[:14]:
            c.create_text(100, y+12, text=coin, fill=C["text"], anchor="e",
                         font=("Segoe UI",10,"bold") if sys.platform=="win32" else ("SF Pro",10,"bold"))
            bar_w = int(abs(score)/5*max_bar)
            color = C["green"] if score >= 0 else C["red"]
            mid   = 110 + max_bar
            if score >= 0:
                c.create_rectangle(mid, y+3, mid+bar_w, y+21, fill=color, outline="")
            else:
                c.create_rectangle(mid-bar_w, y+3, mid, y+21, fill=color, outline="")
            c.create_line(mid, y+1, mid, y+23, fill=C["border"], width=1)
            emoji = "▲" if score>1 else "▼" if score<-1 else "—"
            sign  = "+" if score>=0 else ""
            c.create_text(mid+max_bar+16, y+12,
                         text=f"{emoji} {sign}{score:.1f}",
                         fill=C["text"] if abs(score)>1 else C["muted"],
                         font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9),
                         anchor="w")
            y += 34

    def _populate_signals(self, signals: list):
        self.sig_text.configure(state="normal")
        self.sig_text.delete("1.0", "end")
        for s in signals:
            tag = ("BUY"    if "BUY"     in s else
                   "SELL"   if "SELL"    in s else
                   "ENGINE" if "ENGINE"  in s else
                   "warn"   if "WARNING" in s else "normal")
            line = s[-110:].strip()
            self.sig_text.insert("end", line+"\n", tag)
        self.sig_text.configure(state="disabled")
        self.sig_text.see("end")

    def _render_config(self, text: str):
        self.cfg_text.configure(state="normal")
        self.cfg_text.delete("1.0", "end")
        for line in text.split("\n"):
            if line.strip().startswith("#"):
                self.cfg_text.insert("end", line+"\n", "comment")
            elif "=" in line and not line.strip().startswith("#"):
                parts = line.split("=", 1)
                self.cfg_text.insert("end", parts[0]+"=", "key")
                self.cfg_text.insert("end", parts[1]+"\n", "val")
            else:
                self.cfg_text.insert("end", line+"\n", "normal" if line else "")
        self.cfg_text.configure(state="disabled")

    def _update_clock(self):
        self.clock_lbl.configure(
            text=f"  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")

    # ══════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _make_kv_table(self, parent):
        frame = tk.Frame(parent, bg=C["surface"])
        frame.pack(fill="both", expand=True, padx=12, pady=8)
        return frame

    def _fill_kv(self, frame, data: list):
        for w in frame.winfo_children():
            w.destroy()
        for key, val in data:
            row = tk.Frame(frame, bg=C["surface"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=key, bg=C["surface"], fg=C["muted"], width=18,
                    anchor="w",
                    font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9)
                    ).pack(side="left")
            tk.Label(row, text=val, bg=C["surface"], fg=C["text"],
                    anchor="w",
                    font=("Segoe UI",9,"bold") if sys.platform=="win32" else ("SF Pro",9,"bold")
                    ).pack(side="left")

    # ══════════════════════════════════════════════════════════════════════
    #  CONTROLS
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _pid_alive(pid):
        """Cross-platform PID liveness check — deliberately reimplemented
        here rather than importing watchdog.py, since that module runs
        logging.basicConfig() and opens a raw stdout handle at import
        time (fine for its own standalone process, not something this
        GUI process should also be doing as a side effect of a button)."""
        if not pid:
            return False
        try:
            if sys.platform == "win32":
                result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                        capture_output=True, text=True, timeout=10)
                return str(pid) in result.stdout
            else:
                os.kill(pid, 0)
                return True
        except (ProcessLookupError, PermissionError):
            return False
        except Exception:
            return False

    def _bot_is_running(self):
        """True if logs/liveness.json shows a recent ping from a still-alive
        PID — i.e. some bot.py process (from this GUI, a terminal, or a
        prior session) is currently running, regardless of who started it."""
        try:
            with open("logs/liveness.json") as f:
                data = json.load(f)
            last_ping = datetime.fromisoformat(data["last_ping"])
            if (datetime.now() - last_ping).total_seconds() > 300:
                return False
            return self._pid_alive(data.get("pid"))
        except Exception:
            return False

    def _start_bot(self):
        if self._bot_is_running():
            messagebox.showinfo("Already Running",
                "The bot already appears to be running (a recent liveness "
                "ping was found in logs/liveness.json).\n\n"
                "If this is wrong — e.g. a stale file left over from a "
                "crash — delete logs/liveness.json and try again.")
            return

        mode = messagebox.askyesnocancel("Start Bot",
            "Which mode?\n\n"
            "Yes = LIVE trading (uses real money)\n"
            "No = Paper trading (simulation)\n"
            "Cancel = don't start")
        if mode is None:
            return
        mode_str = "live" if mode else "paper"

        if mode_str == "live" and not messagebox.askyesno("Confirm LIVE Trading",
                "You are about to start the bot in LIVE mode with real funds.\n\n"
                "Are you sure you want to continue?"):
            return

        python_cmd    = sys.executable
        bot_path      = str(Path("bot.py").resolve())
        watchdog_path = str(Path("watchdog.py").resolve())

        try:
            if sys.platform == "win32":
                self._bot_proc = subprocess.Popen(
                    [python_cmd, bot_path, "--mode", mode_str],
                    creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                self._bot_proc = subprocess.Popen(
                    [python_cmd, bot_path, "--mode", mode_str],
                    start_new_session=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not start bot.py:\n{e}")
            return

        # Launch the watchdog alongside it, unless this GUI session already
        # has one running — so clicking Start repeatedly doesn't stack up
        # multiple watchdog processes racing each other on the same bot.
        watchdog_started = False
        if self._watchdog_proc is None or self._watchdog_proc.poll() is not None:
            try:
                if sys.platform == "win32":
                    self._watchdog_proc = subprocess.Popen(
                        [python_cmd, watchdog_path],
                        creationflags=subprocess.CREATE_NEW_CONSOLE)
                else:
                    self._watchdog_proc = subprocess.Popen(
                        [python_cmd, watchdog_path],
                        start_new_session=True)
                watchdog_started = True
            except Exception as e:
                messagebox.showwarning("Watchdog",
                    f"Bot started, but could not launch watchdog.py:\n{e}")

        self.status_badge.configure(text=f"●  STARTING ({mode_str.upper()})", fg=C["teal"])
        messagebox.showinfo("Started",
            f"Bot started in {mode_str.upper()} mode"
            f"{', with the watchdog running alongside it' if watchdog_started else ' (watchdog already running from this session)'}.\n\n"
            "Give it a few seconds, then click Refresh.")
        self.root.after(3000, self._refresh)

    def _pause(self):
        if messagebox.askyesno("Pause Bot",
            "Pause all new buy orders?\nExisting positions continue to be monitored."):
            Path(".bot_pause").touch()
            self.status_badge.configure(text="● PAUSED", fg=C["yellow"])
            messagebox.showinfo("Paused", "Bot paused.\nSend /resume to Telegram or click Resume.")

    def _resume(self):
        Path(".bot_pause").unlink(missing_ok=True)
        self.status_badge.configure(text="● RESUMING", fg=C["green"])
        messagebox.showinfo("Resumed", "Bot will resume buying on next cycle.")

    def _load_log(self):
        p = self.log_var.get()
        if p and Path(p).exists():
            with open(p, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            self._write_log(lines)

    def _tail_log(self):
        p = self.log_var.get()
        if p and Path(p).exists():
            with open(p, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-100:]
            self._write_log(lines)

    def _auto_log(self):
        self._auto_follow = not self._auto_follow
        if self._auto_follow:
            self._tail_log()

    def _write_log(self, lines):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0","end")
        for line in lines:
            tag = ("ERROR"   if "ERROR"   in line else
                   "WARNING" if "WARNING" in line else
                   "BUY"     if "🟢" in line or "BUY" in line else
                   "SELL"    if "🔴" in line or "SELL" in line else
                   "ENGINE"  if "ENGINE"  in line else "normal")
            self.log_text.insert("end", line, tag)
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def _preset_conservative(self):
        self._apply_preset(35,65,0.05,0.05, 40,60,0.07,0.07, "Conservative")

    def _preset_balanced(self):
        self._apply_preset(35,65,0.06,0.04, 42,58,0.08,0.08, "Balanced")

    def _preset_aggressive(self):
        self._apply_preset(38,62,0.08,0.06, 45,55,0.10,0.10, "Aggressive")

    def _apply_preset(self, nb,ns,nsl,ntp, ab,as_,asl,atp, name):
        if not messagebox.askyesno("Apply Preset",
            f"Apply {name} preset?\n\n"
            f"Normal:     RSI {nb}/{ns}  SL {nsl*100:.0f}%  TP {ntp*100:.0f}%\n"
            f"Aggressive: RSI {ab}/{as_}  SL {asl*100:.0f}%  TP {atp*100:.0f}%\n\n"
            "config.py will be updated. Restart bot to apply."):
            return
        try:
            settings_writer.write_config_values({
                "NORMAL_RSI_BUY":nb, "NORMAL_RSI_SELL":ns,
                "NORMAL_STOP_LOSS":nsl, "NORMAL_TAKE_PROFIT":ntp,
                "AGGRESSIVE_RSI_BUY":ab, "AGGRESSIVE_RSI_SELL":as_,
                "AGGRESSIVE_STOP_LOSS":asl, "AGGRESSIVE_TAKE_PROFIT":atp,
            })
            messagebox.showinfo("Done", f"{name} preset applied.\nRestart bot to activate.")
            self._refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _load_bot_settings(self):
        try:
            import config as cfg
            importlib.reload(cfg)
            self.ai_enabled_var.set(bool(getattr(cfg, "AI_ENABLED", True)))
            self.telegram_enabled_var.set(bool(getattr(cfg, "TELEGRAM_ENABLED", True)))
            self.watchdog_restart_var.set(bool(getattr(cfg, "WATCHDOG_AUTO_RESTART", False)))
            self.default_mode_var.set(not bool(getattr(cfg, "PAPER_TRADING", True)))
            self.paper_pool_entry.delete(0, "end")
            self.paper_pool_entry.insert(0, str(getattr(cfg, "PAPER_STARTING_USDT", 100.0)))
        except Exception:
            pass

    def _save_bot_settings(self):
        try:
            pool = float(self.paper_pool_entry.get().strip())
            if pool <= 0:
                raise ValueError("Starting pool must be a positive number")
        except ValueError as e:
            messagebox.showerror("Invalid Value", f"Paper Starting Pool: {e}")
            return

        if not messagebox.askyesno("Save Settings",
                "Save these bot settings to config.py?\nRestart the bot to apply."):
            return
        try:
            settings_writer.write_config_values({
                "AI_ENABLED":              self.ai_enabled_var.get(),
                "PAPER_TRADING":           not self.default_mode_var.get(),
                "TELEGRAM_ENABLED":        self.telegram_enabled_var.get(),
                "WATCHDOG_AUTO_RESTART":   self.watchdog_restart_var.get(),
                "PAPER_STARTING_USDT":     pool,
            })
            messagebox.showinfo("Saved", "Bot settings saved.\nRestart the bot to apply.")
            self._refresh()
        except Exception as e:
            messagebox.showerror("Error", f"Could not save settings:\n{e}")

    # ══════════════════════════════════════════════════════════════════════
    #  EXCHANGE API KEYS  (writes bot_secrets.py + config.py, no .py editing
    #  required — this is the on-ramp for anyone who's never touched the code)
    # ══════════════════════════════════════════════════════════════════════

    def _on_exchange_selected(self, display_value):
        key = self._exch_display_to_key.get(display_value, "kucoin")
        self.exch_key_var.set(key)
        self._build_exchange_fields(key)

    def _build_exchange_fields(self, exchange_key):
        for w in self.exch_fields_frame.winfo_children():
            w.destroy()
        self._exch_entries = {}

        current_values, enabled = {}, True
        try:
            import config as cfg
            importlib.reload(cfg)
            current_values = cfg.EXCHANGES.get(exchange_key, {})
            enabled = current_values.get("enabled", False)
        except Exception:
            pass
        self.exch_enabled_var.set(bool(enabled))

        for field_key, secret_var, label in EXCHANGE_FIELDS[exchange_key]:
            row = tk.Frame(self.exch_fields_frame, bg=C["surface"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=C["surface"], fg=C["muted"], width=26,
                    anchor="w",
                    font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9)
                    ).pack(side="left")
            entry = ctk.CTkEntry(row, height=28, corner_radius=6, fg_color=C["bg"],
                                border_color=C["border"], text_color=C["text"],
                                font=FONT_MONO)
            val = current_values.get(field_key, "")
            if val:
                entry.insert(0, str(val))
            entry.pack(side="left", fill="x", expand=True, padx=(0,12))
            self._exch_entries[field_key] = entry

    def _save_exchange_credentials(self):
        exchange_key = self.exch_key_var.get()
        fields = EXCHANGE_FIELDS[exchange_key]
        values = {secret_var: self._exch_entries[field_key].get().strip()
                 for field_key, secret_var, _label in fields}
        enabled = bool(self.exch_enabled_var.get())

        if not messagebox.askyesno("Save API Keys",
                f"Save {EXCHANGE_DISPLAY[exchange_key]} credentials to bot_secrets.py "
                f"and set it {'enabled' if enabled else 'disabled'} for trading in config.py?\n\n"
                "Restart the bot for this to take effect."):
            return

        try:
            settings_writer.write_bot_secrets(values)
            settings_writer.write_exchange_enabled(exchange_key, enabled)
            messagebox.showinfo("Saved",
                f"{EXCHANGE_DISPLAY[exchange_key]} credentials saved.\n"
                "Restart the bot (or click Start) to apply.")
            self._refresh()
        except Exception as e:
            messagebox.showerror("Error", f"Could not save credentials:\n{e}")

    # ══════════════════════════════════════════════════════════════════════
    #  UPDATES  (reuses auto_updater.py — the same module bot.py's own
    #  background update-checker and the /update Telegram command use)
    # ══════════════════════════════════════════════════════════════════════

    def _check_updates(self, silent=False):
        def _work():
            try:
                import auto_updater
                import config as cfg
                importlib.reload(cfg)
                result = auto_updater.check_for_update(cfg)
            except Exception as e:
                result = {"update_available": False, "reason": f"Update check failed: {e}"}
            self.root.after(0, lambda: self._on_update_check_result(result, silent))
        threading.Thread(target=_work, daemon=True).start()

    def _on_update_check_result(self, result, silent=False):
        if not result.get("update_available"):
            if not silent:
                reason = result.get("reason") or ""
                messagebox.showinfo("Updates",
                    "You're up to date." if reason in ("", "Already up to date")
                    else f"Could not check for updates:\n{reason}")
            return

        local  = (result.get("local_commit")  or "?")[:8]
        remote = (result.get("remote_commit") or "?")[:8]
        if not messagebox.askyesno("Update Available",
                f"A new version is available.\n\n"
                f"Current: {local}\nNew:     {remote}\n\n"
                "Your API keys in bot_secrets.py are gitignored — an update "
                "never touches that file, so there's nothing to re-enter "
                "afterward.\n\n"
                "Pull the update now? (Restart the bot and this GUI "
                "afterward to run the new version.)"):
            return

        def _apply():
            err = None
            try:
                import auto_updater
                import config as cfg
                importlib.reload(cfg)
                ok = auto_updater.perform_update(cfg)
            except Exception as e:
                ok, err = False, str(e)
            self.root.after(0, lambda: self._on_update_applied(ok, err))
        threading.Thread(target=_apply, daemon=True).start()

    def _on_update_applied(self, ok, err):
        if ok:
            messagebox.showinfo("Update Applied",
                "Update pulled successfully. Your bot_secrets.py credentials "
                "were untouched (gitignored) — no need to re-enter them.\n\n"
                "Please restart the bot and this GUI to run the new version.")
        else:
            messagebox.showerror("Update Failed",
                err or "The update could not be applied — check logs/watchdog.log "
                      "or the console for details. A common cause is uncommitted "
                      "local edits to a tracked file (e.g. a hand-edited "
                      "config.py) blocking the pull; commit or discard those first.")

    # ══════════════════════════════════════════════════════════════════════
    #  AUTO-REFRESH
    # ══════════════════════════════════════════════════════════════════════

    def _start_auto_refresh(self):
        self._refresh()
        self._load_news()
        self._tick()
        self.root.after(15_000, lambda: self._check_updates(silent=True))
        self._schedule_update_check()

    def _schedule_update_check(self):
        self.root.after(3_600_000, self._periodic_update_check)   # hourly

    def _periodic_update_check(self):
        self._check_updates(silent=True)
        self._schedule_update_check()

    def _tick(self):
        self._update_clock()
        if self._auto_follow:
            self._tail_log()
        self.root.after(30_000, self._refresh)
        self.root.after(1_000,  self._tick)


def main():
    os.chdir(Path(__file__).parent)
    ctk.set_appearance_mode("dark")
    root = ctk.CTk(fg_color=C["bg"])
    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass
    app = Dashboard(root)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()

if __name__ == "__main__":
    main()

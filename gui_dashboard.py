"""
CryptoTradingBot — Modern GUI Dashboard
==========================================
Clean dark-themed desktop dashboard.
Built with tkinter (included in Python — no extra install).

Run: python gui_dashboard.py
Or:  Double-click START_BOT.bat → [3] GUI Dashboard
"""

import os, sys, glob, threading, importlib, re
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime, date
from pathlib import Path

# ── Palette ────────────────────────────────────────────────────────────────
C = {
    "bg":       "#0d1117",
    "surface":  "#161b22",
    "border":   "#21262d",
    "hover":    "#1f2937",
    "text":     "#e6edf3",
    "muted":    "#8b949e",
    "green":    "#3fb950",
    "red":      "#f85149",
    "blue":     "#58a6ff",
    "yellow":   "#d29922",
    "purple":   "#bc8cff",
    "orange":   "#ffa657",
    "teal":     "#39d353",
    "white":    "#ffffff",
}

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


class Card(tk.Frame):
    def __init__(self, parent, title="", **kw):
        super().__init__(parent, bg=C["surface"],
                        highlightbackground=C["border"],
                        highlightthickness=1, **kw)
        if title:
            tk.Label(self, text=title, bg=C["surface"], fg=C["muted"],
                    font=("Segoe UI", 9, "bold") if sys.platform=="win32" else ("SF Pro",9,"bold"),
                    anchor="w").pack(fill="x", padx=12, pady=(10,2))
            tk.Frame(self, height=1, bg=C["border"]).pack(fill="x", padx=12)


class MetricTile(tk.Frame):
    def __init__(self, parent, label, **kw):
        super().__init__(parent, bg=C["surface"],
                        highlightbackground=C["border"],
                        highlightthickness=1, **kw)
        tk.Label(self, text=label, bg=C["surface"], fg=C["muted"],
                font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9)
                ).pack(anchor="w", padx=12, pady=(10,2))
        self.value_lbl = tk.Label(self, text="—", bg=C["surface"],
                                  fg=C["text"], font=FONT_NUM)
        self.value_lbl.pack(anchor="w", padx=12)
        self.sub_lbl   = tk.Label(self, text="", bg=C["surface"],
                                  fg=C["muted"],
                                  font=("Segoe UI",8) if sys.platform=="win32" else ("SF Pro",8))
        self.sub_lbl.pack(anchor="w", padx=12, pady=(0,10))

    def set(self, value, sub="", color=None):
        self.value_lbl.configure(text=str(value),
                                 fg=color or C["text"])
        self.sub_lbl.configure(text=sub)


class PillButton(tk.Label):
    def __init__(self, parent, text, command=None, color=C["blue"], **kw):
        super().__init__(parent, text=text, bg=color, fg=C["white"],
                        font=("Segoe UI",9,"bold") if sys.platform=="win32" else ("SF Pro",9,"bold"),
                        padx=14, pady=6, cursor="hand2", **kw)
        if command:
            self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self.configure(bg=self._lighten(color)))
        self.bind("<Leave>", lambda e: self.configure(bg=color))
        self._base_color = color

    def _lighten(self, h):
        r,g,b = hex_to_rgb(h)
        r = min(255, r+30); g = min(255, g+30); b = min(255, b+30)
        return f"#{r:02x}{g:02x}{b:02x}"


class Dashboard:

    def __init__(self, root):
        self.root = root
        root.title("CryptoTradingBot")
        root.geometry("1280x820")
        root.minsize(960, 640)
        root.configure(bg=C["bg"])
        self._build()
        self._start_auto_refresh()

    # ══════════════════════════════════════════════════════════════════════
    #  LAYOUT
    # ══════════════════════════════════════════════════════════════════════

    def _build(self):
        self._build_topbar()
        self._build_sidebar()
        self._build_main()

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=C["surface"],
                      highlightbackground=C["border"],
                      highlightthickness=1, height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # Logo + title
        tk.Label(bar, text="⬡", bg=C["surface"], fg=C["blue"],
                font=("Segoe UI",18) if sys.platform=="win32" else ("SF Pro",18)
                ).pack(side="left", padx=(16,4), pady=12)
        tk.Label(bar, text="CryptoTradingBot", bg=C["surface"],
                fg=C["white"], font=FONT_TITLE).pack(side="left", pady=12)

        # Status badge
        self.status_badge = tk.Label(bar, text="● LOADING",
                                     bg=C["surface"], fg=C["yellow"],
                                     font=("Segoe UI",9,"bold") if sys.platform=="win32" else ("SF Pro",9,"bold"))
        self.status_badge.pack(side="left", padx=16)

        self.clock_lbl = tk.Label(bar, text="", bg=C["surface"],
                                  fg=C["muted"],
                                  font=("Segoe UI",9) if sys.platform=="win32" else ("SF Pro",9))
        self.clock_lbl.pack(side="left")

        # Right controls
        ctrl = tk.Frame(bar, bg=C["surface"])
        ctrl.pack(side="right", padx=12)

        PillButton(ctrl, "⏸  Pause",  self._pause,  C["yellow"]).pack(side="left", padx=3)
        PillButton(ctrl, "▶  Resume", self._resume, C["green"]).pack(side="left",  padx=3)
        PillButton(ctrl, "⟳  Refresh",self._refresh,C["blue"]).pack(side="left",  padx=3)

    def _build_sidebar(self):
        self.sidebar = tk.Frame(self.root, bg=C["surface"],
                               highlightbackground=C["border"],
                               highlightthickness=1, width=180)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, text="NAVIGATION", bg=C["surface"],
                fg=C["muted"],
                font=("Segoe UI",8,"bold") if sys.platform=="win32" else ("SF Pro",8,"bold")
                ).pack(anchor="w", padx=16, pady=(20,8))

        self._pages   = {}
        self._nav_btns = {}
        pages = [
            ("📊", "Overview"),
            ("💰", "Pools"),
            ("📋", "Trades"),
            ("📰", "News"),
            ("⚙️", "Config"),
            ("📁", "Logs"),
        ]

        for icon, name in pages:
            btn = tk.Frame(self.sidebar, bg=C["surface"], cursor="hand2")
            btn.pack(fill="x", padx=8, pady=1)
            lbl = tk.Label(btn, text=f"  {icon}  {name}",
                          bg=C["surface"], fg=C["muted"],
                          font=("Segoe UI",10) if sys.platform=="win32" else ("SF Pro",10),
                          anchor="w", padx=8, pady=8)
            lbl.pack(fill="x")
            self._nav_btns[name] = (btn, lbl)
            for w in (btn, lbl):
                w.bind("<Button-1>", lambda e, n=name: self._nav(n))
                w.bind("<Enter>",    lambda e, b=btn, l=lbl: [
                    b.configure(bg=C["hover"]), l.configure(bg=C["hover"])])
                w.bind("<Leave>",    lambda e, n=name, b=btn, l=lbl: [
                    b.configure(bg=C["surface"] if self._current_page != n else C["border"]),
                    l.configure(bg=C["surface"] if self._current_page != n else C["border"])])

        # Strategy info at bottom
        tk.Frame(self.sidebar, height=1, bg=C["border"]).pack(fill="x", padx=16, pady=16)
        self.engine_info = tk.Label(self.sidebar,
                                    text="Engine: loading...",
                                    bg=C["surface"], fg=C["muted"],
                                    font=("Segoe UI",8) if sys.platform=="win32" else ("SF Pro",8),
                                    justify="left", wraplength=150)
        self.engine_info.pack(anchor="w", padx=16)

    def _build_main(self):
        self.main = tk.Frame(self.root, bg=C["bg"])
        self.main.pack(side="left", fill="both", expand=True)

        self._current_page = "Overview"
        self._page_frames  = {}

        for name in ("Overview","Pools","Trades","News","Config","Logs"):
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
        self.t_pnl       = MetricTile(metrics, "NET P&L")
        self.t_wr        = MetricTile(metrics, "WIN RATE")
        for t in (self.t_pool,self.t_normal,self.t_aggr,
                  self.t_trades,self.t_pnl,self.t_wr):
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

        self.sig_text = tk.Text(sig_card, bg=C["surface"], fg=C["text"],
                               font=FONT_MONO, relief="flat", wrap="word",
                               height=10, state="disabled")
        self.sig_text.tag_configure("buy",     foreground=C["green"])
        self.sig_text.tag_configure("sell",    foreground=C["red"])
        self.sig_text.tag_configure("warn",    foreground=C["yellow"])
        self.sig_text.tag_configure("engine",  foreground=C["purple"])
        self.sig_text.tag_configure("normal",  foreground=C["muted"])
        sb = ttk.Scrollbar(sig_card, command=self.sig_text.yview)
        self.sig_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
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

    def _build_news(self):
        p = self._page_frames["News"]

        hdr = tk.Frame(p, bg=C["bg"])
        hdr.pack(fill="x", padx=16, pady=(16,8))
        tk.Label(hdr, text="News Sentiment Scores",
                bg=C["bg"], fg=C["text"], font=FONT_TITLE).pack(side="left")
        PillButton(hdr, "⟳  Refresh", self._load_news, C["blue"]).pack(side="right")
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
        PillButton(preset_row, "Conservative", self._preset_conservative, C["blue"]).pack(side="left",  padx=3)
        PillButton(preset_row, "Balanced",     self._preset_balanced,     C["purple"]).pack(side="left",padx=3)
        PillButton(preset_row, "Aggressive",   self._preset_aggressive,   C["orange"]).pack(side="left",padx=3)
        tk.Label(preset_row,
                text="  ← Updates config.py. Restart bot to apply.",
                bg=C["bg"], fg=C["muted"],
                font=("Segoe UI",8) if sys.platform=="win32" else ("SF Pro",8)
                ).pack(side="left")

        card = Card(p)
        card.pack(fill="both", expand=True, padx=16, pady=8)

        self.cfg_text = tk.Text(card, bg=C["surface"], fg=C["text"],
                               font=FONT_MONO, relief="flat", wrap="none",
                               state="disabled")
        self.cfg_text.tag_configure("key",     foreground=C["blue"])
        self.cfg_text.tag_configure("val",     foreground=C["green"])
        self.cfg_text.tag_configure("section", foreground=C["yellow"])
        self.cfg_text.tag_configure("comment", foreground=C["muted"])
        vsb = ttk.Scrollbar(card, command=self.cfg_text.yview)
        self.cfg_text.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
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
        cb = ttk.Combobox(ctrl, textvariable=self.log_var, values=logs,
                         width=28, font=FONT_MONO)
        cb.pack(side="left", padx=6)
        PillButton(ctrl, "Load",        self._load_log,  C["blue"]).pack(side="left",  padx=3)
        PillButton(ctrl, "Last 100",    self._tail_log,  C["purple"]).pack(side="left",padx=3)
        PillButton(ctrl, "Auto-follow", self._auto_log,  C["teal"]).pack(side="left",  padx=3)

        card = Card(p)
        card.pack(fill="both", expand=True, padx=16, pady=8)
        self.log_text = tk.Text(card, bg="#090d12", fg="#00d084",
                               font=FONT_MONO, relief="flat",
                               wrap="none", state="disabled")
        self.log_text.tag_configure("ERROR",   foreground=C["red"])
        self.log_text.tag_configure("WARNING", foreground=C["yellow"])
        self.log_text.tag_configure("BUY",     foreground=C["green"])
        self.log_text.tag_configure("SELL",    foreground=C["orange"])
        self.log_text.tag_configure("ENGINE",  foreground=C["purple"])
        self.log_text.tag_configure("normal",  foreground="#00d084")
        vsb = ttk.Scrollbar(card, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        hsb = ttk.Scrollbar(card, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        self.log_text.pack(fill="both", expand=True, padx=12, pady=8)
        self._auto_follow = False

    # ══════════════════════════════════════════════════════════════════════
    #  NAVIGATION
    # ══════════════════════════════════════════════════════════════════════

    def _nav(self, page: str):
        for name, (btn, lbl) in self._nav_btns.items():
            active = (name == page)
            bg = C["border"] if active else C["surface"]
            fg = C["white"]  if active else C["muted"]
            btn.configure(bg=bg); lbl.configure(bg=bg, fg=fg)

        for name, frame in self._page_frames.items():
            if name == page:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

        self._current_page = page

    # ══════════════════════════════════════════════════════════════════════
    #  DATA LOADING
    # ══════════════════════════════════════════════════════════════════════

    def _refresh(self):
        threading.Thread(target=self._load_all, daemon=True).start()

    def _load_all(self):
        self._load_config()
        self._load_trades()
        self._load_engine_stats()
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
            # Update pool bar
            self.root.after(100, lambda: self._draw_pool_bar(pool, pool*norm, pool*aggr, res))

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

        wins   = sum(1 for t in trades if "+$" in t)
        losses = len(trades) - wins
        wr_str = f"{wins/len(trades)*100:.0f}%" if trades else "—"

        self.root.after(0, lambda: self.t_trades.set(str(len(trades))))
        self.root.after(0, lambda: self.t_wr.set(wr_str, f"{wins}W {losses}L",
                                                  C["green"] if wins>=losses else C["red"]))
        self.root.after(0, lambda: self.trade_summary_lbl.configure(
            text=f"{len(trades)} trades  ·  {wins} wins  ·  {losses} losses  ·  {wr_str} win rate"))

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
            self.root.after(0, lambda: self.engine_info.configure(
                text=f"Engine\nWR: {avg_wr:.0%}\nEV: ${avg_ev:.4f}"))
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
            with open("config.py","r", encoding="utf-8") as f:
                content = f.read()
            for key,val in [
                ("NORMAL_RSI_BUY",nb),("NORMAL_RSI_SELL",ns),
                ("NORMAL_STOP_LOSS",nsl),("NORMAL_TAKE_PROFIT",ntp),
                ("AGGRESSIVE_RSI_BUY",ab),("AGGRESSIVE_RSI_SELL",as_),
                ("AGGRESSIVE_STOP_LOSS",asl),("AGGRESSIVE_TAKE_PROFIT",atp),
            ]:
                content = re.sub(
                    rf"^({re.escape(key)}\s*=\s*)(.+?)(\s*(?:#.*)?)$",
                    rf"\g<1>{val}\g<3>",
                    content, flags=re.MULTILINE)
            with open("config.py","w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Done", f"{name} preset applied.\nRestart bot to activate.")
            self._refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ══════════════════════════════════════════════════════════════════════
    #  AUTO-REFRESH
    # ══════════════════════════════════════════════════════════════════════

    def _start_auto_refresh(self):
        self._refresh()
        self._load_news()
        self._tick()

    def _tick(self):
        self._update_clock()
        if self._auto_follow:
            self._tail_log()
        self.root.after(30_000, self._refresh)
        self.root.after(1_000,  self._tick)


def main():
    os.chdir(Path(__file__).parent)
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass
    app = Dashboard(root)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()

if __name__ == "__main__":
    main()

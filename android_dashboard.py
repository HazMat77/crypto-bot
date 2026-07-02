"""
android_dashboard.py — Flask dashboard for the Android APK.

Streamlit requires pyarrow (C++) which has no pre-built Android ARM wheel.
This Flask app serves an AMD Adrenaline-themed single-page dashboard with
balances, open positions, today's trades, Start/Stop controls, and a
GitHub auto-update button.

Runs on http://127.0.0.1:8501 (same port MainActivity polls).
"""

import os
import sys
import json
import glob
import threading
import runpy
from pathlib import Path
from datetime import datetime, date
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)
_bot_thread = None
_bot_lock   = threading.Lock()

# ── HTML/JS single-page dashboard ────────────────────────────────────────────
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>HazMat Crypto Bot</title>
<style>
  :root {
    --bg:      #1a1a1a;
    --surface: #242424;
    --border:  #333;
    --red:     #e63b2b;
    --green:   #2ecc71;
    --text:    #e8e8e8;
    --muted:   #8a8a8a;
    --font:    'Segoe UI', system-ui, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; }

  .topbar {
    background: var(--surface); border-bottom: 2px solid var(--red);
    padding: 12px 16px; display: flex; align-items: center; gap: 12px;
    position: sticky; top: 0; z-index: 10;
  }
  .topbar h1 { font-size: 18px; font-weight: 700; color: var(--red); letter-spacing: -.3px; }
  .badge {
    padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700;
    margin-left: auto; white-space: nowrap;
  }
  .badge.running { background: #1a3a25; color: var(--green); border: 1px solid var(--green); }
  .badge.stopped { background: #3a1a1a; color: var(--red);   border: 1px solid var(--red); }

  .tabs { display: flex; border-bottom: 1px solid var(--border); overflow-x: auto; background: var(--surface); }
  .tab {
    padding: 11px 18px; cursor: pointer; color: var(--muted); font-weight: 600;
    font-size: 13px; white-space: nowrap; border-bottom: 3px solid transparent;
  }
  .tab.active { color: var(--red); border-bottom-color: var(--red); }

  .page { display: none; padding: 14px; }
  .page.active { display: block; }

  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(145px, 1fr)); gap: 10px; margin-bottom: 14px; }
  .card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 13px; display: flex; flex-direction: column; gap: 3px;
  }
  .card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  .card .value { font-size: 20px; font-weight: 700; }
  .card .sub   { font-size: 11px; color: var(--muted); }

  .green { color: var(--green); }
  .red   { color: var(--red); }

  .tbl-wrap { overflow-x: auto; border-radius: 10px; border: 1px solid var(--border); }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th {
    background: var(--surface); color: var(--muted); padding: 8px 10px;
    text-align: left; font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: .5px; white-space: nowrap;
  }
  td { padding: 8px 10px; border-top: 1px solid var(--border); }
  tr:hover td { background: #2a2a2a; }

  .btn-row { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
  .btn {
    padding: 10px 20px; border: none; border-radius: 8px; font-weight: 700;
    font-size: 13px; cursor: pointer; transition: opacity .15s; min-width: 130px;
  }
  .btn:active { opacity: .7; }
  .btn-start  { background: var(--green); color: #000; }
  .btn-stop   { background: var(--red);   color: #fff; }
  .btn-paper  { background: #2a4a7a;      color: #fff; }
  .btn-update { background: #2a3a4a;      color: #cce; border: 1px solid #446; }

  h2 {
    font-size: 12px; font-weight: 700; color: var(--muted);
    text-transform: uppercase; letter-spacing: .5px;
    margin-bottom: 10px; margin-top: 18px;
  }
  h2:first-child { margin-top: 0; }

  .info-box {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 13px; font-size: 13px; line-height: 1.6; color: var(--muted);
    margin-bottom: 12px; word-break: break-all;
  }
  .info-box b { color: var(--text); }

  .refresh { font-size: 11px; color: var(--muted); text-align: right; margin-top: 8px; }
  .empty   { color: var(--muted); text-align: center; padding: 28px; font-size: 13px; }

  .toast {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #333; color: #eee; padding: 10px 20px; border-radius: 8px;
    font-size: 13px; z-index: 100; display: none; max-width: 90vw; text-align: center;
  }
</style>
</head>
<body>

<div class="topbar">
  <h1>🔥 HazMat Crypto Bot</h1>
  <span class="badge stopped" id="statusBadge">Stopped</span>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('dashboard', this)">Dashboard</div>
  <div class="tab" onclick="showTab('trades',    this)">Trades</div>
  <div class="tab" onclick="showTab('positions', this)">Positions</div>
  <div class="tab" onclick="showTab('control',   this)">Control</div>
</div>

<!-- Dashboard -->
<div class="page active" id="tab-dashboard">
  <h2>Balances</h2>
  <div class="cards" id="balanceCards"><div class="empty">Loading…</div></div>
  <h2>Today's P&amp;L</h2>
  <div class="cards" id="pnlCards"><div class="empty">Loading…</div></div>
  <div class="refresh" id="lastRefresh"></div>
</div>

<!-- Trades -->
<div class="page" id="tab-trades">
  <h2>Today's Trades</h2>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>Time</th><th>Exchange</th><th>Coin</th><th>Side</th><th>USDT</th><th>P&amp;L</th></tr></thead>
      <tbody id="tradeRows"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody>
    </table>
  </div>
</div>

<!-- Positions -->
<div class="page" id="tab-positions">
  <h2>Open Positions</h2>
  <div class="tbl-wrap">
    <table>
      <thead><tr><th>Exchange</th><th>Coin</th><th>Entry</th><th>Current</th><th>P&amp;L %</th><th>USDT</th></tr></thead>
      <tbody id="posRows"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody>
    </table>
  </div>
</div>

<!-- Control -->
<div class="page" id="tab-control">
  <h2>Bot Control</h2>
  <div class="btn-row">
    <button class="btn btn-start" onclick="startBot('live')">▶ Start Live</button>
    <button class="btn btn-paper" onclick="startBot('paper')">▶ Start Paper</button>
    <button class="btn btn-stop"  onclick="stopBot()">⏹ Stop Bot</button>
  </div>

  <h2>Updates</h2>
  <div class="btn-row">
    <button class="btn btn-update" onclick="checkUpdate()">⬇ Check for Updates</button>
  </div>
  <div class="info-box" id="updateStatus">
    Tap "Check for Updates" to pull the latest bot code from GitHub.<br>
    Your API keys are never touched by an update.
  </div>

  <h2>Status</h2>
  <div class="info-box" id="statusBox">Loading…</div>
</div>

<div class="toast" id="toast"></div>

<script>
function showTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
}

function toast(msg, ms=3000) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  setTimeout(() => t.style.display='none', ms);
}

async function api(url, opts={}) {
  try { const r = await fetch(url, opts); return await r.json(); }
  catch(e) { return null; }
}

function fmtPnl(v) {
  if (v == null) return '—';
  const c = v >= 0 ? 'green' : 'red';
  return `<span class="${c}">${v>=0?'+':''}${v.toFixed(2)}</span>`;
}
function num(v, d=2) { return v != null ? (+v).toFixed(d) : '—'; }

async function refresh() {
  const [st, bal, trades, pos] = await Promise.all([
    api('/api/status'), api('/api/balances'),
    api('/api/trades'), api('/api/positions'),
  ]);

  const running = st && st.running;
  const badge = document.getElementById('statusBadge');
  badge.textContent = running ? 'Running' : 'Stopped';
  badge.className = 'badge ' + (running ? 'running' : 'stopped');

  const sb = document.getElementById('statusBox');
  sb.innerHTML = `<b>Bot:</b> ${running ? '✅ Running' : '⛔ Stopped'}<br>`
    + `<b>Mode:</b> ${(st && st.mode) || '—'}<br>`
    + `<b>Version:</b> ${(st && st.sha) || 'bundled'}`;

  // Balances
  const bc = document.getElementById('balanceCards');
  if (bal && Object.keys(bal).length) {
    bc.innerHTML = Object.entries(bal).map(([ex, b]) =>
      `<div class="card"><div class="label">${ex}</div>`+
      `<div class="value">$${num(b.usdt)}</div>`+
      `<div class="sub">USDT available</div></div>`
    ).join('');
  } else bc.innerHTML = '<div class="empty">No balance data yet</div>';

  // P&L summary
  const pc = document.getElementById('pnlCards');
  if (trades && trades.length) {
    let pnl=0, buys=0, sells=0;
    trades.forEach(t => { pnl += t.pnl||0; if(t.side==='BUY') buys++; else sells++; });
    pc.innerHTML =
      `<div class="card"><div class="label">P&L Today</div>`+
      `<div class="value ${pnl>=0?'green':'red'}">${pnl>=0?'+':''}$${num(pnl)}</div>`+
      `<div class="sub">${trades.length} trades</div></div>`+
      `<div class="card"><div class="label">Buys / Sells</div>`+
      `<div class="value">${buys} / ${sells}</div><div class="sub">today</div></div>`;
  } else pc.innerHTML = '<div class="empty">No trades today</div>';

  // Trades table
  const tb = document.getElementById('tradeRows');
  if (trades && trades.length) {
    tb.innerHTML = trades.slice().reverse().slice(0,80).map(t =>
      `<tr><td>${t.time||'—'}</td><td>${t.exchange||'—'}</td><td>${t.coin||'—'}</td>`+
      `<td style="color:${t.side==='BUY'?'var(--green)':'var(--red)'}"><b>${t.side||'—'}</b></td>`+
      `<td>$${num(t.usdt)}</td><td>${fmtPnl(t.pnl)}</td></tr>`
    ).join('');
  } else tb.innerHTML = '<tr><td colspan="6" class="empty">No trades today</td></tr>';

  // Positions
  const pr = document.getElementById('posRows');
  if (pos && pos.length) {
    pr.innerHTML = pos.map(p =>
      `<tr><td>${p.exchange||'—'}</td><td>${p.coin||'—'}</td>`+
      `<td>$${num(p.entry,4)}</td><td>$${num(p.current,4)}</td>`+
      `<td>${fmtPnl(p.pnl_pct)}%</td><td>$${num(p.usdt)}</td></tr>`
    ).join('');
  } else pr.innerHTML = '<tr><td colspan="6" class="empty">No open positions</td></tr>';

  document.getElementById('lastRefresh').textContent =
    'Updated ' + new Date().toLocaleTimeString();
}

async function startBot(mode) {
  toast('Starting bot in ' + mode + ' mode…');
  await api('/api/start', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify({mode})});
  setTimeout(refresh, 2000);
}
async function stopBot() {
  toast('Stopping bot…');
  await api('/api/stop', {method:'POST'});
  setTimeout(refresh, 1000);
}
async function checkUpdate() {
  const box = document.getElementById('updateStatus');
  box.innerHTML = '⏳ Checking GitHub for updates…';
  const r = await api('/api/update', {method:'POST'});
  if (r) {
    box.innerHTML = r.status || 'Done';
    toast(r.status || 'Done', 5000);
  } else {
    box.innerHTML = 'Could not reach GitHub — check your internet connection.';
  }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(TEMPLATE)

@app.route("/healthz")
def health():
    return "ok", 200

@app.route("/api/status")
def api_status():
    running = _bot_thread is not None and _bot_thread.is_alive()
    sha_path = os.path.join(_bot_dir(), ".android_update_sha")
    sha = open(sha_path).read().strip()[:7] if os.path.exists(sha_path) else "bundled"
    return jsonify({"running": running, "mode": _read_mode(), "sha": sha})

@app.route("/api/balances")
def api_balances():
    return jsonify(_load_balances())

@app.route("/api/trades")
def api_trades():
    return jsonify(_load_trades_today())

@app.route("/api/positions")
def api_positions():
    return jsonify(_load_positions())

@app.route("/api/start", methods=["POST"])
def api_start():
    global _bot_thread
    mode = (request.get_json(silent=True) or {}).get("mode", "paper")
    with _bot_lock:
        if _bot_thread is None or not _bot_thread.is_alive():
            _write_mode(mode)
            _bot_thread = threading.Thread(
                target=_run_bot, args=(mode,), daemon=True, name="hazmat-bot"
            )
            _bot_thread.start()
    return jsonify({"started": True, "mode": mode})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    _write_mode("stopped")
    return jsonify({"stopped": True})

@app.route("/api/update", methods=["POST"])
def api_update():
    try:
        import android_launcher
        msg = android_launcher.trigger_update(_bot_dir())
    except Exception as e:
        msg = f"Update error: {e}"
    return jsonify({"status": msg})


# ── Data helpers ──────────────────────────────────────────────────────────────

def _bot_dir():
    return os.environ.get("HAZMAT_BOT_DIR", os.getcwd())

def _read_mode():
    try:
        return open(os.path.join(_bot_dir(), "logs", "bot_mode.txt")).read().strip()
    except Exception:
        return ""

def _write_mode(mode):
    try:
        p = os.path.join(_bot_dir(), "logs", "bot_mode.txt")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(mode)
    except Exception:
        pass

def _load_balances():
    try:
        return json.loads(open(os.path.join(_bot_dir(), "logs", "balances.json")).read())
    except Exception:
        return {}

def _load_trades_today():
    today = date.today().isoformat()
    trades = []
    for fp in glob.glob(os.path.join(_bot_dir(), "logs", "trades_*.json")):
        try:
            rows = json.loads(open(fp).read())
            for r in (rows if isinstance(rows, list) else [rows]):
                t = r.get("time", "")
                if t.startswith(today):
                    trades.append({
                        "time":     t[11:19] if len(t) > 10 else t,
                        "exchange": r.get("exchange", ""),
                        "coin":     r.get("coin", ""),
                        "side":     r.get("side", ""),
                        "usdt":     r.get("usdt", 0),
                        "pnl":      r.get("pnl"),
                    })
        except Exception:
            pass
    trades.sort(key=lambda x: x["time"])
    return trades

def _load_positions():
    try:
        raw = json.loads(open(os.path.join(_bot_dir(), "logs", "positions.json")).read())
        out = []
        for ex, coins in (raw if isinstance(raw, dict) else {}).items():
            for coin, pos in coins.items():
                entry   = pos.get("entry_price", 0)
                current = pos.get("current_price", entry)
                pnl_pct = ((current - entry) / entry * 100) if entry else 0
                out.append({
                    "exchange": ex, "coin": coin,
                    "entry": entry, "current": current,
                    "pnl_pct": round(pnl_pct, 2),
                    "usdt": pos.get("usdt_value", 0),
                })
        return out
    except Exception:
        return []

def _run_bot(mode):
    bot_path = os.path.join(_bot_dir(), "bot.py")
    sys.argv = ["bot.py", "--mode", mode]
    try:
        runpy.run_path(bot_path, run_name="__main__")
    except SystemExit:
        pass
    except Exception as e:
        print(f"[BotThread] {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def run(host="127.0.0.1", port=8501):
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)

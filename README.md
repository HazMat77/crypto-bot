# CryptoTradingBot

Multi-exchange, multi-coin RSI + MA crypto trading bot with paper/live modes,
Telegram control, an independent watchdog, self-updating via git, and a
startup self-check that protects against orphaned positions after a crash.

Current version: see [`version.py`](version.py)

> Paper trading is the default. Read the whole **Safety** section before
> ever setting `PAPER_TRADING = False`.

---

## Quick start

```bash
git clone <your-fork-or-repo-url>
cd CryptoTradingBot
pip install -r requirements.txt   # or see INSTALL_LINUX.sh / INSTALL.bat
cp bot_secrets.example.py bot_secrets.py
# edit bot_secrets.py with your real API keys (see "Secrets" below)
python bot.py --mode paper
```

In another terminal (or as a separate service — see **Watchdog**):

```bash
python watchdog.py
```

Platform-specific quick starts: `README_WINDOWS.txt`, `README_LINUX.txt`,
`README_ANDROID.txt`. Telegram bot setup: `README_TELEGRAM.txt`.

---

## Secrets — read this before pushing to GitHub

All API keys, tokens, and passphrases live in **`bot_secrets.py`**, not
`config.py`. `bot_secrets.py` is listed in `.gitignore` and is never
committed — only the placeholder template `bot_secrets.example.py` is.

```bash
cp bot_secrets.example.py bot_secrets.py
# then fill in your real KuCoin / Telegram / AI keys in bot_secrets.py
```

If `bot_secrets.py` doesn't exist (e.g. right after a fresh clone),
`config.py` falls back to harmless placeholders so the bot still imports
cleanly — it just can't authenticate with anything until you create
`bot_secrets.py` for real.

**Why it's not named `secrets.py`:** Python's standard library already has
a built-in module called `secrets`. Naming this file `secrets.py` would
shadow it in a way that breaks the safe-fallback logic silently. Don't
rename it.

**Never commit `bot_secrets.py`.** If you ever do by accident, rotate every
key in it immediately — removing the file in a later commit does not
remove it from git history.

---

## Commands (via Telegram)

| Command | What it does |
|---|---|
| `/status` | Pool balance, tier, version, open positions |
| `/heartbeat` | On-demand snapshot — pool, P&L, live unrealized P&L per position |
| `/version` | Current bot version |
| `/trades` | Today's completed trades |
| `/daily` / `/monthly` | Full P&L reports |
| `/coins` | Active trading coins |
| `/news` / `/score` | News headlines / sentiment scores |
| `/regime` / `/engine` / `/diag` / `/capacity` | Strategy diagnostics |
| `/autoapply` | Auto-apply settings + recent self-tuning changes |
| `/aggressive` / `/safe` | Switch risk profile |
| `/sell` | **Sell menu** — see below |
| `/find` | Check now for new BTC/BCH/USDT deposits (live mode) |
| `/pause` / `/resume` / `/stop` | Trading control |
| `/help` | List all commands |

### `/sell` menu

`/sell` no longer immediately liquidates everything. It shows a numbered
menu:

```
1. Sell ALL open positions → USDT
2. Sell ZEC only [KUCOIN]
3. Sell TAO only [KUCOIN]
```

Reply with a number, or just type the coin symbol (e.g. `ZEC`) to sell
that one specifically. The menu expires after 10 minutes or as soon as you
send another command.

### `/heartbeat` vs. the scheduled heartbeat

The bot already sends an automatic heartbeat every 30 minutes. Set
`HEARTBEAT_VISIBLE_BY_DEFAULT = False` in `config.py` to silence that
scheduled message — `/heartbeat` still works on demand at any time
regardless of this setting.

---

## Watchdog

`watchdog.py` is a **separate process** from `bot.py` — if the bot freezes
or crashes outright, a thread living inside that same frozen process
can't save you, so the watchdog runs independently and can detect/alert/
restart even when the bot's own Telegram thread is unresponsive.

```bash
python watchdog.py            # Linux/Mac
WATCHDOG.bat                  # Windows, after starting the bot
```

Config (`config.py`):

```python
WATCHDOG_ENABLED               = True
WATCHDOG_CHECK_INTERVAL_SECS   = 60
LIVENESS_STALE_MINUTES         = 5
WATCHDOG_AUTO_RESTART          = False   # off by default — see note below
WATCHDOG_MAX_RESTARTS_PER_DAY  = 3
```

`WATCHDOG_AUTO_RESTART` only governs restarts after a genuine crash/freeze.
**Restarts after a deliberate auto-update always happen regardless of this
setting** — see Auto-Update below — since the bot exiting to apply an
update is expected to come back up, not something that should wait on a
flag meant for unplanned failures.

---

## Auto-Update

The bot can pull updates from its own GitHub remote and restart itself on
the new code, with the watchdog guaranteeing it comes back up.

### Setup

```bash
git remote add origin <your-repo-url>
git push -u origin main
```

Then in `config.py`:

```python
AUTO_UPDATE_ENABLED             = True
AUTO_UPDATE_CHECK_INTERVAL_SECS = 3600     # check hourly
AUTO_UPDATE_REMOTE              = "origin"
AUTO_UPDATE_BRANCH              = "main"
AUTO_UPDATE_REQUIRE_APPROVAL    = False    # see warning below
```

### How it behaves (current setting: `AUTO_UPDATE_REQUIRE_APPROVAL = False`)

When a new commit is found on `origin/main`, the bot:

1. Pulls it immediately — **in both paper AND live mode** — with no
   waiting period and no Y/N approval gate.
2. Sends you a Telegram notice *after* the pull, not before.
3. Exits cleanly. The watchdog detects this was a deliberate update (not a
   crash) and relaunches `bot.py` on the new code automatically — this
   restart happens even if `WATCHDOG_AUTO_RESTART` is off, and does not
   count against `WATCHDOG_MAX_RESTARTS_PER_DAY`.

> **This means new code can take control of real trades with zero human
> review window, in live mode, the moment you push to `main`.** That's the
> behavior you asked for. If you'd rather review changes before they go
> live — especially for a bot trading real money — set
> `AUTO_UPDATE_REQUIRE_APPROVAL = True` instead, which routes the update
> through the same Y/N Telegram approval gate used for monthly strategy
> reviews, and only pulls after you reply `Y`.

### Safety guard

If you've hand-edited a tracked file (e.g. `config.py`) on the deployed
machine and haven't committed it, the auto-updater refuses to pull —
`git pull` over uncommitted changes can silently overwrite or conflict.
You'll get a Telegram notice explaining exactly that instead.

`bot_secrets.py` and everything under `logs/` are gitignored and untouched
by any of this either way.

---

## Startup self-check (orphaned position recovery)

**Live mode only.** In live trading, the bot's notion of "what positions
are open" lives entirely in memory. If the bot crashes or the machine
loses power, that memory is gone on restart — but anything the bot had
bought is still sitting on the exchange, with nothing watching it: no
stop-loss, no take-profit, nothing.

On every startup, after coin discovery and before any trading begins, the
bot:

1. Pulls every nonzero balance directly from the exchange (currently
   implemented for KuCoin — see `get_all_balances()` in `exchanges.py`).
2. Compares it against the coins it's about to actively track.
3. Anything held that isn't already expected is an **orphan**. Each orphan
   is:
   - **Adopted** into tracking (so stop-loss/take-profit/`/sell` all apply
     to it going forward, and it appears in `/status`), using the
     **current market price as an estimated entry price** — the real
     entry price was lost along with the rest of the crashed process's
     memory.
   - Reported loudly via Telegram, and flagged in `/status` with a visible
     ⚠️ *estimated entry* tag so this is never mistaken for a known number.
4. If a balance is found but can't be priced, or an exchange doesn't
   implement `get_all_balances()` yet, you get an explicit warning instead
   of a false "all clear."

Balances below $1 (configurable via `ORPHAN_DUST_THRESHOLD_USDT` in
`bot.py`) are treated as rounding dust and ignored.

**Only KuCoin has `get_all_balances()` implemented right now.** Other
exchanges will report "could not be checked" on every startup until
someone adds the equivalent bulk-balance call for them in `exchanges.py`.

---

## Version

The bot's version lives in a single place: [`version.py`](version.py). It
shows up in `/status`, `/version`, and the startup Telegram banner. Bump it
on every release you intend to push.

---

## Safety reminders

- API keys should have **Trade permission only — never withdrawal.**
- Always test in `PAPER_TRADING = True` before going live.
- `WATCHDOG_AUTO_RESTART` is off by default for genuine crashes — turning
  it on means a persistently broken bot could restart-loop; the daily
  restart cap (`WATCHDOG_MAX_RESTARTS_PER_DAY`) exists specifically to
  catch that and demand a human look at it.
- Auto-update is off by default. Read the **Auto-Update** section above in
  full before enabling it on a live-money deployment.

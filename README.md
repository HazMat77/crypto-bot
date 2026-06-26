# CryptoTradingBot

Multi-exchange, multi-coin RSI + MA crypto trading bot with paper/live modes,
Telegram control, an independent watchdog, self-updating via git, and a
startup self-check that protects against orphaned positions after a crash.

Current version: see [`version.py`](version.py)

> Paper trading is the default. Read the whole **Safety** section before
> ever setting `PAPER_TRADING = False`.

This repo is public so anyone can clone and run their own independent copy.
**Each person's API keys and Telegram credentials stay entirely local to
their own machine** — see **Secrets** below for why this is safe even
with auto-update checking in the background.

---

## Setup (anyone cloning this)

```bash
git clone https://github.com/HazMat77/crypto-bot.git
cd crypto-bot
pip install -r requirements.txt
cp bot_secrets.example.py bot_secrets.py
# edit bot_secrets.py with YOUR OWN KuCoin/Telegram/AI keys — never anyone else's
python bot.py --mode paper
```

In another terminal (or as a separate service — see **Watchdog**):

```bash
python watchdog.py
```

---

## Quick start — platform-specific guides

`README_WINDOWS.txt`, `README_LINUX.txt`, `README_ANDROID.txt` for
install-script details on each OS. Telegram bot setup: `README_TELEGRAM.txt`.

---

## Running it (Windows, manual start)

Every time you want to trade, open two Command Prompt / PowerShell windows
in the bot's folder:

```cmd
python bot.py --mode paper
```
```cmd
python watchdog.py
```

(or double-click `START_BOT.bat` and `WATCHDOG.bat` instead — same effect)

Closing either window, or shutting down/restarting the PC, stops both.
Nothing trades or auto-updates unless you've deliberately started `bot.py`
yourself — this is intentional, by request, rather than an unattended
always-on setup. Keep **both** windows open together if you want the
watchdog's auto-restart-after-update behavior to actually apply; if only
`bot.py` is running and it exits to apply an update, nothing will bring it
back up until you relaunch it yourself.

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

### Multiple people running their own clone of this same repo

`bot_secrets.py` is gitignored on every clone independently — it's never
part of any commit, push, or pull. This means:

- Your `bot_secrets.py` and someone else's `bot_secrets.py` never interact,
  collide, or overwrite each other, even though you're both tracking the
  same `origin/main`.
- An update — in any `AUTO_UPDATE_MODE` — only ever touches *tracked*
  files (the actual `.py` code). It has no mechanism to read, modify, or
  replace an untracked file. Your KuCoin keys, Telegram token, and AI key
  stay exactly as you typed them, regardless of how many updates get
  applied.
- Each person decides for themselves when (or whether) to apply an
  update — see **Auto-Update** below. The default mode (`notify_only`)
  means a push from the repo owner never silently takes over anyone else's
  running bot.

---

## Commands (via Telegram)

| Command | What it does |
|---|---|
| `/status` | Pool balance, tier, version, open positions |
| `/heartbeat` | On-demand snapshot — pool, P&L, live unrealized P&L per position |
| `/version` | Current bot version |
| `/update` | Check for and apply an available update right now |
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

The bot can check its own GitHub remote for new commits. **By default it
only ever notifies you — it never pulls or restarts on its own.** You (or
anyone running their own clone) decide when to actually apply an update.

### Setup

If you haven't already:

```bash
git remote add origin https://github.com/HazMat77/crypto-bot.git
git push -u origin main
```

Config (`config.py`) — these are the defaults, nothing extra to set up:

```python
AUTO_UPDATE_ENABLED             = True          # checking is always on by default
AUTO_UPDATE_CHECK_INTERVAL_SECS = 3600          # check hourly
AUTO_UPDATE_REMOTE              = "origin"
AUTO_UPDATE_BRANCH              = "main"
AUTO_UPDATE_MODE                = "notify_only" # see the three modes below
```

### The three modes (`AUTO_UPDATE_MODE`)

**`"notify_only"` — the default, recommended for everyone, especially if
other people are running their own clone of this bot.** When a new commit
is found:

1. You get exactly one Telegram message saying an update is available —
   you won't be re-notified for the same commit on every later check.
2. **Nothing else happens.** No file changes, no restart, no pull.
3. Whenever it's convenient, send **`/update`** in Telegram, or run
   `git pull` yourself on the machine. Either way, it's a deliberate action
   you take, not something that happens to you.

This is the only mode where a push to your repo can never silently take
over someone else's running bot — including your own, in live mode.

**`"require_approval"`** — sends a Y/N Telegram approval request (the same
mechanism used for monthly strategy reviews) and only pulls after an
explicit `Y` reply. Functionally similar to `notify_only`, but goes
through the formal approval-gate flow and audit log instead of `/update`.

**`"auto_apply"`** — pulls immediately the moment an update is found, in
**both paper and live mode**, with no review window — notifies only
*after* the pull, then restarts. The watchdog detects this was a
deliberate update (not a crash) and relaunches `bot.py` automatically;
this restart happens even if `WATCHDOG_AUTO_RESTART` is off, and doesn't
count against `WATCHDOG_MAX_RESTARTS_PER_DAY`.

> ⚠️ **`auto_apply` means a single push from you takes effect on every
> running bot in that mode — including anyone else's live-money bot — within
> `AUTO_UPDATE_CHECK_INTERVAL_SECS`, with no chance for them to review it
> first.** Only use this for a bot you alone run, and even then, only once
> you trust your own testing before every push.

### Safety guard (applies to every mode)

If a tracked file (e.g. `config.py`) has been hand-edited on the deployed
machine and not committed, the auto-updater refuses to pull — `git pull`
over uncommitted changes can silently overwrite or conflict. You'll get a
Telegram notice explaining exactly that instead, in any mode.

`bot_secrets.py` and everything under `logs/` are gitignored and untouched
by any of this, in every mode — an update can never read, change, or
overwrite anyone's API keys or Telegram credentials.

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

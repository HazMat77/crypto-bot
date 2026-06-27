# ══════════════════════════════════════════════════════════════════════════════
#  CryptoTradingBot — Configuration
#  
#  !! SECURITY REMINDER !!
#  Never share this file or paste these keys into any chat, email, or forum.
#  Anyone with your API keys can trade on your account.
#  Keys should have TRADE permission only — never withdrawal permission.
# ══════════════════════════════════════════════════════════════════════════════

# ── SECRETS — loaded from bot_secrets.py, which is gitignored and never
#   committed. If bot_secrets.py doesn't exist yet (e.g. straight after a
#   fresh git clone, before you've run `cp bot_secrets.example.py bot_secrets.py`
#   and filled it in), fall back to harmless placeholders so the bot still
#   imports cleanly instead of crashing — it just won't be able to
#   authenticate with anything until bot_secrets.py is created for real.
#   See bot_secrets.example.py for the full list of fields and setup steps.
#
#   NOTE: this file is deliberately named "bot_secrets.py", NOT "secrets.py" —
#   Python's standard library already has a built-in module called `secrets`
#   (used for cryptographic random tokens). Naming this file secrets.py would
#   shadow it in a way that's invisible most of the time but breaks silently
#   and confusingly: if bot_secrets.py is ever missing, `import secrets` would
#   succeed anyway by quietly grabbing the *stdlib* module instead of raising
#   ImportError, so the safe fallback below would never trigger.
try:
    import bot_secrets as _secrets
except ImportError:
    class _secrets:
        KUCOIN_API_KEY = KUCOIN_API_SECRET = KUCOIN_PASSPHRASE = "YOUR_KUCOIN_API_KEY"
        BINANCE_API_KEY = BINANCE_API_SECRET = ""
        KRAKEN_API_KEY = KRAKEN_API_SECRET = ""
        BYBIT_API_KEY = BYBIT_API_SECRET = ""
        OKX_API_KEY = OKX_API_SECRET = OKX_PASSPHRASE = ""
        GATEIO_API_KEY = GATEIO_API_SECRET = ""
        MEXC_API_KEY = MEXC_API_SECRET = ""
        WEBULL_API_KEY = WEBULL_API_SECRET = ""
        VIRGOCX_API_KEY = VIRGOCX_API_SECRET = ""
        TELEGRAM_TOKEN   = "YOUR_TELEGRAM_BOT_TOKEN"
        TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"
        AI_API_KEY   = "YOUR_ANTHROPIC_API_KEY_HERE"
        GROK_API_KEY = "YOUR_GROK_API_KEY_HERE"
    import logging
    logging.getLogger(__name__).warning(
        "[CONFIG] bot_secrets.py not found — using placeholder credentials. "
        "Run: cp bot_secrets.example.py bot_secrets.py   then fill in your real keys."
    )

# ── SAFETY ────────────────────────────────────────────────────────────────
PAPER_TRADING        = True    # True = simulation only. False = real money.
PAPER_STARTING_USDT  = 100.0   # Starting pool per exchange (paper mode)

# ══════════════════════════════════════════════════════════════════════════════
#  DYNAMIC SCALING TIERS
#  Bot auto-selects the highest tier your pool qualifies for.
#  Adjusts trade size and number of active coins automatically.
# ══════════════════════════════════════════════════════════════════════════════

SCALING_TIERS = [
    { "min_pool":    0, "max_per_trade":  5, "max_coins": 15, "label": "Starter"  },
    { "min_pool":  250, "max_per_trade": 12, "max_coins": 15, "label": "Growth"   },
    { "min_pool":  500, "max_per_trade": 20, "max_coins": 15, "label": "Advanced" },
    { "min_pool": 1000, "max_per_trade": 30, "max_coins": 20, "label": "Pro"      },
    { "min_pool": 2500, "max_per_trade": 60, "max_coins": 20, "label": "Pro+"     },
    { "min_pool": 5000, "max_per_trade": 70, "max_coins": 50, "label": "Elite"    },
    # NOTE: max_per_trade × max_coins should be able to reach roughly
    # the pool size so capital isn't stuck idle. Previous values capped
    # total possible deployment well below 100% of pool (e.g. $5 x 4 = $20
    # max deployed on a $100 pool = 20% ceiling even with all signals firing).
    #
    # Coin-count floor by pool size (always at least this many monitored):
    #   any pool size : 15 coins minimum
    #   $1,000+        : 20 coins
    #   $5,000+        : 50 coins
]

MIN_TRADE_USDT = 10.0
TRADE_PCT      = 0.95

# ── Coin discovery filters ─────────────────────────────────────────────────
MIN_VOLUME_USDT  = 100_000
EXCLUDE_KEYWORDS = [
    "UP","DOWN","BULL","BEAR","3L","3S","2L","2S",
    "USDC","BUSD","DAI","TUSD","USDT","FDUSD","USDP",
]

# ── Candle interval ────────────────────────────────────────────────────────
CANDLE_INTERVAL = "15min"

# ── Poll interval — 90s recommended for 19+ coins to avoid rate limits ────
POLL_SECONDS = 90

# ── RSI settings — 45/55 for testing, tighten to 35/65 for live trading ──
RSI_PERIOD = 14
# ── RSI thresholds ─────────────────────────────────────────────────────────
# PAPER TESTING:  RSI_BUY = 45, RSI_SELL = 55  (more signals to test with)
# LIVE TRADING:   RSI_BUY = 35, RSI_SELL = 65  (conservative, less false signals)
# Currently set for LIVE — change back to 45/55 if you want more paper test signals
RSI_BUY    = 25
RSI_SELL   = 55

# ── Moving Average ─────────────────────────────────────────────────────────
MA_PERIOD = 20

# ══════════════════════════════════════════════════════════════════════════════
#  RISK CONTROLS  ← CRITICAL — protects your pool from large losses
# ══════════════════════════════════════════════════════════════════════════════

# ── Stop-Loss (measured from the PEAK price, not from entry) ──────────────
# Forces a sell if the current price has dropped STOP_LOSS_PCT below the
# HIGHEST price reached since you bought — not below your original buy
# price. This means the stop-loss is ALWAYS active, even while a position
# is profitable overall:
#   Example: bought BTC at $100. Price rises to $115 (new peak), then
#   falls to $110.40 — a 4% drop from the $115 peak. This SELLS, even
#   though the position is still +10.4% versus the original $100 entry.
# If price never rises above entry, the peak stays at the buy price, so
# the stop behaves like a normal stop-loss from entry in that case.
STOP_LOSS_ENABLED = True
STOP_LOSS_PCT     = 0.04   # 4% drop from the peak triggers a sell

# ── GLOBAL HARD CEILING — no stop loss anywhere in the bot can exceed this ──
# This is enforced in code (risk_manager.py, strategy_engine.py, strategy_optimizer.py,
# adaptive_intelligence.py) regardless of regime, preset, AI suggestion, or
# /aggressive mode. A losing trade will never be allowed to exceed this
# percentage of the position size. This is the ceiling for AGGRESSIVE mode
# specifically (10%) — safe mode stays well under it at 4%.
MAX_STOP_LOSS_PCT = 0.10
                            # Safe mode: 0.04 (4%)  Aggressive ceiling: 0.10 (10%)

# ── Take-Profit (measured from your original entry price) ─────────────────
# Forces a sell once the price is TAKE_PROFIT_PCT above your buy price.
# Example: bought at $100, TAKE_PROFIT_PCT=0.25 → auto-sell at $125
TAKE_PROFIT_ENABLED = True
TAKE_PROFIT_PCT     = 0.25  # 25% gain from entry triggers a sell

# ── Max Hold Time ─────────────────────────────────────────────────────────
# Auto-sells a position if held longer than X hours, regardless of price.
# Prevents being stuck in a dead trade indefinitely. Whichever of the
# three conditions (stop-loss from peak, take-profit from entry, or this
# max hold time) is reached FIRST is what actually triggers the sell.
MAX_HOLD_HOURS    = 48      # sell after 48 hours regardless of price
                            # Set to 0 to disable

# ── Drawdown Protection — TIERED CIRCUIT BREAKER ──────────────────────────
# Instead of a single on/off pause, the bot now escalates through 4 levels
# as the pool drops further from its peak:
#
#   < CAUTION%             : normal — full position sizes
#   CAUTION% - PAUSE%      : new buy sizes cut by 50%, still trading
#   PAUSE% - EMERGENCY%    : new buys fully paused, existing positions held
#   >= EMERGENCY%          : closes ALL open positions + full bot pause,
#                             requires explicit /resume — does NOT auto-recover
#
# Recovery between the lower tiers (caution/pause/normal) happens
# automatically as the pool climbs back up. Emergency-level recovery is
# intentionally NOT automatic — that's the one tier that always needs a
# human to look at what happened before trading resumes.
DRAWDOWN_CAUTION_PCT   = 0.10   # 10% drop — half-size positions
DRAWDOWN_PAUSE_PCT     = 0.15   # 15% drop — pause new buys
DRAWDOWN_EMERGENCY_PCT = 0.25   # 25% drop — close everything, full stop
                                 # Set DRAWDOWN_EMERGENCY_PCT to 0 to disable
                                 # the emergency tier entirely (not recommended)

# ── Volatility filter (ATR-based position sizing) ─────────────────────────
# Reduces trade size on highly volatile coins to limit risk.
# True = trade size scales DOWN when ATR is high (coin is very volatile)
# False = fixed trade size regardless of volatility
VOLATILITY_SIZING  = True
VOLATILITY_ATR_PERIOD = 14  # ATR period for volatility measurement

# ══════════════════════════════════════════════════════════════════════════════
#  PRICE DATA SOURCE
#  Using CoinGecko (free, no API key needed) for market prices
#  to reduce KuCoin API calls. Candle data still from KuCoin.
#  Options: "kucoin" | "coingecko" | "binance_public"
# ══════════════════════════════════════════════════════════════════════════════

PRICE_SOURCE = "coingecko"   # free public API, no key needed

# ══════════════════════════════════════════════════════════════════════════════
#  EXCHANGE CREDENTIALS
#  Fill in whichever exchanges you have accounts on.
#  Set "enabled": False to skip an exchange.
#  Keys must have TRADE permission only — never withdrawal.
# ══════════════════════════════════════════════════════════════════════════════

EXCHANGES = {

    "kucoin": {
        "enabled":    True,
        "api_key":    _secrets.KUCOIN_API_KEY,
        "api_secret": _secrets.KUCOIN_API_SECRET,
        "passphrase": _secrets.KUCOIN_PASSPHRASE,
    },

    "binance": {
        "enabled":    False,
        "api_key":    _secrets.BINANCE_API_KEY,
        "api_secret": _secrets.BINANCE_API_SECRET,
    },

    "kraken": {
        "enabled":    True,
        "api_key":    _secrets.KRAKEN_API_KEY,
        "api_secret": _secrets.KRAKEN_API_SECRET,
    },

    "bybit": {
        "enabled":    False,
        "api_key":    _secrets.BYBIT_API_KEY,
        "api_secret": _secrets.BYBIT_API_SECRET,
    },

    "okx": {
        "enabled":    False,
        "api_key":    _secrets.OKX_API_KEY,
        "api_secret": _secrets.OKX_API_SECRET,
        "passphrase": _secrets.OKX_PASSPHRASE,
    },

    "gateio": {
        "enabled":    False,
        "api_key":    _secrets.GATEIO_API_KEY,
        "api_secret": _secrets.GATEIO_API_SECRET,
    },

    "mexc": {
        "enabled":    False,
        "api_key":    _secrets.MEXC_API_KEY,
        "api_secret": _secrets.MEXC_API_SECRET,
    },

    "webull": {
        "enabled":    False,
        "api_key":    _secrets.WEBULL_API_KEY,    # App Key from developer.webull.com
        "api_secret": _secrets.WEBULL_API_SECRET, # App Secret from developer.webull.com
        "region":     "us",        # "us" or "hk" — see Webull OpenAPI docs
        "sandbox":    True,        # True = UAT test environment, no real trades.
                                    # Set False only once you have approved
                                    # production credentials and have tested
                                    # thoroughly in sandbox first.
        # Webull crypto requires a ONE-TIME interactive approval via the
        # Webull mobile app the first time these credentials authenticate —
        # the bot will pause on startup waiting for that approval. See the
        # comment block above WebullExchange in exchanges.py for details.
    },

    "virgocx": {
        "enabled":    False,
        "api_key":    _secrets.VIRGOCX_API_KEY,    # From https://virgocx.ca/en-virgocx-api
        "api_secret": _secrets.VIRGOCX_API_SECRET, # From https://virgocx.ca/en-virgocx-api
        # IMPORTANT: VirgoCX requires this machine's IP address to be
        # WHITELISTED in your VirgoCX API settings before any request
        # will succeed. Orders will fail with an auth error otherwise.
        #
        # IMPORTANT: ALL VirgoCX pairs are quoted in CAD, not USDT
        # (e.g. "BTC/CAD"). Your "trading pool" on this exchange is
        # effectively a CAD balance, not USDT — keep that in mind when
        # reading pool size / profit numbers for this exchange.
        #
        # Requires: pip install vcx-py
    },
}

# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM NOTIFICATIONS
#  See README_WINDOWS.txt for setup instructions.
# ══════════════════════════════════════════════════════════════════════════════

TELEGRAM_ENABLED  = True
TELEGRAM_TOKEN    = _secrets.TELEGRAM_TOKEN
TELEGRAM_CHAT_ID  = _secrets.TELEGRAM_CHAT_ID

NOTIFY_ON_BUY       = True
NOTIFY_ON_SELL      = True
NOTIFY_ON_ERROR     = True
NOTIFY_ON_START     = True
NOTIFY_ON_STOP      = True
NOTIFY_BALANCE_SKIP = True

# ── Scheduled heartbeat visibility ──────────────────────────────────────────
# The bot sends an automatic heartbeat (pool balance, P&L, open positions)
# every 30 minutes. Set this False to silence that scheduled message while
# still keeping /heartbeat available on demand at any time — useful if you
# want the data there when you ask for it, without a notification every
# half hour whether you're looking or not.
HEARTBEAT_VISIBLE_BY_DEFAULT = True

# ══════════════════════════════════════════════════════════════════════════════
#  AI TRADING
#  See README_WINDOWS.txt for API key setup instructions.
#  Paper mode automatically uses free fake AI — no key needed.
# ══════════════════════════════════════════════════════════════════════════════

AI_ENABLED        = True
AI_API_KEY        = _secrets.AI_API_KEY
AI_MODE           = "alongside"   # "alongside" | "filter" | "full"
AI_NEWS_SEARCH    = True          # FREE — reads RSS from The Block, CoinDesk, Blockworks,
                                  # Cointelegraph, The Defiant. No API key needed.
AI_CONFIDENCE_MIN = 70

# ── Strategy engine approval threshold ─────────────────────────────────────
# Separate from AI_CONFIDENCE_MIN above. This gates the strategy_engine.py
# filter stack (volume, ADX, liquidity, news veto) AFTER RSI+MA already
# triggered a signal. Keep this lower than AI_CONFIDENCE_MIN since the
# signal has already been confirmed once before reaching this check.
# If trades are rarely firing, lower this first before touching RSI thresholds.
ENGINE_CONFIDENCE_MIN = 55

# ── Also supports Grok (xAI) as an alternative AI provider ────────────────
# Set AI_PROVIDER = "grok" and fill in GROK_API_KEY to use Grok instead.
# See README_WINDOWS.txt for how to get a Grok API key.
AI_PROVIDER   = "claude"   # "claude" | "grok"
GROK_API_KEY  = _secrets.GROK_API_KEY

# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-CONVERT (15th and 30th of each month)
#  Sells 80% of BTC/BCH/XRP to USDT and adds to trading pool.
# ══════════════════════════════════════════════════════════════════════════════

AUTO_CONVERT_ENABLED = True
AUTO_CONVERT_COINS   = ["BTC", "BCH", "XRP"]
AUTO_CONVERT_PCT     = 0.80

# ── Deposit detection ──────────────────────────────────────────────────────
DEPOSIT_MONITOR_ENABLED = True

# ══════════════════════════════════════════════════════════════════════════════
#  NEW LISTING HUNTER
#  Monitors KuCoin announcements for new coin listings.
#  Auto-buys at listing time and sells when profitable.
#  KuCoin new listings often spike 50-200%+ in first few minutes.
# ══════════════════════════════════════════════════════════════════════════════

LISTING_HUNTER_ENABLED = True
LISTING_BUY_USDT       = 5.0    # fixed $5 per new listing
LISTING_TAKE_PROFIT    = 0.15   # sell at +15% gain
LISTING_STOP_LOSS      = 0.05   # sell at -8% loss
LISTING_MAX_HOLD_MINS  = 168    # force sell after 2 hours

# ── Listing reserve ────────────────────────────────────────────────────────
# This amount is ALWAYS kept in the pool untouched by regular trades.
# It is ONLY used for new listing buys.
# Regular trading uses (pool - LISTING_RESERVE_USDT) as its available balance.
# Set to 0.0 to disable the reserve (listing buys compete with regular trades).
LISTING_RESERVE_USDT   = 5.0    # keep $5 protected for new listings always

# ── Capital floor — genuine emergency reserve ──────────────────────────────
# A separate, larger reserve on top of LISTING_RESERVE_USDT above. This is
# calculated as a % of your STARTING capital (a fixed number, unlike the
# drawdown circuit breakers which track the fluctuating peak) and the bot
# will NEVER trade with it, regardless of signals, regime, or approval-gate
# changes. Think of this as money that's already "out" of the trading pool
# even though it technically still sits in the same exchange account.
#
# This stacks with (doesn't replace) the tiered drawdown circuit breakers
# in DRAWDOWN_CAUTION_PCT / DRAWDOWN_PAUSE_PCT / DRAWDOWN_EMERGENCY_PCT
# above — those should kick in and protect capital well before this floor
# is ever approached. This is the final backstop, not the first line of
# defence.
#
# OFF by default (0.0) so existing setups aren't silently changed by this
# update. Recommended range if you enable it: 0.30-0.50 (keep 30-50% of
# starting capital completely untouchable).
CAPITAL_FLOOR_PCT      = 0.0    # e.g. 0.30 = bottom 30% of starting capital
                                  # is NEVER deployed, no matter what

# ── News-based coin selection ──────────────────────────────────────────────
# True  = on startup AND every hour, coins are ranked by news sentiment
#         from The Block, CoinDesk, Blockworks, Cointelegraph, The Defiant
#         Combined score: 70% volume + 30% news sentiment
# False = rank by volume only (original behaviour)
NEWS_COIN_RANKING = True

# ── Correlation-aware coin selection ───────────────────────────────────────
# True  = after ranking by volume+news, actively skips candidates that are
#         highly correlated (>0.80) with a coin already selected, picking
#         the next-best alternative instead. Prevents ending up with e.g.
#         6 coins that all just move together with BTC and calling that
#         diversification.
# False = plain top-N ranking, no correlation check (faster, original behaviour)
# Adds a small delay during coin discovery/re-rank (fetches price history
# per candidate) — disable if discovery cycles feel slow on a weak connection.
CORRELATION_AWARE_SELECTION = True

# ══════════════════════════════════════════════════════════════════════════════
#  MONTHLY AI STRATEGY SELF-IMPROVEMENT
#  On the 1st of each month, the bot reviews its own performance and
#  asks Claude/Grok to suggest parameter updates based on:
#    - Last 30 days of real trade data
#    - Backtest comparison of current vs optimised settings
#    - Live market conditions and news sentiment
#
#  Every proposal — from this monthly review, the weekly market study, and
#  regime changes — goes through approval_gate.py. What happens next depends
#  on AUTO_APPLY_ENABLED below:
#    AUTO_APPLY_ENABLED = False (default): every proposal waits on your Y/N
#    AUTO_APPLY_ENABLED = True: small changes (see AUTO_APPLY_* below) apply
#      immediately and you're notified after the fact; anything bigger, or
#      anything touching coins/wallets/exchanges, still waits on your Y/N
#  Always backs up config.py before any auto-applied change.
#  Full audit log saved in logs/strategy_updates/ and logs/approval_history.json
# ══════════════════════════════════════════════════════════════════════════════

STRATEGY_AUTO_UPDATE          = True   # Enable monthly self-improvement
STRATEGY_UPDATE_MIN_CONFIDENCE = 70    # Only apply if AI is >= 70% confident
                                       # Raise to 85 for more conservative updates

# ── Quarterly deep walk-forward validation ─────────────────────────────────
# Every 3rd monthly review (Mar/Jun/Sep/Dec) additionally runs a full
# multi-window rolling walk-forward optimization — tests parameter sets
# across 4 consecutive unseen periods instead of just one train/test split.
# This is more rigorous but takes longer and needs 270+ days of history.
# A walk-forward result only overrides the regular monthly suggestion if
# it held up in at least 75% of the windows tested — a parameter set that
# only worked in 1 of 4 periods isn't more trustworthy, just overfit
# differently.
QUARTERLY_WALKFORWARD_ENABLED = True

# ══════════════════════════════════════════════════════════════════════════════
#  WATCHDOG — independent health-monitoring process
#  Run alongside the bot with: python watchdog.py (or WATCHDOG.bat on Windows)
#  This is a SEPARATE process from bot.py — it can detect and alert on a
#  frozen/crashed bot even when the bot's own threads (including its own
#  Telegram alerts) are unresponsive.
# ══════════════════════════════════════════════════════════════════════════════

WATCHDOG_ENABLED               = True
WATCHDOG_CHECK_INTERVAL_SECS   = 60    # how often the watchdog checks liveness
LIVENESS_STALE_MINUTES         = 5     # bot considered frozen if no ping in this long
                                        # (bot pings every 2 min, so 5 min = ~2 missed pings)
WATCHDOG_AUTO_RESTART          = False # if True, watchdog relaunches bot.py on failure
                                        # OFF by default — restarting a broken bot
                                        # automatically can mask a real problem;
                                        # turn on only once you trust the failure mode
WATCHDOG_MAX_RESTARTS_PER_DAY  = 3     # circuit breaker — stops auto-restart looping
                                        # forever on a bot that's persistently broken

# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-UPDATE — checks your GitHub repo for new commits
#  Requires the bot to be running from a `git clone` (not a standalone
#  .zip extract) so there's a remote to check against. See auto_updater.py
#  for the full mechanism and README.md for setup.
# ══════════════════════════════════════════════════════════════════════════════

AUTO_UPDATE_ENABLED             = True    # ON by default — checking for updates
                                           # is always safe; see AUTO_UPDATE_MODE
                                           # below for what happens once one is found
AUTO_UPDATE_CHECK_INTERVAL_SECS = 3600    # check once per hour
AUTO_UPDATE_REMOTE              = "origin"
AUTO_UPDATE_BRANCH              = "main"

# What happens when an update IS found. Three options:
#
#   "notify_only" (DEFAULT — recommended for everyone, including multi-user
#   distribution): never pulls automatically, in either paper or live mode.
#   Sends a Telegram message saying an update is available, and that's it —
#   nothing changes on disk and nothing restarts until the person running
#   the bot deliberately sends /update via Telegram, or runs `git pull`
#   themselves, whenever is convenient for them. This is the only mode where
#   a push to your repo can NEVER silently take over someone else's running
#   bot — every update requires a deliberate action by the person it affects.
#
#   "require_approval": sends a Y/N approval request through the same
#   mechanism as monthly strategy reviews (approval_gate.py) and pulls only
#   after an explicit Y reply. Functionally similar to notify_only but uses
#   the formal approval-gate flow/audit log instead of the /update command.
#
#   "auto_apply": pulls immediately the moment an update is found, in BOTH
#   paper and live mode, with no review window and no approval step —
#   notifies only AFTER the pull, then restarts. This is the highest-risk
#   option: a single push from you takes effect on every running bot with
#   this mode set, including anyone trading real money, within
#   AUTO_UPDATE_CHECK_INTERVAL_SECS. Only use this for a bot you alone run.
AUTO_UPDATE_MODE = "notify_only"

# ── Auto-apply for small, low-risk changes ─────────────────────────────────
# OFF by default. When ON, a proposal auto-applies without waiting for your
# Y/N reply ONLY if every changed parameter is a plain numeric setting that
# moves by less than AUTO_APPLY_MAX_CHANGE_PCT. You're still notified after
# the fact — this isn't silent, it just doesn't make you wait for a tiny nudge.
#
# Regardless of this setting, these ALWAYS require your explicit approval:
#   - Any single parameter moving more than AUTO_APPLY_REQUIRE_APPROVAL_PCT
#   - Anything touching coin lists, wallets, or exchange enable/disable flags
#   - Any non-numeric or unrecognised setting
AUTO_APPLY_ENABLED               = False
AUTO_APPLY_MAX_CHANGE_PCT        = 0.05   # <=5% relative change auto-applies
AUTO_APPLY_REQUIRE_APPROVAL_PCT  = 0.15   # >=15% relative change always asks first

# ══════════════════════════════════════════════════════════════════════════════
#  DUAL POOL — 80% Normal / 20% Aggressive
#  The trading pool is split into two sub-pools:
#
#  DEFAULT BEHAVIOUR: 100% SAFE MODE, no split.
#  Every coin trades with SAFE settings (18% take-profit, 4% stop-loss)
#  until /aggressive is sent via Telegram.
#
#  /aggressive splits the active coin list 50/50:
#    Half the coins trade SAFE  (18% TP / 4% SL)
#    Half the coins trade AGGRESSIVE (25% TP / 10% SL)
#  This split is PERMANENT until /safe is sent, which reverts to
#  100% safe mode with no split.
#
#  Example on $100 total pool, after /aggressive:
#    Safe half:       $50 — trades with RSI 35/65, SL 4%, TP 18%
#    Aggressive half: $50 — trades with RSI 42/58, SL 10%, TP 25%
#
#  Both halves share the same coin discovery/news ranking — the split
#  only affects which risk settings each coin trades under.
# ══════════════════════════════════════════════════════════════════════════════

DUAL_POOL_ENABLED      = False   # OFF by default — 100% safe mode, no split.
                                  # /aggressive sets this True (and the 50/50 ratio).
                                  # /safe sets this back to False.
AGGRESSIVE_POOL_PCT    = 0.0    # Only used when DUAL_POOL_ENABLED=True — 50/50 split

# Safe pool settings — these are the ACTIVE settings whenever
# DUAL_POOL_ENABLED is False (the default), and also the "safe half"
# whenever /aggressive is active.
NORMAL_RSI_BUY         = 25
NORMAL_RSI_SELL        = 55
NORMAL_STOP_LOSS       = 0.04    # 4% — measured from peak, see risk_manager.py
NORMAL_TAKE_PROFIT     = 0.1    # 25% — from entry
NORMAL_TRAILING_STOP   = 0.03    # 3%
NORMAL_MAX_HOLD_HOURS  = 168

# Aggressive pool settings — only active for the "aggressive half"
# of coins after /aggressive has been sent.
AGGRESSIVE_RSI_BUY     = 42     # fires more often (wider band)
AGGRESSIVE_RSI_SELL    = 58     # exits sooner on overbought
AGGRESSIVE_STOP_LOSS   = 0.10   # 10% — matches global ceiling
AGGRESSIVE_TAKE_PROFIT = 0.25   # 25% — matches global target
AGGRESSIVE_TRAILING_STOP = 0.04 # 4% trailing
AGGRESSIVE_MAX_HOLD_HOURS = 24  # shorter hold — take profits faster

# Auto-adapted to BEAR_STRONG regime (2026-06-26 15:22)

# Auto-adapted to SIDEWAYS regime (2026-06-26 15:53)

# Auto-adapted to BEAR_STRONG regime (2026-06-27 01:44)

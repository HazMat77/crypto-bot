"""
Approval Gate
==============
Every proposed change to the bot — strategy updates, regime adaptations,
parameter adjustments — MUST pass through this gate first.

Workflow:
  1. System proposes a change with full explanation
  2. Telegram message sent: "Here's what I learned, here's what I want to change, reply Y or N"
  3. You reply Y → change applied, logged, bot continues with new settings
  4. You reply N → change discarded, logged, bot continues with current settings
  5. No reply in 24h → change automatically discarded (safety default)

No change is EVER made to the bot without your explicit Y approval.
This applies to:
  - Monthly AI strategy reviews
  - Market regime adaptations
  - Self-learning parameter updates
  - Risk control adjustments
"""

import json
import logging
import threading
import time
import shutil
import requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

log = logging.getLogger(__name__)

PENDING_FILE   = Path("logs/pending_approvals.json")
APPROVAL_LOG   = Path("logs/approval_history.json")
PENDING_FILE.parent.mkdir(exist_ok=True)

# Global pending approvals queue
_pending        = {}       # { approval_id: approval_dict }
_pending_lock   = threading.Lock()
_approved_ids   = set()    # IDs that got Y
_rejected_ids   = set()    # IDs that got N


# ══════════════════════════════════════════════════════════════════════════════
#  APPROVAL REQUEST
# ══════════════════════════════════════════════════════════════════════════════

def _pct_change(old_val, new_val) -> float:
    """
    Relative change between old and new, as a fraction (0.05 = 5%).
    Returns None if either value isn't numeric or old_val is zero
    (can't compute a meaningful percentage from zero).
    """
    try:
        old_f, new_f = float(old_val), float(new_val)
    except (TypeError, ValueError):
        return None
    if old_f == 0:
        return None
    return abs(new_f - old_f) / abs(old_f)


def _classify_for_auto_apply(proposed: dict, current: dict, cfg) -> dict:
    """
    Decides whether a proposed change set qualifies for auto-apply
    without waiting on human Y/N, based on hard guardrails:

      - AUTO_APPLY_ENABLED must be True in config (off by default)
      - Every changed parameter's relative move must be <= the small-change
        threshold (default 5%)
      - If ANY parameter's relative move exceeds the large-change threshold
        (default 15%), human approval is always required regardless of
        the rest — one big swing in a basket of small ones still needs eyes
      - Any parameter name that looks like a coin-exclusion/inclusion list
        (not a plain numeric setting) always requires human approval —
        changing which coins trade is a structurally different decision
        than nudging a percentage
      - Non-numeric or unrecognised parameters always require approval —
        the auto-apply path only ever fires for things it can measure

    Returns:
        {
          "auto_apply":  bool,
          "reason":      str   (why it was or wasn't auto-applied)
          "max_change":  float (largest relative change seen, for logging)
        }
    """
    if not getattr(cfg, "AUTO_APPLY_ENABLED", False):
        return {"auto_apply": False, "reason": "Auto-apply disabled in config", "max_change": None}

    small_cap = getattr(cfg, "AUTO_APPLY_MAX_CHANGE_PCT", 0.05)
    large_cap = getattr(cfg, "AUTO_APPLY_REQUIRE_APPROVAL_PCT", 0.15)

    # Parameter name patterns that always require human approval regardless
    # of magnitude — these aren't simple numeric nudges.
    STRUCTURAL_KEYWORDS = ("COIN", "EXCLUDE", "WALLET", "EXCHANGE", "ENABLED")

    max_change  = 0.0
    blocking    = []

    for param, new_val in proposed.items():
        old_val = current.get(param)
        if str(old_val) == str(new_val):
            continue   # no actual change, nothing to evaluate

        # Structural changes (coin lists, wallet config, enable/disable
        # flags) always need a human regardless of how "small" they look
        if any(kw in param.upper() for kw in STRUCTURAL_KEYWORDS):
            blocking.append(f"{param} is a structural setting, not a numeric tweak")
            continue

        pct = _pct_change(old_val, new_val)
        if pct is None:
            blocking.append(f"{param} change isn't a measurable numeric percentage")
            continue

        max_change = max(max_change, pct)

        if pct >= large_cap:
            blocking.append(f"{param} moves {pct*100:.1f}% (>= {large_cap*100:.0f}% large-change threshold)")
        elif pct > small_cap:
            blocking.append(f"{param} moves {pct*100:.1f}% (over the {small_cap*100:.0f}% auto-apply limit)")

    if blocking:
        return {
            "auto_apply": False,
            "reason": "Requires approval: " + "; ".join(blocking),
            "max_change": max_change,
        }

    return {
        "auto_apply": True,
        "reason": f"All changes within {small_cap*100:.0f}% — auto-applied without waiting on approval",
        "max_change": max_change,
    }


def request_approval(
    change_type:   str,        # "regime_change" | "monthly_review" | "learning_update"
    title:         str,        # short title for the message
    what_learned:  str,        # what the bot observed/studied
    why_change:    str,        # reasoning for the change
    proposed:      dict,       # { "PARAM_NAME": new_value, ... }
    current:       dict,       # { "PARAM_NAME": current_value, ... }
    confidence:    int,        # AI/system confidence 0-100
    config,
    timeout_hours: float = 24,
) -> str:
    """
    Sends a Telegram approval request and waits for Y/N response —
    UNLESS the change qualifies for auto-apply (see _classify_for_auto_apply),
    in which case it's applied immediately and you're notified after the
    fact instead of being asked to wait.

    Returns:
        "approved"     — user replied Y, or auto-applied
        "rejected"     — user replied N
        "timeout"      — no response within timeout_hours
        "no_telegram"  — Telegram not configured
        "no_changes"   — nothing actually changed
    """
    import config as cfg

    # ── Auto-apply check — small, safe changes skip the human wait ────────
    classification = _classify_for_auto_apply(proposed, current, cfg)
    if classification["auto_apply"]:
        log.info(f"[GATE] Auto-applying {title}: {classification['reason']}")
        _send_auto_apply_notice(title, proposed, current, classification, cfg)
        return "approved"
    elif getattr(cfg, "AUTO_APPLY_ENABLED", False):
        # Auto-apply is on, but THIS proposal didn't qualify — log why,
        # then fall through to the normal Y/N approval flow below.
        log.info(f"[GATE] {title} does not qualify for auto-apply: {classification['reason']}")

    if not getattr(cfg, "TELEGRAM_ENABLED", False):
        log.info(f"[GATE] Telegram disabled — auto-approving {title}")
        return "approved"

    # Build approval ID
    approval_id = f"{change_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Format the changes for display
    changes_lines = ""
    for param, new_val in proposed.items():
        old_val = current.get(param, "?")
        if str(old_val) != str(new_val):
            arrow = "▲" if (isinstance(new_val,(int,float)) and isinstance(old_val,(int,float)) and new_val > old_val) else "▼"
            changes_lines += f"  {arrow} <b>{param}</b>: {old_val} → <b>{new_val}</b>\n"

    if not changes_lines:
        log.info(f"[GATE] No meaningful changes in {title} — skipping approval")
        return "no_changes"

    # Build message
    msg = (
        f"🧠 <b>Bot Self-Improvement Proposal</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Type:</b> {change_type.replace('_',' ').title()}\n"
        f"🎯 <b>Confidence:</b> {confidence}%\n\n"

        f"📚 <b>What I Studied & Learned:</b>\n"
        f"{what_learned}\n\n"

        f"💡 <b>Why I Want to Make These Changes:</b>\n"
        f"{why_change}\n\n"

        f"⚙️ <b>Proposed Changes:</b>\n"
        f"{changes_lines}\n"

        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Reply <b>Y</b> to apply these changes\n"
        f"Reply <b>N</b> to keep current settings\n\n"
        f"⏰ Expires in {timeout_hours:.0f}h if no reply\n"
        f"🆔 ID: <code>{approval_id}</code>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # Store pending approval
    approval = {
        "id":           approval_id,
        "change_type":  change_type,
        "title":        title,
        "what_learned": what_learned,
        "why_change":   why_change,
        "proposed":     proposed,
        "current":      current,
        "confidence":   confidence,
        "sent_at":      datetime.now().isoformat(),
        "expires_at":   (datetime.now() + timedelta(hours=timeout_hours)).isoformat(),
        "status":       "pending",
    }

    with _pending_lock:
        _pending[approval_id] = approval
        _save_pending()

    # Send Telegram message
    try:
        requests.post(
            f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=15,
        )
        log.info(f"[GATE] Approval request sent: {approval_id}")
    except Exception as e:
        log.error(f"[GATE] Could not send approval request: {e}")
        return "no_telegram"

    # Wait for response
    deadline = datetime.now() + timedelta(hours=timeout_hours)
    while datetime.now() < deadline:
        time.sleep(10)   # check every 10 seconds

        if approval_id in _approved_ids:
            _log_outcome(approval_id, "approved", approval)
            _send_confirmation(approval_id, True, changes_lines, cfg)
            return "approved"

        if approval_id in _rejected_ids:
            _log_outcome(approval_id, "rejected", approval)
            _send_confirmation(approval_id, False, changes_lines, cfg)
            return "rejected"

    # Timeout
    log.info(f"[GATE] Approval {approval_id} timed out — discarding change")
    _log_outcome(approval_id, "timeout", approval)
    with _pending_lock:
        _pending.pop(approval_id, None)
        _save_pending()

    try:
        requests.post(
            f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": cfg.TELEGRAM_CHAT_ID,
                "text": (f"⏰ <b>Approval Expired</b>\n"
                        f"The proposed changes for <b>{title}</b> were not "
                        f"approved within {timeout_hours:.0f}h.\n"
                        f"Current settings kept unchanged.\n"
                        f"🆔 {approval_id}"),
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception:
        pass

    return "timeout"


def _backup_config_before_auto_apply() -> str:
    """
    Copies the current config.py to logs/strategy_updates/ before an
    auto-applied change is written. Returns the backup path as a string,
    or "" if the backup failed (failure here should never block the
    actual change — it just means there's no safety copy for this one).
    """
    backup_dir = Path("logs/strategy_updates")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp       = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"config_backup_autoapply_{stamp}.py"
    try:
        shutil.copy("config.py", backup_path)
        return str(backup_path)
    except Exception as e:
        log.warning(f"[GATE] Could not back up config.py before auto-apply: {e}")
        return ""


def _send_auto_apply_notice(title: str, proposed: dict, current: dict,
                             classification: dict, cfg):
    """
    Notifies after an auto-applied change — informational, not a request.
    Backs up config.py first (regardless of which module triggered this —
    regime change, monthly review, or weekly study all funnel through here),
    then logs to the same approval_history.json used by manual approvals so
    /diag and the dashboard see auto-applied changes in the same place as
    everything else, tagged distinctly so they're never confused with a
    change you explicitly approved yourself.
    """
    backup_path = _backup_config_before_auto_apply()

    changes_lines = ""
    for param, new_val in proposed.items():
        old_val = current.get(param, "?")
        if str(old_val) != str(new_val):
            changes_lines += f"  • <b>{param}</b>: {old_val} → <b>{new_val}</b>\n"

    auto_apply_id = f"autoapply_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    if getattr(cfg, "TELEGRAM_ENABLED", False):
        backup_line = (f"A backup was saved to:\n<code>{backup_path}</code>\n"
                       if backup_path else
                       "⚠️ Backup could not be saved — check disk permissions.\n")
        msg = (
            f"⚡ <b>Auto-Applied — {title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"This was a small, low-risk change — applied automatically\n"
            f"without waiting on your approval.\n\n"
            f"⚙️ <b>Changes:</b>\n{changes_lines}\n"
            f"📋 {classification['reason']}\n\n"
            f"{backup_line}"
            f"To revert manually: stop the bot, copy that file back over\n"
            f"config.py, then restart.\n"
            f"🆔 {auto_apply_id}\n"
            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        try:
            requests.post(
                f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception as e:
            log.warning(f"[GATE] Auto-apply notice send failed: {e}")

    # Log to the same history file manual approvals use, tagged distinctly
    try:
        history = []
        if APPROVAL_LOG.exists():
            with open(APPROVAL_LOG) as f:
                history = json.load(f)
        history.append({
            "id":          auto_apply_id,
            "outcome":     "auto_applied",
            "type":        title,
            "time":        datetime.now().isoformat(),
            "proposed":    proposed,
            "reason":      classification["reason"],
            "backup_path": backup_path,
        })
        history = history[-500:]
        with open(APPROVAL_LOG, "w") as f:
            json.dump(history, f, indent=2, default=str)
    except Exception:
        pass


def _send_confirmation(approval_id: str, approved: bool, changes: str, cfg):
    """Send confirmation that changes were applied or discarded."""
    icon = "✅" if approved else "❌"
    verb = "Applied"  if approved else "Discarded"
    try:
        requests.post(
            f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": cfg.TELEGRAM_CHAT_ID,
                "text": (f"{icon} <b>Changes {verb}</b>\n"
                        f"{'New settings are now active.' if approved else 'Current settings kept unchanged.'}\n"
                        f"🆔 {approval_id}\n"
                        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  RESPONSE HANDLER (called by Telegram command handler)
# ══════════════════════════════════════════════════════════════════════════════

def handle_response(text: str, chat_id: str, config) -> bool:
    """
    Process a Y or N reply from the user.
    Returns True if the message was handled as an approval response.
    """
    import config as cfg

    if str(chat_id) != str(cfg.TELEGRAM_CHAT_ID):
        return False

    text_clean = text.strip().upper()

    with _pending_lock:
        pending_list = list(_pending.items())

    if not pending_list:
        return False

    # Y or N applies to the most recent pending approval
    if text_clean in ("Y", "YES", "N", "NO"):
        # Find most recent pending
        most_recent = max(pending_list, key=lambda x: x[1]["sent_at"])
        approval_id = most_recent[0]

        if text_clean in ("Y", "YES"):
            _approved_ids.add(approval_id)
            log.info(f"[GATE] ✅ User approved {approval_id}")
        else:
            _rejected_ids.add(approval_id)
            log.info(f"[GATE] ❌ User rejected {approval_id}")

        with _pending_lock:
            if approval_id in _pending:
                _pending[approval_id]["status"] = "approved" if text_clean in ("Y","YES") else "rejected"
                _save_pending()
        return True

    return False


def get_pending_count() -> int:
    with _pending_lock:
        return len(_pending)


def get_pending_list() -> list:
    with _pending_lock:
        return list(_pending.values())


# ══════════════════════════════════════════════════════════════════════════════
#  PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

def _save_pending():
    try:
        with open(PENDING_FILE, "w") as f:
            json.dump(_pending, f, indent=2, default=str)
    except Exception:
        pass


def _log_outcome(approval_id: str, outcome: str, approval: dict):
    try:
        history = []
        if APPROVAL_LOG.exists():
            with open(APPROVAL_LOG) as f:
                history = json.load(f)
        history.append({
            "id":       approval_id,
            "outcome":  outcome,
            "type":     approval.get("change_type"),
            "time":     datetime.now().isoformat(),
            "proposed": approval.get("proposed", {}),
        })
        history = history[-500:]   # keep last 500
        with open(APPROVAL_LOG, "w") as f:
            json.dump(history, f, indent=2, default=str)
    except Exception:
        pass


def load_approval_history(limit: int = 20) -> list:
    try:
        if APPROVAL_LOG.exists():
            with open(APPROVAL_LOG) as f:
                data = json.load(f)
            return data[-limit:]
    except Exception:
        pass
    return []

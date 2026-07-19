"""
Version
========
Single source of truth for the bot's version number. Every other file
that needs to display or compare the version imports it from here —
never hardcode a version string anywhere else.

Bump this on every release you intend to push to GitHub. The auto-update
system (auto_updater.py) compares this against the version on the remote
branch to decide whether an update is even available, so this number is
also the literal gate that update logic checks.

Format: MAJOR.MINOR.PATCH (semantic versioning)
  MAJOR — breaking changes to config.py structure, command behaviour, etc.
  MINOR — new features, backwards compatible
  PATCH — bug fixes, no new behaviour
"""

__version__ = "1.2.0"

# Optional human-readable release name/date — shown in /status and /version.
# Update alongside __version__ when you bump it.
RELEASE_DATE = "2026-07-19"

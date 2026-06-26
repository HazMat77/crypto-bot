"""
Retry Utility
==============
Smart exponential backoff retry decorator and helper.
Used across all exchange and API calls to handle:
  - Rate limits (429)
  - Temporary network errors
  - Exchange downtime
  - Timeout errors

Usage:
    from retry_utils import retry, RetryError

    @retry(max_attempts=3, base_delay=2.0)
    def my_api_call():
        return requests.get(url)

    # Or call directly:
    result = retry_call(my_fn, args=(arg1,), max_attempts=3)
"""

import time
import logging
import functools
import requests

log = logging.getLogger(__name__)

# Error messages that indicate rate limiting
RATE_LIMIT_SIGNALS = [
    "429", "too many requests", "rate limit", "ratelimit",
    "throttle", "quota exceeded", "request limit",
]

# Error messages that indicate we should NOT retry (permanent errors)
NO_RETRY_SIGNALS = [
    "invalid api key", "unauthorized", "forbidden", "invalid signature",
    "insufficient balance", "min size", "order size too small",
]


def is_rate_limit(error: Exception) -> bool:
    msg = str(error).lower()
    return any(sig in msg for sig in RATE_LIMIT_SIGNALS)


def is_permanent(error: Exception) -> bool:
    msg = str(error).lower()
    return any(sig in msg for sig in NO_RETRY_SIGNALS)


class RetryError(Exception):
    """Raised when all retry attempts are exhausted."""
    pass


def retry(max_attempts: int = 3, base_delay: float = 2.0,
          max_delay: float = 60.0, backoff_factor: float = 2.0):
    """
    Decorator for automatic retry with exponential backoff.

    Args:
        max_attempts:   Total attempts before giving up
        base_delay:     Initial delay in seconds
        max_delay:      Maximum delay cap in seconds
        backoff_factor: Multiply delay by this each attempt

    Rate limit errors get longer delays (base_delay * 5).
    Permanent errors (bad API key etc.) raise immediately.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_error = None
            delay      = base_delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)

                except Exception as e:
                    last_error = e

                    # Permanent error — don't retry
                    if is_permanent(e):
                        log.error(f"[RETRY] Permanent error in {fn.__name__}: {e}")
                        raise

                    # Rate limit — use longer delay
                    if is_rate_limit(e):
                        wait = min(base_delay * 5 * attempt, max_delay)
                        log.warning(f"[RETRY] Rate limited in {fn.__name__} "
                                   f"(attempt {attempt}/{max_attempts}) — waiting {wait:.0f}s")
                    else:
                        wait = min(delay, max_delay)
                        log.warning(f"[RETRY] {fn.__name__} failed "
                                   f"(attempt {attempt}/{max_attempts}): {e} — retrying in {wait:.1f}s")

                    if attempt < max_attempts:
                        time.sleep(wait)
                        delay *= backoff_factor

            raise RetryError(f"{fn.__name__} failed after {max_attempts} attempts: {last_error}")

        return wrapper
    return decorator


def retry_call(fn, args=(), kwargs=None, max_attempts=3,
               base_delay=2.0, max_delay=60.0, default=None):
    """
    Call a function with retry logic. Returns default on failure instead of raising.
    Useful for non-critical calls where failure is acceptable.
    """
    if kwargs is None:
        kwargs = {}

    @retry(max_attempts=max_attempts, base_delay=base_delay, max_delay=max_delay)
    def _call():
        return fn(*args, **kwargs)

    try:
        return _call()
    except (RetryError, Exception) as e:
        log.warning(f"[RETRY] {fn.__name__} exhausted retries, returning default: {e}")
        return default


def fetch_with_retry(url: str, method: str = "GET",
                     headers: dict = None, params: dict = None,
                     json_data: dict = None, timeout: int = 15,
                     max_attempts: int = 3) -> requests.Response:
    """
    Fetch a URL with automatic retry and backoff.
    Returns Response object or raises RetryError.
    """
    @retry(max_attempts=max_attempts, base_delay=2.0)
    def _fetch():
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        else:
            resp = requests.post(url, headers=headers, json=json_data, timeout=timeout)

        # Treat 429 as an exception so retry logic kicks in
        if resp.status_code == 429:
            raise Exception(f"429 Too Many Requests — {url}")
        if resp.status_code >= 500:
            raise Exception(f"Server error {resp.status_code} — {url}")

        return resp

    return _fetch()

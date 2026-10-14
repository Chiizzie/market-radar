"""Bounded retries for idempotent reads only; never retry ambiguous message sends."""
import time
import requests


def retry_read(call, valid=lambda value: True, attempts=2):
    for attempt in range(1, attempts + 1):
        try:
            value = call()
            if not valid(value):
                raise ValueError('Empty provider response')
            return value, attempt
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(min(attempt, 2))


def http_get(url, **kwargs):
    """Retry one transient read failure; do not hammer forbidden/bad endpoints."""
    for attempt in range(2):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if attempt or (status is not None and status not in {429, 500, 502, 503, 504}):
                raise
            delay = 1
            if status == 429:
                try:
                    delay = float(exc.response.headers.get('Retry-After', 1))
                except (ValueError, TypeError):
                    raise
                if not 0 <= delay <= 5:
                    raise
            time.sleep(delay)

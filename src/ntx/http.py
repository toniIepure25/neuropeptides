"""Minimal HTTP client with an on-disk response cache and per-host rate limits.

The cache is keyed by a hash of the request URL, which makes ingestion
idempotent and lets the demo and tests run fully offline from recorded data.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "ntx-evidence/0.2 (research prototype; contact: local-user)"
DEFAULT_TIMEOUT = 30
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_HOST_LAST_CALL: dict[str, float] = {}
_HOST_MIN_INTERVAL = {
    "www.ebi.ac.uk": 0.12,
    "eutils.ncbi.nlm.nih.gov": 0.40,
    "api.crossref.org": 0.20,
}


class OfflineError(RuntimeError):
    pass


def cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _throttle(host: str) -> None:
    interval = _HOST_MIN_INTERVAL.get(host, 0.1)
    last = _HOST_LAST_CALL.get(host)
    now = time.monotonic()
    if last is not None:
        wait = interval - (now - last)
        if wait > 0:
            time.sleep(wait)
    _HOST_LAST_CALL[host] = time.monotonic()


def get_json(
    url: str,
    cache_dir: Path,
    offline: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 3,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> Any:
    """Return parsed JSON for *url*, using the cache when present.

    With ``offline=True`` a cache miss raises OfflineError instead of touching
    the network, which is what tests and the demo rely on.

    Only HTTPS is accepted, and responses larger than ``max_bytes`` are rejected
    before parsing so an untrusted endpoint cannot exhaust memory.
    """
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("only https URLs are allowed: " + url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key(url)
    cached = cache_dir / (key + ".json")
    if cached.exists():
        with open(cached, encoding="utf-8") as fh:
            return json.load(fh)
    if offline:
        raise OfflineError("no cached response for " + url)

    host = urllib.parse.urlparse(url).netloc
    last_error: Exception | None = None
    for attempt in range(retries):
        _throttle(host)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read(max_bytes + 1)
            if len(raw_bytes) > max_bytes:
                raise ValueError("response exceeds %d bytes: %s" % (max_bytes, url))
            raw = raw_bytes.decode("utf-8", errors="replace")
            payload = json.loads(raw)
            temporary = cached.with_suffix(".json.tmp")
            with open(temporary, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(temporary, cached)
            return payload
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError("request failed after %d attempts: %s (%s)" % (retries, url, last_error))


def get_text(
    url: str,
    cache_dir: Path,
    offline: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 3,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> str:
    """Return UTF-8 text for *url*, using an on-disk cache when present.

    This is the text counterpart of :func:`get_json`, used for JATS full text.
    It uses a separate ``.txt`` cache entry so XML and JSON responses for the
    same URL cannot collide.
    """
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("only https URLs are allowed: " + url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key(url)
    cached = cache_dir / (key + ".txt")
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    if offline:
        raise OfflineError("no cached response for " + url)

    host = urllib.parse.urlparse(url).netloc
    last_error: Exception | None = None
    for attempt in range(retries):
        _throttle(host)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read(max_bytes + 1)
            if len(raw_bytes) > max_bytes:
                raise ValueError("response exceeds %d bytes: %s" % (max_bytes, url))
            text = raw_bytes.decode("utf-8", errors="replace")
            temporary = cached.with_suffix(".txt.tmp")
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, cached)
            return text
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = exc
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError("request failed after %d attempts: %s (%s)" % (retries, url, last_error))

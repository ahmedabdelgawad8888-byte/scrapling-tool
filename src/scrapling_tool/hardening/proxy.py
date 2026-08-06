"""Round-robin proxy rotation through a user-supplied pool.

This is *your* pool — credentials you bought, or free trials you control.
We don't ship free proxies (those are a reliability and security hazard).

Usage::

    rotator = ProxyRotator([
        "http://user:pass@residential-1.example.com:8000",
        "socks5://user:pass@residential-2.example.com:1080",
    ])
    proxies = rotator.next()  # {"http://": ..., "https://": ...}

The rotator tracks per-proxy error counts and temporarily removes
proxies whose error rate exceeds a threshold. It re-enables them after a
cooldown so transient blips don't permanently blackhole a node.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterable


def _parse(proxy: str) -> dict[str, str]:
    """Translate ``socks5://u:p@h:p`` to ``{"http://": ..., "https://": ...}``.

    If the scheme is ``socks5`` we route both schemes through the SOCKS URL
    because httpx does not split them.
    """
    if proxy.startswith("socks5://") or proxy.startswith("socks5h://"):
        url = proxy.replace("socks5h://", "socks5://", 1)
        return {"http://": url, "https://": url}
    return {"http://": proxy, "https://": proxy}


class ProxyRotator:
    """Thread-safe round-robin proxy pool with self-healing cooldowns."""

    def __init__(
        self,
        proxies: Iterable[str],
        *,
        cooldown_seconds: float = 60.0,
        error_threshold: int = 5,
    ) -> None:
        self._proxies = list(proxies)
        if not self._proxies:
            self._enabled: list[bool] = []
        else:
            self._enabled = [True] * len(self._proxies)
        self._cooldown = cooldown_seconds
        self._threshold = error_threshold
        self._errors: dict[int, int] = defaultdict(int)
        self._last_disabled: dict[int, float] = {}
        self._index = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._proxies)

    def next(self) -> dict[str, str] | None:
        """Return the next enabled proxy as an httpx proxies mapping, or None."""
        with self._lock:
            self._recover()
            n = len(self._proxies)
            for _ in range(n):
                if self._enabled[self._index]:
                    url = self._proxies[self._index]
                    self._index = (self._index + 1) % n
                    return _parse(url)
                self._index = (self._index + 1) % n
            return None

    def report_error(self, proxy_url: str) -> None:
        """Tell the rotator that the last request through ``proxy_url`` failed.

        After ``error_threshold`` consecutive errors the proxy is taken out
        of rotation for ``cooldown_seconds``.
        """
        with self._lock:
            try:
                idx = self._proxies.index(proxy_url)
            except ValueError:
                return
            self._errors[idx] += 1
            if self._errors[idx] >= self._threshold and self._enabled[idx]:
                self._enabled[idx] = False
                self._last_disabled[idx] = time.time()

    def report_success(self, proxy_url: str) -> None:
        """Reset the error counter for ``proxy_url``."""
        with self._lock:
            try:
                idx = self._proxies.index(proxy_url)
            except ValueError:
                return
            self._errors[idx] = 0

    def _recover(self) -> None:
        now = time.time()
        for idx, when in list(self._last_disabled.items()):
            if now - when >= self._cooldown:
                self._enabled[idx] = True
                self._errors[idx] = 0
                del self._last_disabled[idx]

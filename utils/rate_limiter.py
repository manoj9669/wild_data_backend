import asyncio
import time
from typing import Dict

class RateLimiter:
    """Simple async rate limiter — ensures minimum gap between requests per domain."""

    def __init__(self):
        self._last_call: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def wait(self, domain: str, min_gap_seconds: float = 1.1):
        lock = self._get_lock(domain)
        async with lock:
            now = time.monotonic()
            last = self._last_call.get(domain, 0)
            gap = now - last
            if gap < min_gap_seconds:
                await asyncio.sleep(min_gap_seconds - gap)
            self._last_call[domain] = time.monotonic()

# Global singleton
rate_limiter = RateLimiter()

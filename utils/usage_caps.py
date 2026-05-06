import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (ValueError, TypeError):
        return default


SAFE_MODE = os.getenv("FREE_TIER_SAFE_MODE", "1").lower() not in {"0", "false", "no"}


def _normalize_limit(val: Optional[int], allow_zero: bool = False) -> Optional[int]:
    if val is None:
        return None
    if val < 0:
        return None
    if val == 0 and not allow_zero:
        return None
    return val


def get_limits(provider: str) -> Optional[Dict[str, Optional[int]]]:
    """
    Return usage limits for a provider.
    None means no cap (or safe mode disabled).
    """
    if not SAFE_MODE:
        return None

    if provider == "foursquare":
        day = _normalize_limit(_int_env("FOURSQUARE_DAILY_CALL_LIMIT", 400), allow_zero=True)
        month = _normalize_limit(_int_env("FOURSQUARE_MONTHLY_CALL_LIMIT", 9000), allow_zero=False)
    elif provider == "geoapify":
        day = _normalize_limit(_int_env("GEOAPIFY_DAILY_CREDITS_LIMIT", 2000), allow_zero=True)
        month = _normalize_limit(_int_env("GEOAPIFY_MONTHLY_CREDITS_LIMIT", 0), allow_zero=False)
    elif provider == "opentripmap":
        day = _normalize_limit(_int_env("OTM_DAILY_CALL_LIMIT", 4500), allow_zero=True)
        month = _normalize_limit(_int_env("OTM_MONTHLY_CALL_LIMIT", 0), allow_zero=False)
    elif provider == "here":
        day = _normalize_limit(_int_env("HERE_DAILY_CALL_LIMIT", 8000), allow_zero=True)
        month = _normalize_limit(_int_env("HERE_MONTHLY_CALL_LIMIT", 240000), allow_zero=False)
    elif provider == "opentripmap":
        day = _normalize_limit(_int_env("OTM_DAILY_CALL_LIMIT", 4000), allow_zero=True)
        month = _normalize_limit(_int_env("OTM_MONTHLY_CALL_LIMIT", 0), allow_zero=False)
    elif provider == "here":
        day = _normalize_limit(_int_env("HERE_DAILY_CALL_LIMIT", 8000), allow_zero=True)
        month = _normalize_limit(_int_env("HERE_MONTHLY_CALL_LIMIT", 240000), allow_zero=False)
    else:
        return None

    return {"day": day, "month": month}


class UsageCaps:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = asyncio.Lock()

    def _load(self) -> Dict:
        try:
            if self.path.exists():
                return json.loads(self.path.read_text() or "{}")
        except (OSError, ValueError):
            pass
        return {}

    def _save(self, data: Dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data))
        except (OSError, IOError):
            # Best effort — never crash the pipeline for a stats write
            pass

    def _ensure_periods(self, rec: Dict) -> None:
        today = _utc_today()
        month = _utc_month()
        if rec.get("day") != today:
            rec["day"] = today
            rec["day_used"] = 0
        if rec.get("month") != month:
            rec["month"] = month
            rec["month_used"] = 0

    async def spend(self, provider: str, cost: int = 1) -> bool:
        """
        Atomically spend `cost` from provider caps.
        Returns False if caps would be exceeded.
        """
        if cost <= 0:
            return True

        limits = get_limits(provider)
        if limits is None:
            return True

        async with self._lock:
            data = self._load()
            rec = data.get(provider, {})
            self._ensure_periods(rec)

            day_limit = limits.get("day")
            month_limit = limits.get("month")
            day_used = int(rec.get("day_used", 0))
            month_used = int(rec.get("month_used", 0))

            if day_limit is not None and day_used + cost > day_limit:
                return False
            if month_limit is not None and month_used + cost > month_limit:
                return False

            rec["day_used"] = day_used + cost
            rec["month_used"] = month_used + cost
            data[provider] = rec
            self._save(data)
            return True


_caps_path = os.getenv("USAGE_CAPS_PATH", "/tmp/wilddata_usage_caps.json")
# CWE-22: sanitize path - only allow /tmp/ directory for security
if not os.path.abspath(_caps_path).startswith("/tmp/"):
    _caps_path = "/tmp/wilddata_usage_caps.json"
usage_caps = UsageCaps(_caps_path)

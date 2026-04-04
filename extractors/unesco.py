"""
UNESCO World Heritage Sites — free public API.
No API key required. ~1,199 sites in 168 countries.

API: https://whc.unesco.org/api/sites/?format=json
Paginated: up to 100 per page. Full list downloaded once and cached in memory.

Categories:
  C = Cultural Heritage
  N = Natural Heritage
  M = Mixed (Cultural + Natural)

Data includes: site name, short description, lat/lng, country, region, year inscribed.
"""

import math
import re
import httpx
from typing import Dict, Any, AsyncGenerator, Optional, Tuple, List

UNESCO_API_URL = "https://whc.unesco.org/api/sites/"

# In-memory cache — populated on first call, reused for the process lifetime.
_CACHE: Optional[List[Dict]] = None

_CATEGORY_LABELS = {
    "C": "Cultural Heritage",
    "N": "Natural Heritage",
    "M": "Mixed Heritage",
}

_CATEGORY_TYPE_ID = {
    "C": "cultural",
    "N": "park",
    "M": "historic",
}


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


async def _load_all_sites() -> List[Dict]:
    """Download all UNESCO WH sites (paginated). Cached in memory after first call."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    all_sites: List[Dict] = []
    page = 1

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                resp = await client.get(
                    UNESCO_API_URL,
                    params={"format": "json", "page": page},
                )
                if resp.status_code != 200:
                    print(f"[UNESCO] HTTP {resp.status_code} on page {page}")
                    break
                data = resp.json()
            except Exception as e:
                print(f"[UNESCO] fetch error page {page}: {e}")
                break

            results = data.get("results", [])
            if not results:
                break

            all_sites.extend(results)
            page += 1

            # Stop when we've fetched everything
            total = data.get("count", 0)
            if len(all_sites) >= total:
                break

    print(f"[UNESCO] Loaded {len(all_sites)} World Heritage Sites into cache")
    _CACHE = all_sites
    return all_sites


async def fetch_unesco(
    lat: float,
    lng: float,
    radius_km: float,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Yield UNESCO World Heritage Sites within the given bbox or radius.
    Uses in-memory cache so the full list is only downloaded once per process.
    """
    sites = await _load_all_sites()

    for site in sites:
        try:
            s_lat = float(site.get("latitude") or 0)
            s_lng = float(site.get("longitude") or 0)
        except (TypeError, ValueError):
            continue

        if not s_lat and not s_lng:
            continue

        # Spatial filter
        if bbox:
            south, west, north, east = bbox
            if not (south <= s_lat <= north and west <= s_lng <= east):
                continue
        else:
            if _haversine(lat, lng, s_lat, s_lng) > radius_km:
                continue

        name = (site.get("site") or "").strip()
        if not name:
            continue

        category = (site.get("category") or "C").strip()
        type_label = _CATEGORY_LABELS.get(category, "World Heritage Site")
        type_id = _CATEGORY_TYPE_ID.get(category, "cultural")

        desc = _strip_html(site.get("short_description", ""))[:500]
        site_id = site.get("id_number", "")
        website = f"https://whc.unesco.org/en/list/{site_id}/" if site_id else ""

        year = site.get("date_inscribed", "")
        if year and desc:
            desc = f"Inscribed {year}. {desc}"
        elif year:
            desc = f"UNESCO World Heritage Site, inscribed {year}."

        yield {
            "name":        name,
            "type":        type_label,
            "type_id":     type_id,
            "lat":         round(s_lat, 6),
            "lng":         round(s_lng, 6),
            "elevation":   "",
            "description": desc,
            "wikipedia":   "",
            "website":     website,
            "city":        "",
            "region":      site.get("region_en", ""),
            "country":     site.get("states_name_en", ""),
            "image":       "",
            "osm_id":      "",
            "source":      "UNESCO WHC",
            "confidence":  "High",
        }

"""
HERE Places API (Browse + Discover) — global POI database.
Free tier: 250,000 transactions/month (~8,333/day). Very generous.
Per-extraction cap: MAX_CALLS_PER_RUN (default 30) → ~275 runs/day within free tier.

Covers: outdoor attractions, national parks, viewpoints, historic sites,
        beaches, mountains, camping, cultural landmarks worldwide.

Setup:
1. Go to https://developer.here.com  → create free account
2. Create a new project → REST section → copy API Key
3. Set env var: HERE_API_KEY

API docs: https://www.here.com/docs/bundle/geocoding-and-search-api-developer-guide
"""

import os
import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter

HERE_API_KEY = os.getenv("HERE_API_KEY", "")
HERE_BROWSE_URL = "https://browse.search.hereapi.com/v1/browse"

# Free tier: 250,000 req/month. Cap at 30 per run → safe for ~275 runs/day.
MAX_CALLS_PER_RUN = 30

# WildData feature ID → HERE category IDs (comma-separated)
# Full category list: https://developer.here.com/documentation/geocoding-search-api/dev_guide/topics-places/places-category-system-full.html
HERE_CATEGORIES: Dict[str, str] = {
    "peak":       "700-7400-0000",   # Natural & Geographical (mountains, hills)
    "waterfall":  "700-7400-0000",   # Natural & Geographical
    "waterway":   "700-7400-0000",   # Natural & Geographical (lakes, rivers)
    "cave":       "700-7400-0000",   # Natural & Geographical
    "glacier":    "700-7400-0000",   # Natural & Geographical
    "volcano":    "700-7400-0000",   # Natural & Geographical
    "hot_spring": "700-7400-0000",   # Natural & Geographical
    "beach":      "700-7400-0000",   # Natural & Geographical (beaches)
    "park":       "500-5000-0000",   # Outdoor-Recreation (parks, nature reserves)
    "forest":     "500-5000-0000",   # Outdoor-Recreation
    "viewpoint":  "300-3000-0000",   # Tourist Attraction
    "camp":       "550-5510-0000",   # Camping & Caravan Park
    "hiking":     "500-5000-0000",   # Outdoor-Recreation
    "mtb":        "500-5000-0000",   # Outdoor-Recreation
    "historic":   "800-8200-0000",   # Historical Monument / Heritage
    "cultural":   "800-8000-0000,300-3000-0000",  # Museum + Tourist Attraction
}

# HERE result category name → WildData type label
CATEGORY_TYPE_MAP: Dict[str, str] = {
    "natural-geographical": "Natural Feature",
    "mountain-hill":        "Mountain Peak",
    "body-of-water":        "Water Feature",
    "outdoor-recreation":   "Outdoor Area",
    "park-recreation-area": "Park / Recreation",
    "camping-caravan-park": "Campsite",
    "tourist-attraction":   "Tourist Attraction",
    "historical-monument":  "Historical Monument",
    "museum":               "Museum",
    "gallery":              "Gallery",
    "beach":                "Beach",
}


def _type_from_here_categories(here_cats: List[Dict]) -> str:
    for cat in here_cats:
        cat_id = cat.get("id", "")
        name   = cat.get("name", "").lower().replace(" ", "-").replace("&", "")
        for key, label in CATEGORY_TYPE_MAP.items():
            if key in name or key in cat_id:
                return label
    return "Place of Interest"


def _confidence(contacts: Dict, categories: List) -> str:
    has_web = bool(contacts.get("www") or contacts.get("phone"))
    has_cat = bool(categories)
    if has_web and has_cat:
        return "High"
    if has_cat:
        return "Medium"
    return "Low"


async def fetch_here(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch places from HERE Places Browse API.
    Groups features by category to minimise API calls.
    Respects MAX_CALLS_PER_RUN free-tier cap.
    Yields one result dict at a time.
    """
    if not HERE_API_KEY:
        print("[HERE] HERE_API_KEY not set — skipping")
        return

    radius_m = int(min(radius_km * 1000, 50000))

    # Build bbox or circle 'in' param
    if bbox:
        south, west, north, east = bbox
        location_param = {"in": f"bbox:{west},{south},{east},{north}"}
    else:
        location_param = {
            "at":     f"{lat},{lng}",
            "in":     f"circle:{lng},{lat};r={radius_m}",
        }

    # Deduplicate category strings — multiple feature_ids may share a category
    cat_to_fids: Dict[str, List[str]] = {}
    for fid in feature_ids:
        cat = HERE_CATEGORIES.get(fid)
        if not cat:
            continue
        for c in cat.split(","):
            c = c.strip()
            cat_to_fids.setdefault(c, []).append(fid)

    seen: set = set()
    calls_made = 0

    async with httpx.AsyncClient(timeout=20) as client:
        for cat_id, fids in cat_to_fids.items():
            if calls_made >= MAX_CALLS_PER_RUN:
                print(f"[HERE] Free tier cap reached ({MAX_CALLS_PER_RUN} calls) — stopping")
                return

            try:
                await rate_limiter.wait("browse.search.hereapi.com", 0.2)
                params = {
                    **location_param,
                    "categories": cat_id,
                    "limit":      min(limit, 100),
                    "lang":       "en",
                    "apiKey":     HERE_API_KEY,
                }
                resp = await client.get(HERE_BROWSE_URL, params=params)
                calls_made += 1

                if resp.status_code == 401:
                    print("[HERE] Auth error (401) — check HERE_API_KEY")
                    return
                if resp.status_code == 429:
                    print("[HERE] Rate limit hit (429) — stopping")
                    return
                if resp.status_code == 403:
                    print("[HERE] Quota exceeded (403) — monthly free tier reached")
                    return
                if resp.status_code != 200:
                    print(f"[HERE] HTTP {resp.status_code} for category {cat_id}")
                    continue

                data = resp.json()

            except Exception as e:
                print(f"[HERE] fetch error (cat {cat_id}): {e}")
                continue

            for item in data.get("items", []):
                place_id = item.get("id", "")
                if not place_id or place_id in seen:
                    continue
                seen.add(place_id)

                title = item.get("title", "").strip()
                if not title:
                    continue

                pos = item.get("position", {})
                f_lat = pos.get("lat")
                f_lng = pos.get("lng")
                if not f_lat or not f_lng:
                    continue

                here_cats = item.get("categories", [])
                type_label = _type_from_here_categories(here_cats)

                address = item.get("address", {})
                contacts = item.get("contacts", [{}])[0] if item.get("contacts") else {}
                website = ""
                if contacts.get("www"):
                    website = contacts["www"][0].get("value", "") if contacts["www"] else ""

                # Primary feature_id from first matching fid
                primary_fid = fids[0] if fids else "viewpoint"

                yield {
                    "name":        title,
                    "type":        type_label,
                    "type_id":     primary_fid,
                    "lat":         round(float(f_lat), 6),
                    "lng":         round(float(f_lng), 6),
                    "elevation":   "",
                    "description": item.get("description", ""),
                    "wikipedia":   "",
                    "website":     website,
                    "city":        address.get("city", "") or address.get("town", "") or address.get("village", ""),
                    "region":      address.get("state", "") or address.get("county", ""),
                    "country":     address.get("countryName", ""),
                    "image":       "",
                    "osm_id":      "",
                    "source":      "HERE Places",
                    "confidence":  _confidence(contacts, here_cats),
                }

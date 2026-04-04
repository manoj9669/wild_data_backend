"""
Geoapify Places API — global POI database.
Free tier: 3,000 requests/day.
Per-extraction cap: MAX_CALLS_PER_RUN (default 20) to leave headroom for multiple runs.

Setup:
1. Register at https://www.geoapify.com  (free account)
2. Set env var: GEOAPIFY_API_KEY

API docs: https://apidocs.geoapify.com/docs/places
"""

import os
import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter

GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY", "")
GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"

# Free tier: 3,000 requests/day.
# We cap at 20 calls per extraction run → ~150 runs/day within free tier.
MAX_CALLS_PER_RUN = 20

# WildData feature ID → Geoapify category string
GEOAPIFY_CATEGORIES: Dict[str, str] = {
    "waterfall":  "natural.water",
    "peak":       "natural.mountain",
    "beach":      "natural.beach",
    "glacier":    "natural",
    "volcano":    "natural.mountain",
    "cave":       "natural",
    "hot_spring": "natural.water",
    "waterway":   "natural.water",
    "park":       "national_park,leisure.park",
    "forest":     "natural.forest",
    "viewpoint":  "tourism.sights",
    "cultural":   "tourism.sights,heritage",
    "historic":   "heritage,tourism.sights.archaeological_site,tourism.sights.castle",
    "camp":       "camping.camp_site",
    "hiking":     "sport.outdoor,natural",
    "mtb":        "sport.cycling,sport.outdoor",
}

# Geoapify category → display label
CATEGORY_LABELS: Dict[str, str] = {
    "natural.water":            "Water Feature",
    "natural.mountain":         "Mountain / Peak",
    "natural.beach":            "Beach",
    "natural.forest":           "Forest",
    "natural":                  "Natural Feature",
    "national_park":            "National Park",
    "leisure.park":             "Park",
    "tourism.sights":           "Tourist Sight",
    "heritage":                 "Heritage Site",
    "tourism.sights.archaeological_site": "Archaeological Site",
    "tourism.sights.castle":    "Castle / Fort",
    "camping.camp_site":        "Campsite",
    "sport.outdoor":            "Outdoor Sport",
    "sport.cycling":            "Cycling",
}


def _make_filter(
    lat: float,
    lng: float,
    radius_km: float,
    bbox: Optional[Tuple[float, float, float, float]],
) -> str:
    """Return Geoapify filter string — rect (bbox) preferred over circle."""
    if bbox:
        south, west, north, east = bbox
        return f"rect:{west},{south},{east},{north}"
    radius_m = int(min(radius_km * 1000, 50000))
    return f"circle:{lng},{lat},{radius_m}"


def _primary_category(cats) -> str:
    if not cats:
        return "natural"
    if isinstance(cats, list):
        return cats[0] if cats else "natural"
    return cats.split(",")[0].strip()


async def fetch_geoapify(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch places from Geoapify Places API.
    Respects the free-tier cap of MAX_CALLS_PER_RUN requests per extraction run.
    Yields one result dict at a time.
    """
    if not GEOAPIFY_API_KEY:
        print("[Geoapify] GEOAPIFY_API_KEY not set — skipping")
        return

    filter_str = _make_filter(lat, lng, radius_km, bbox)
    seen: set = set()
    calls_made = 0

    async with httpx.AsyncClient(timeout=20) as client:
        for fid in feature_ids:
            if calls_made >= MAX_CALLS_PER_RUN:
                print(f"[Geoapify] Free tier cap reached ({MAX_CALLS_PER_RUN} calls) — stopping")
                return

            categories = GEOAPIFY_CATEGORIES.get(fid)
            if not categories:
                continue

            # Split multi-category strings and query each separately
            for cat in categories.split(","):
                cat = cat.strip()
                if calls_made >= MAX_CALLS_PER_RUN:
                    break

                try:
                    await rate_limiter.wait("api.geoapify.com", 0.3)
                    resp = await client.get(
                        GEOAPIFY_PLACES_URL,
                        params={
                            "categories": cat,
                            "filter":     filter_str,
                            "limit":      min(limit, 100),  # Geoapify max per call = 100
                            "apiKey":     GEOAPIFY_API_KEY,
                        },
                    )
                    calls_made += 1

                    if resp.status_code == 402:
                        print("[Geoapify] Daily free tier quota exceeded (402)")
                        return
                    if resp.status_code == 429:
                        print("[Geoapify] Rate limit hit (429) — stopping")
                        return
                    if resp.status_code != 200:
                        print(f"[Geoapify] HTTP {resp.status_code} for {fid}/{cat}")
                        continue

                    data = resp.json()

                except Exception as e:
                    print(f"[Geoapify] fetch error ({fid}/{cat}): {e}")
                    continue

                for feat in data.get("features", []):
                    props = feat.get("properties", {})
                    place_id = props.get("place_id", "")

                    if not place_id or place_id in seen:
                        continue
                    seen.add(place_id)

                    coords = feat.get("geometry", {}).get("coordinates", [])
                    if len(coords) < 2:
                        continue
                    f_lng, f_lat = float(coords[0]), float(coords[1])

                    name = (
                        props.get("name") or
                        props.get("address_line1") or
                        ""
                    ).strip()
                    if not name:
                        continue

                    cat_key = _primary_category(props.get("categories") or [cat])
                    type_label = CATEGORY_LABELS.get(cat_key, fid.replace("_", " ").title())

                    # Build confidence from datasource
                    datasource = props.get("datasource", {})
                    raw_src = datasource.get("sourcename", "")
                    confidence = "Medium"
                    if props.get("wiki_and_media", {}).get("wikipedia"):
                        confidence = "High"

                    wiki_url = ""
                    wiki_raw = props.get("wiki_and_media", {}).get("wikipedia", "")
                    if wiki_raw:
                        if wiki_raw.startswith("http"):
                            wiki_url = wiki_raw
                        else:
                            wiki_url = f"https://en.wikipedia.org/wiki/{wiki_raw.replace(' ', '_')}"

                    yield {
                        "name":        name,
                        "type":        type_label,
                        "type_id":     fid,
                        "lat":         round(f_lat, 6),
                        "lng":         round(f_lng, 6),
                        "elevation":   "",
                        "description": props.get("description", ""),
                        "wikipedia":   wiki_url,
                        "website":     props.get("website", ""),
                        "city":        props.get("city", "") or props.get("town", "") or props.get("village", ""),
                        "region":      props.get("state", "") or props.get("county", ""),
                        "country":     props.get("country", ""),
                        "image":       props.get("wiki_and_media", {}).get("image", ""),
                        "osm_id":      datasource.get("osm_id", "") if raw_src == "openstreetmap" else "",
                        "source":      "Geoapify",
                        "confidence":  confidence,
                    }

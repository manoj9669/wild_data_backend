"""
Foursquare Places API (v3) — global venue database.
Free tier: 1,000 API calls/day.
Per-extraction cap: MAX_CALLS_PER_RUN (default 10) to stay within free tier.

Best for: cultural sites, heritage venues, museums, religious/historic buildings,
          urban parks, viewpoints. NOT ideal for pure outdoor/wilderness data.

Setup:
1. Register at https://foursquare.com/developer  (free account)
2. Create a project → copy the API key
3. Set env var: FOURSQUARE_API_KEY

API docs: https://docs.foursquare.com/developer/reference/place-search
"""

import os
import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter

FOURSQUARE_API_KEY = os.getenv("FOURSQUARE_API_KEY", "")
FSQ_SEARCH_URL = "https://api.foursquare.com/v3/places/search"

# Free tier: 1,000 calls/day.
# We cap at 10 calls per extraction run → ~100 runs/day within free tier.
MAX_CALLS_PER_RUN = 10

# WildData feature ID → Foursquare category IDs
# FSQ category IDs: https://docs.foursquare.com/data-products/docs/categories
FSQ_CATEGORIES: Dict[str, str] = {
    "historic":   "16000",   # Landmarks & Outdoors > Historic & Heritage
    "cultural":   "10000",   # Arts & Entertainment (museums, galleries, monuments)
    "park":       "16032",   # Parks > National Park
    "viewpoint":  "16000",   # Landmarks & Outdoors
    "camp":       "16020",   # Campground
    "waterfall":  "16054",   # Waterfall
    "peak":       "16039",   # Mountain
    "cave":       "16026",   # Cave
    "beach":      "16019",   # Beach
    "hot_spring": "16052",   # Hot Spring
}

# FSQ category → display label
FSQ_LABELS: Dict[str, str] = {
    "16000": "Landmark",
    "10000": "Cultural Site",
    "16032": "National Park",
    "16020": "Campground",
    "16054": "Waterfall",
    "16039": "Mountain Peak",
    "16026": "Cave",
    "16019": "Beach",
    "16052": "Hot Spring",
}


def _make_params(
    lat: float,
    lng: float,
    radius_km: float,
    bbox: Optional[Tuple[float, float, float, float]],
    cat_id: str,
    limit: int,
) -> Dict:
    radius_m = int(min(radius_km * 1000, 50000))
    params = {
        "categories": cat_id,
        "limit":      min(limit, 50),  # FSQ free tier max = 50 per call
        "fields":     "fsq_id,name,geocodes,categories,location,description,website,related_places,tips",
    }
    if bbox:
        south, west, north, east = bbox
        params["ne"] = f"{north},{east}"
        params["sw"] = f"{south},{west}"
    else:
        params["ll"]     = f"{lat},{lng}"
        params["radius"] = radius_m
    return params


async def fetch_foursquare(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 50,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch places from Foursquare Places API v3.
    Best for: cultural, historical, and landmark POIs.
    Respects the free-tier cap of MAX_CALLS_PER_RUN calls per extraction run.
    Yields one result dict at a time.
    """
    if not FOURSQUARE_API_KEY:
        print("[Foursquare] FOURSQUARE_API_KEY not set — skipping")
        return

    seen: set = set()
    calls_made = 0
    headers = {
        "Authorization": FOURSQUARE_API_KEY,
        "Accept":        "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        for fid in feature_ids:
            if calls_made >= MAX_CALLS_PER_RUN:
                print(f"[Foursquare] Free tier cap reached ({MAX_CALLS_PER_RUN} calls) — stopping")
                return

            cat_id = FSQ_CATEGORIES.get(fid)
            if not cat_id:
                continue

            try:
                await rate_limiter.wait("api.foursquare.com", 0.5)
                params = _make_params(lat, lng, radius_km, bbox, cat_id, limit)
                resp = await client.get(FSQ_SEARCH_URL, params=params, headers=headers)
                calls_made += 1

                if resp.status_code == 429:
                    print("[Foursquare] Rate limit hit (429) — stopping")
                    return
                if resp.status_code == 403:
                    print("[Foursquare] Auth error (403) — check FOURSQUARE_API_KEY")
                    return
                if resp.status_code != 200:
                    print(f"[Foursquare] HTTP {resp.status_code} for {fid}")
                    continue

                data = resp.json()

            except Exception as e:
                print(f"[Foursquare] fetch error ({fid}): {e}")
                continue

            for place in data.get("results", []):
                fsq_id = place.get("fsq_id", "")
                if not fsq_id or fsq_id in seen:
                    continue
                seen.add(fsq_id)

                name = place.get("name", "").strip()
                if not name:
                    continue

                geocodes = place.get("geocodes", {})
                main_geo = geocodes.get("main", {})
                f_lat = main_geo.get("latitude")
                f_lng = main_geo.get("longitude")
                if not f_lat or not f_lng:
                    continue

                location = place.get("location", {})
                cats = place.get("categories", [])
                cat_label = cats[0].get("name", FSQ_LABELS.get(cat_id, fid)) if cats else FSQ_LABELS.get(cat_id, fid)

                desc = place.get("description", "")
                tips = place.get("tips", [])
                if not desc and tips:
                    desc = tips[0].get("text", "")[:400]

                yield {
                    "name":        name,
                    "type":        cat_label,
                    "type_id":     fid,
                    "lat":         round(float(f_lat), 6),
                    "lng":         round(float(f_lng), 6),
                    "elevation":   "",
                    "description": desc,
                    "wikipedia":   "",
                    "website":     place.get("website", ""),
                    "city":        location.get("locality", "") or location.get("dma", ""),
                    "region":      location.get("region", ""),
                    "country":     location.get("country", ""),
                    "image":       "",
                    "osm_id":      "",
                    "source":      "Foursquare",
                    "confidence":  "Medium",
                }

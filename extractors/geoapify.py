"""
Geoapify Places API — optional spot lookups.

Setup:
1. Create a Geoapify account
2. Set env var: GEOAPIFY_API_KEY

Docs:
https://apidocs.geoapify.com/docs/places/
"""

import math
import os
from typing import List, Dict, Any, AsyncGenerator
import httpx

from utils.rate_limiter import rate_limiter
from utils.usage_caps import usage_caps


GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY", "")
GEOAPIFY_URL = "https://api.geoapify.com/v2/places"
GEOAPIFY_MAX_PER_FEATURE = int(os.getenv("GEOAPIFY_MAX_PER_FEATURE", "40"))


# Use precise Geoapify categories where available (avoid overly broad matches).
# Categories reference: https://apidocs.geoapify.com/docs/places/
FEATURE_CATEGORIES = {
    "peak":       ["natural.mountain.peak"],
    "cave":       ["natural.mountain.cave_entrance"],
    "glacier":    ["natural.mountain.glacier"],
    "hot_spring": ["natural.water.hot_spring"],
    "forest":     ["natural.forest"],
    "park":       ["national_park", "natural.protected_area", "leisure.park.nature_reserve"],
    "viewpoint":  ["tourism.attraction.viewpoint"],
    "cultural":   [
        "entertainment.museum",
        "entertainment.culture.gallery",
        "entertainment.culture.arts_centre",
        "entertainment.culture.theatre",
        "tourism.attraction.artwork",
        "tourism.sights.place_of_worship",
    ],
    "historic":   [
        "heritage",
        "heritage.unesco",
        "building.historic",
        "tourism.sights.archaeological_site",
        "tourism.sights.castle",
        "tourism.sights.fort",
        "tourism.sights.memorial",
        "tourism.sights.memorial.monument",
        "tourism.sights.ruines",
        "tourism.sights.monastery",
    ],
    "camp":       ["camping.camp_site", "camping.camp_pitch", "camping.caravan_site"],
    "beach":      ["beach", "beach.beach_resort"],
    # NOTE: Waterfalls, volcanoes, lakes/rivers, and trail networks don't have
    # precise categories in Geoapify's public list, so we skip them here to
    # avoid noisy results. Those are covered by OSM/OpenTripMap/Waymarked.
}

FEATURE_LABELS = {
    "waterfall": "Waterfall", "pool": "Natural Pool", "hiking": "Hike", "mtb": "MTB / Cycling",
    "motorbiking": "Motorbiking Route", "peak": "Mountain Peak", "park": "National Park",
    "viewpoint": "Viewpoint", "camp": "Free-Camping Sites", "hut": "Hut", "cave": "Cave",
    "hot_spring": "Hot Spring", "lake": "Lake", "beach": "Beach", "gorge": "Adventure Gorge/Canyon",
    "meadow": "Meadow", "unesco": "Unesco Heritage", "forest_walk": "Forest Walk", "monastery": "Old Monastery & Temple",
    "glacier": "Glacier", "volcano": "Volcano", "forest": "Forest",
}


def _estimate_credits(limit: int) -> int:
    if limit <= 0:
        return 0
    return max(1, math.ceil(limit / 20))


async def fetch_geoapify(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    if not GEOAPIFY_API_KEY:
        print("[Geoapify] GEOAPIFY_API_KEY not set — skipping")
        return

    radius_m = int(min(radius_km * 1000, 50000))  # keep queries tight

    async with httpx.AsyncClient(timeout=20) as client:
        for fid in feature_ids:
            cats = FEATURE_CATEGORIES.get(fid)
            if not cats:
                continue

            per_limit = min(limit, GEOAPIFY_MAX_PER_FEATURE)
            if per_limit <= 0:
                continue

            credits = _estimate_credits(per_limit)
            allowed = await usage_caps.spend("geoapify", cost=credits)
            if not allowed:
                print("[Geoapify] Free-tier cap reached — skipping")
                return

            try:
                await rate_limiter.wait("api.geoapify.com", 0.25)
                resp = await client.get(
                    GEOAPIFY_URL,
                    params={
                        "categories": ",".join(cats),
                        "filter": f"circle:{lng},{lat},{radius_m}",
                        "bias": f"proximity:{lng},{lat}",
                        "limit": per_limit,
                        "apiKey": GEOAPIFY_API_KEY,
                    },
                )
                if resp.status_code == 429:
                    print("[Geoapify] Rate limit hit — stopping")
                    return
                if resp.status_code != 200:
                    print(f"[Geoapify] HTTP {resp.status_code} for {fid}")
                    continue

                data = resp.json()

            except Exception as e:
                print(f"[Geoapify] error ({fid}): {e}")
                continue

            for feat in data.get("features", []):
                props = feat.get("properties", {}) or {}
                p_lat = props.get("lat")
                p_lng = props.get("lon")
                if p_lat is None or p_lng is None:
                    continue

                name = props.get("name", "")
                confidence = "Medium" if name else "Low"

                yield {
                    "name": name,
                    "type": FEATURE_LABELS.get(fid, fid.replace("_", " ").title()),
                    "type_id": fid,
                    "lat": p_lat,
                    "lng": p_lng,
                    "elevation": "",
                    "description": "",
                    "wikipedia": "",
                    "website": props.get("website") or props.get("contact:website") or "",
                    "region": props.get("state") or "",
                    "country": props.get("country") or "",
                    "image": "",
                    "osm_id": f"geoapify:{props.get('place_id','')}",
                    "source": "Geoapify",
                    "confidence": confidence,
                }

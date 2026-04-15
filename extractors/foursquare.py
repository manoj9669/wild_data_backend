"""
Foursquare Places API — optional spot lookups.
Free tier: keep calls under your monthly cap to stay free.

Setup:
1. Create a Foursquare developer account
2. Set env var: FOURSQUARE_API_KEY

Docs:
https://docs.foursquare.com/developer/reference/places-api-usage
"""

import os
from typing import List, Dict, Any, AsyncGenerator
import httpx

from utils.rate_limiter import rate_limiter
from utils.usage_caps import usage_caps


FOURSQUARE_API_KEY = os.getenv("FOURSQUARE_API_KEY", "")
FSQ_SEARCH_URL = "https://api.foursquare.com/v3/places/search"

FOURSQUARE_MAX_PER_FEATURE = int(os.getenv("FOURSQUARE_MAX_PER_FEATURE", "20"))

FEATURE_QUERY = {
    "waterfall":  "waterfall",
    "hiking":     "hiking trail",
    "mtb":        "mountain bike trail",
    "motorbiking":"scenic route",
    "peak":       "mountain peak",
    "park":       "national park",
    "viewpoint":  "viewpoint",
    "cultural":   "cultural center",
    "historic":   "historic site",
    "camp":       "campground",
    "cave":       "cave",
    "hot_spring": "hot spring",
    "lake":   "lake",
    "beach":      "beach",
    "glacier":    "glacier",
    "volcano":    "volcano",
    "forest":     "forest",
}

FEATURE_LABELS = {
    "waterfall": "Waterfall", "hiking": "Hiking Trail", "mtb": "MTB / Cycling",
    "motorbiking": "Motorbiking Route", "peak": "Mountain Peak", "park": "National Park",
    "viewpoint": "Viewpoint", "cultural": "Cultural Site", "historic": "Historic Site",
    "camp": "Campsite", "cave": "Cave",
    "hot_spring": "Hot Spring", "lake": "Lake / River", "beach": "Beach",
    "glacier": "Glacier", "volcano": "Volcano", "forest": "Forest",
}


async def fetch_foursquare(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    if not FOURSQUARE_API_KEY:
        print("[Foursquare] FOURSQUARE_API_KEY not set — skipping")
        return

    radius_m = int(min(radius_km * 1000, 100000))  # FSQ radius cap ~100km

    async with httpx.AsyncClient(timeout=20) as client:
        for fid in feature_ids:
            query = FEATURE_QUERY.get(fid)
            if not query:
                continue

            per_limit = min(limit, FOURSQUARE_MAX_PER_FEATURE)
            if per_limit <= 0:
                continue

            allowed = await usage_caps.spend("foursquare", cost=1)
            if not allowed:
                print("[Foursquare] Free-tier cap reached — skipping")
                return

            try:
                await rate_limiter.wait("api.foursquare.com", 0.15)
                resp = await client.get(
                    FSQ_SEARCH_URL,
                    params={
                        "ll": f"{lat},{lng}",
                        "radius": radius_m,
                        "limit": per_limit,
                        "query": query,
                    },
                    headers={
                        "Authorization": FOURSQUARE_API_KEY,
                        "Accept": "application/json",
                    },
                )
                if resp.status_code == 429:
                    print("[Foursquare] Rate limit hit — stopping")
                    return
                if resp.status_code != 200:
                    print(f"[Foursquare] HTTP {resp.status_code} for {fid}")
                    continue

                data = resp.json()

            except Exception as e:
                print(f"[Foursquare] error ({fid}): {e}")
                continue

            for place in data.get("results", []):
                geocodes = (place.get("geocodes") or {}).get("main", {})
                p_lat = geocodes.get("latitude")
                p_lng = geocodes.get("longitude")
                if p_lat is None or p_lng is None:
                    continue

                name = place.get("name", "")
                cats = place.get("categories") or []
                cat_name = cats[0].get("name") if cats else ""
                location = place.get("location") or {}

                confidence = "Medium" if name else "Low"

                yield {
                    "name": name,
                    "type": FEATURE_LABELS.get(fid, cat_name or fid.replace("_", " ").title()),
                    "type_id": fid,
                    "lat": p_lat,
                    "lng": p_lng,
                    "elevation": "",
                    "description": "",
                    "wikipedia": "",
                    "website": place.get("website", ""),
                    "region": location.get("region") or location.get("state") or "",
                    "country": location.get("country") or "",
                    "image": "",
                    "osm_id": f"fsq:{place.get('fsq_id','')}",
                    "source": "Foursquare",
                    "confidence": confidence,
                }

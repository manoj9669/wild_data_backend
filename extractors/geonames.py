"""
GeoNames.org — global geographic names database.
Works for ALL countries. Free registered account required.
Register at: https://www.geonames.org/login
Then enable free web services in your account settings.
Set env var: GEONAMES_USERNAME
"""

import math
import os
import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME", "demo")
GEONAMES_URL = "http://api.geonames.org/searchJSON"

# GeoNames feature class + code per WildData feature ID
GEONAMES_FEATURE_MAP: Dict[str, tuple] = {
    "waterfall":  ("H", "FLLS"),   # Falls
    "peak":       ("T", "PK"),     # Peak / Summit
    "cave":       ("H", "CAVE"),   # Cave
    "beach":      ("H", "BCH"),    # Beach
    "hot_spring": ("H", "SPNG"),   # Spring
    "waterway":   ("H", "LK"),     # Lake
    "park":       ("L", "PRK"),    # Park
    "camp":       ("S", "RSRT"),   # Resort (closest to campsite in GeoNames)
    "glacier":    ("T", "GLCR"),   # Glacier
    "volcano":    ("T", "VLC"),    # Volcano
    "viewpoint":  ("T", "PK"),     # No viewpoint class — peaks are best proxy
}

GEONAMES_LABELS: Dict[str, str] = {
    "waterfall": "Waterfall", "peak": "Mountain Peak", "cave": "Cave",
    "beach": "Beach", "hot_spring": "Hot Spring", "waterway": "Lake",
    "park": "National Park", "camp": "Campsite", "glacier": "Glacier",
    "volcano": "Volcano", "viewpoint": "Viewpoint",
}


def _haversine(lat1, lng1, lat2, lng2) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


async def fetch_geonames(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    country_code: str = "",
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch geographic features from GeoNames.org for any country.
    Falls back silently if username is 'demo' and rate-limited.
    """
    if not GEONAMES_USERNAME:
        return

    deg = radius_km / 111.0

    for fid in feature_ids:
        fc_pair = GEONAMES_FEATURE_MAP.get(fid)
        if not fc_pair:
            continue

        fc_class, fc_code = fc_pair
        label = GEONAMES_LABELS.get(fid, fid)

        params = {
            "featureClass": fc_class,
            "featureCode":  fc_code,
            "north": lat + deg,
            "south": lat - deg,
            "east":  lng + deg,
            "west":  lng - deg,
            "maxRows": min(limit, 200),
            "username": GEONAMES_USERNAME,
            "type": "json",
            "lang": "en",
        }
        if country_code:
            params["country"] = country_code

        try:
            await rate_limiter.wait("api.geonames.org", 1.0)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(GEONAMES_URL, params=params)
                if resp.status_code != 200:
                    print(f"[GeoNames] HTTP {resp.status_code} for {fid}: {resp.text[:120]}")
                    continue
                data = resp.json()

            # GeoNames returns {"status": {"message": "...", "value": 18}} on errors
            if "status" in data:
                print(f"[GeoNames] API error for {fid}: {data['status'].get('message','')}")
                continue

            for item in data.get("geonames", []):
                try:
                    f_lat = float(item.get("lat", 0))
                    f_lng = float(item.get("lng", 0))
                except (TypeError, ValueError):
                    continue

                if _haversine(lat, lng, f_lat, f_lng) > radius_km:
                    continue

                name = item.get("name", "")
                if not name:
                    continue

                elev = item.get("elevation") or item.get("srtm3", "")

                yield {
                    "name": name,
                    "type": label,
                    "type_id": fid,
                    "lat": f_lat,
                    "lng": f_lng,
                    "elevation": f"{elev}m" if elev else "",
                    "description": item.get("fcodeName", ""),
                    "wikipedia": f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}",
                    "website": "",
                    "region": item.get("adminName1", ""),
                    "country": item.get("countryName", ""),
                    "image": "",
                    "osm_id": "",
                    "source": "GeoNames",
                    "confidence": "High",
                }

        except Exception as e:
            print(f"[GeoNames] {fid} error: {e}")
            continue

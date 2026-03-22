"""
Protected Planet / WDPA — World Database on Protected Areas.
Global protected areas: national parks, nature reserves, marine parks.
Free API key required — register at: https://api.protectedplanet.net
Set env var: WDPA_API_KEY
"""

import math
import os
import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

WDPA_API_KEY = os.getenv("WDPA_API_KEY", "")
WDPA_URL = "https://api.protectedplanet.net/v3/protected_areas/search"

IUCN_LABELS = {
    "Ia":  "Strict Nature Reserve",
    "Ib":  "Wilderness Area",
    "II":  "National Park",
    "III": "Natural Monument",
    "IV":  "Habitat / Species Management Area",
    "V":   "Protected Landscape",
    "VI":  "Protected Area with Sustainable Use",
}


def _haversine(lat1, lng1, lat2, lng2) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


async def fetch_protected_planet(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    country_code: str = "",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch protected areas from the Protected Planet API (WDPA).
    Only runs when 'park' is in the selected features.
    Requires WDPA_API_KEY env var.
    """
    if not any(f in feature_ids for f in ("park", "forest")):
        return

    if not WDPA_API_KEY:
        print("[ProtectedPlanet] WDPA_API_KEY not set — skipping. Register free at api.protectedplanet.net")
        return

    params = {
        "token":        WDPA_API_KEY,
        "latitude":     lat,
        "longitude":    lng,
        "radius":       int(min(radius_km, 500)),
        "with_geometry": False,
        "per_page":     50,
        "page":         1,
    }
    if country_code:
        params["country"] = country_code

    try:
        await rate_limiter.wait("api.protectedplanet.net", 1.0)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(WDPA_URL, params=params)
            if resp.status_code == 401:
                print("[ProtectedPlanet] Invalid API key — check WDPA_API_KEY env var")
                return
            if resp.status_code != 200:
                print(f"[ProtectedPlanet] HTTP {resp.status_code}")
                return
            data = resp.json()

        for area in data.get("protected_areas", []):
            centroid = area.get("centroid", {})
            f_lat = centroid.get("lat") or centroid.get("latitude")
            f_lng = centroid.get("long") or centroid.get("longitude")
            if not f_lat or not f_lng:
                continue
            try:
                f_lat, f_lng = float(f_lat), float(f_lng)
            except (TypeError, ValueError):
                continue

            if _haversine(lat, lng, f_lat, f_lng) > radius_km:
                continue

            iucn_cat = (area.get("iucn_category") or {}).get("name", "")
            type_label = IUCN_LABELS.get(iucn_cat, "Protected Area")
            wdpa_id = area.get("wdpa_id", "")
            marine = area.get("marine", "0")
            marine_label = " (Marine)" if str(marine) in ("1", "2", True) else ""

            yield {
                "name": area.get("name", ""),
                "type": type_label + marine_label,
                "type_id": "park",
                "lat": round(f_lat, 6),
                "lng": round(f_lng, 6),
                "elevation": "",
                "description": f"IUCN Category {iucn_cat} — {area.get('reported_area', '?')} km²",
                "wikipedia": "",
                "website": f"https://www.protectedplanet.net/{wdpa_id}" if wdpa_id else "",
                "region": "",
                "country": (area.get("countries") or [{}])[0].get("name", ""),
                "image": "",
                "osm_id": "",
                "source": "Protected Planet / WDPA",
                "confidence": "High",
            }

    except Exception as e:
        print(f"[ProtectedPlanet] error: {e}")

"""
Waymarked Trails — global trail index built on OpenStreetMap data.
Covers hiking, MTB/cycling, and horse riding trails worldwide.
Completely free, no API key required.
https://waymarkedtrails.org
"""

import math
import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

# Base URLs for each trail category
WAYMARKED_BASES = {
    "hiking": "https://hiking.waymarkedtrails.org/api/v1",
    "mtb":    "https://mtb.waymarkedtrails.org/api/v1",
    "cycling": "https://cycling.waymarkedtrails.org/api/v1",
}

TRAIL_LABELS = {
    "hiking":  "Hiking Trail",
    "mtb":     "MTB / Cycling Route",
    "cycling": "Cycling Route",
}


def _haversine(lat1, lng1, lat2, lng2) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


async def fetch_waymarked(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch trail routes from Waymarked Trails for the given location.
    Handles hiking, MTB, and cycling — skips others silently.
    """
    deg = radius_km / 111.0
    bbox = f"{lng-deg},{lat-deg},{lng+deg},{lat+deg}"

    # Map mtb feature to both mtb and cycling bases for better coverage
    feature_base_map: List[tuple] = []
    if "hiking" in feature_ids:
        feature_base_map.append(("hiking", WAYMARKED_BASES["hiking"]))
    if "mtb" in feature_ids:
        feature_base_map.append(("mtb", WAYMARKED_BASES["mtb"]))
        feature_base_map.append(("mtb", WAYMARKED_BASES["cycling"]))

    for fid, base_url in feature_base_map:
        label = TRAIL_LABELS.get(fid, "Trail")
        try:
            await rate_limiter.wait("waymarkedtrails.org", 0.5)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{base_url}/list/by_area",
                    params={
                        "bbox":   bbox,
                        "locale": "en",
                        "limit":  min(limit, 100),
                    },
                    headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
                )
                if resp.status_code != 200:
                    print(f"[Waymarked] {fid} HTTP {resp.status_code}")
                    continue
                data = resp.json()

            routes = data.get("results", data.get("routes", []))
            for route in routes:
                # Waymarked returns a bounding box per route; use its centre as location
                r_bbox = route.get("bbox")
                if r_bbox:
                    # bbox format: [minLng, minLat, maxLng, maxLat]
                    try:
                        r_lng = (r_bbox[0] + r_bbox[2]) / 2
                        r_lat = (r_bbox[1] + r_bbox[3]) / 2
                    except (TypeError, IndexError):
                        continue
                else:
                    r_lat = route.get("lat", 0)
                    r_lng = route.get("lon", route.get("lng", 0))

                if not r_lat or not r_lng:
                    continue
                if _haversine(lat, lng, float(r_lat), float(r_lng)) > radius_km:
                    continue

                name = route.get("name", "") or route.get("ref", "")
                if not name:
                    continue

                osm_id = route.get("id", "")
                length_km = route.get("length", 0)
                desc = ""
                if length_km:
                    try:
                        desc = f"{round(float(length_km), 1)} km trail"
                    except Exception:
                        pass

                yield {
                    "name": name,
                    "type": label,
                    "type_id": fid,
                    "lat": round(float(r_lat), 6),
                    "lng": round(float(r_lng), 6),
                    "elevation": "",
                    "description": desc,
                    "wikipedia": "",
                    "website": f"https://hiking.waymarkedtrails.org/#route?id={osm_id}" if fid == "hiking" else f"https://mtb.waymarkedtrails.org/#route?id={osm_id}",
                    "region": "",
                    "country": "",
                    "image": "",
                    "osm_id": f"relation/{osm_id}" if osm_id else "",
                    "source": "Waymarked Trails",
                    "confidence": "High",
                }

        except Exception as e:
            print(f"[Waymarked] {fid} error: {e}")
            continue

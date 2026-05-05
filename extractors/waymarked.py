"""
Waymarked Trails — global trail index built on OpenStreetMap data.
Covers hiking, MTB/cycling trails worldwide. Free, no API key required.
https://waymarkedtrails.org

Fix: by_area endpoint uses Web Mercator (EPSG:3857) bbox, NOT WGS84 lat/lng.
"""

import math
import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

WAYMARKED_BASES = {
    "hiking":  "https://hiking.waymarkedtrails.org/api/v1",
    "mtb":     "https://mtb.waymarkedtrails.org/api/v1",
    "cycling": "https://cycling.waymarkedtrails.org/api/v1",
}

TRAIL_LABELS = {
    "hiking":  "Hiking Trail",
    "mtb":     "MTB / Cycling Route",
    "cycling": "Cycling Route",
}

GROUP_LABELS = {
    "INT": "International Trail",
    "NAT": "National Trail",
    "REG": "Regional Trail",
    "LOC": "Local Trail",
}


def _to_mercator(lat: float, lng: float):
    """Convert WGS84 lat/lng to Web Mercator (EPSG:3857) x/y."""
    x = lng * 20037508.34 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) / (math.pi / 180)
    y = y * 20037508.34 / 180
    return x, y


def _from_mercator(x: float, y: float):
    """Convert Web Mercator x/y back to WGS84 lat/lng."""
    lng = x * 180 / 20037508.34
    lat = math.degrees(math.atan(math.exp(y * math.pi / 20037508.34)) * 2 - math.pi / 2)
    return lat, lng


async def fetch_waymarked(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch trail routes from Waymarked Trails.
    Uses Web Mercator bbox as required by the by_area endpoint.
    Works globally — Nepal, Greece, India, UK, USA, Bhutan, everywhere.
    """
    deg = radius_km / 111.0
    x1, y1 = _to_mercator(lat - deg, lng - deg)
    x2, y2 = _to_mercator(lat + deg, lng + deg)
    mercator_bbox = f"{x1},{y1},{x2},{y2}"

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
                        "bbox":   mercator_bbox,
                        "locale": "en",
                        "limit":  min(limit, 100),
                    },
                    headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
                )
                if resp.status_code != 200:
                    print(f"[Waymarked] {fid} HTTP {resp.status_code}")
                    continue
                data = resp.json()

            for route in data.get("results", []):
                osm_id = route.get("id", "")
                name = route.get("name", "") or route.get("ref", "")
                if not name:
                    continue

                # bbox is in Mercator — convert center back to WGS84
                r_bbox = route.get("bbox")
                if r_bbox and len(r_bbox) == 4:
                    cx = (r_bbox[0] + r_bbox[2]) / 2
                    cy = (r_bbox[1] + r_bbox[3]) / 2
                    r_lat, r_lng = _from_mercator(cx, cy)
                else:
                    r_lat = route.get("lat", 0)
                    r_lng = route.get("lon", route.get("lng", 0))

                if not r_lat or not r_lng:
                    continue

                group = route.get("group", "")
                trail_type = GROUP_LABELS.get(group, label)

                official_length = route.get("official_length", "")
                desc = f"{official_length} km" if official_length else ""

                base_domain = base_url.split("/api")[0]
                website = f"{base_domain}/#route?id={osm_id}" if osm_id else base_domain

                yield {
                    "name":        name,
                    "type":        trail_type,
                    "type_id":     fid,
                    "lat":         round(r_lat, 6),
                    "lng":         round(r_lng, 6),
                    "elevation":   "",
                    "description": desc,
                    "wikipedia":   "",
                    "website":     website,
                    "region":      "",
                    "country":     "",
                    "image":       "",
                    "osm_id":      f"relation/{osm_id}" if osm_id else "",
                    "source":      "Waymarked Trails",
                    "confidence":  "High",
                }

        except Exception as e:
            print(f"[Waymarked] {fid} error: {e}")
            continue

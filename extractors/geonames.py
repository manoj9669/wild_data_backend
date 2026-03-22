"""
GeoNames.org — global geographic names database.
Works for ALL countries. Free registered account required.

Setup:
1. Register at https://www.geonames.org/login
2. Go to your account → click "enable free web services"
3. Set env var: GEONAMES_USERNAME

Why GeoNames instead of Wikidata:
- GeoNames is purpose-built for geographic feature discovery
- Data sourced from official national surveys worldwide (USGS, IGN, etc.)
- Feature classification is reliable and consistent (H=Hydrographic, T=Mountain, etc.)
- No misclassification issues (rivers don't appear as waterfalls)
- Covers 11 million place names globally
"""

import math
import os
import httpx
from typing import List, Dict, Any, AsyncGenerator, Tuple
from utils.rate_limiter import rate_limiter

GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME", "demo")
GEONAMES_SEARCH_URL = "http://api.geonames.org/searchJSON"
GEONAMES_NEARBY_URL = "http://api.geonames.org/findNearbyJSON"

# GeoNames feature class + code(s) per WildData feature ID
# Multiple codes per feature = multiple API calls for comprehensive coverage.
# Source: https://www.geonames.org/export/codes.html
GEONAMES_FEATURE_MAP: Dict[str, List[Tuple[str, str]]] = {
    "waterfall": [
        ("H", "FLLS"),   # Falls
        ("H", "FLLSX"),  # Section of falls
    ],
    "peak": [
        ("T", "PK"),     # Peak
        ("T", "MT"),     # Mountain
        ("T", "MNTS"),   # Mountain range
        ("T", "DPRS"),   # Depression (saddle/pass)
        ("T", "PASS"),   # Pass
    ],
    "cave": [
        ("H", "CAVE"),   # Cave
        ("T", "RK"),     # Rock / natural arch
    ],
    "beach": [
        ("H", "BCH"),    # Beach
        ("H", "BCHS"),   # Beaches
        ("T", "CAPE"),   # Cape/headland
    ],
    "hot_spring": [
        ("H", "SPNG"),   # Spring
        ("H", "SPNGS"),  # Springs
    ],
    "waterway": [
        ("H", "LK"),     # Lake
        ("H", "LKS"),    # Lakes
        ("H", "LKRS"),   # Reservoir
        ("H", "STM"),    # Stream/river
        ("H", "STMH"),   # Headwaters
        ("H", "GRGE"),   # Gorge
    ],
    "park": [
        ("L", "PRK"),    # Park
        ("L", "PKLT"),   # Parkland — skip, but include L class for NP
        ("L", "RESN"),   # Nature reserve
        ("L", "RES"),    # Reserve
    ],
    "camp": [
        ("S", "CAMP"),   # Camp
        ("S", "RSRT"),   # Resort
        ("P", "PPLL"),   # Locality (some campgrounds registered here)
    ],
    "glacier": [
        ("T", "GLCR"),   # Glacier
        ("T", "ICERF"),  # Ice field
        ("T", "ICECAP"), # Ice cap
    ],
    "volcano": [
        ("T", "VLC"),    # Volcano
    ],
    "viewpoint": [
        ("T", "CLF"),    # Cliff
        ("T", "CLFS"),   # Cliffs
        ("T", "HDLD"),   # Headland
        ("T", "PK"),     # Peak (also good viewpoints)
    ],
    "forest": [
        ("V", "FRST"),   # Forest
        ("V", "FRSTF"),  # Fossilized forest
        ("L", "RES"),    # Reserve / protected area
    ],
    "hiking": [
        ("T", "PASS"),   # Mountain pass (common hiking destination)
        ("T", "TRL"),    # Trail (if available)
    ],
}

GEONAMES_LABELS: Dict[str, str] = {
    "waterfall": "Waterfall", "peak": "Mountain Peak", "cave": "Cave",
    "beach": "Beach", "hot_spring": "Hot Spring", "waterway": "Lake / River",
    "park": "National Park", "camp": "Campsite", "glacier": "Glacier",
    "volcano": "Volcano", "viewpoint": "Viewpoint", "forest": "Forest",
    "hiking": "Hiking Area",
}

# GeoNames feature code → specific display label
GEONAMES_CODE_LABELS: Dict[str, str] = {
    "FLLS": "Waterfall", "FLLSX": "Waterfall",
    "PK": "Mountain Peak", "MT": "Mountain", "MNTS": "Mountain Range",
    "PASS": "Mountain Pass", "DPRS": "Saddle",
    "CAVE": "Cave", "RK": "Rock Formation",
    "BCH": "Beach", "BCHS": "Beach", "CAPE": "Cape",
    "SPNG": "Hot Spring", "SPNGS": "Hot Springs",
    "LK": "Lake", "LKS": "Lakes", "LKRS": "Reservoir",
    "STM": "River", "STMH": "River Headwaters", "GRGE": "Gorge",
    "PRK": "National Park", "RESN": "Nature Reserve", "RES": "Reserve",
    "CAMP": "Campsite", "RSRT": "Resort / Camp",
    "GLCR": "Glacier", "ICERF": "Ice Field",
    "VLC": "Volcano",
    "CLF": "Cliff", "CLFS": "Cliffs", "HDLD": "Headland",
    "FRST": "Forest",
}


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
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
    Uses bounding box search with specific feature codes for precision.
    Falls back silently if username is 'demo' and rate-limited.
    """
    if not GEONAMES_USERNAME:
        return

    deg = radius_km / 111.0
    seen: set = set()

    for fid in feature_ids:
        fc_pairs = GEONAMES_FEATURE_MAP.get(fid)
        if not fc_pairs:
            continue

        label = GEONAMES_LABELS.get(fid, fid)

        for fc_class, fc_code in fc_pairs:
            params: Dict[str, Any] = {
                "featureClass": fc_class,
                "featureCode":  fc_code,
                "north":   lat + deg,
                "south":   lat - deg,
                "east":    lng + deg,
                "west":    lng - deg,
                "maxRows": min(limit, 200),
                "username": GEONAMES_USERNAME,
                "type":    "json",
                "lang":    "en",
                "orderby": "relevance",
            }
            if country_code:
                params["country"] = country_code

            try:
                await rate_limiter.wait("api.geonames.org", 1.0)
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(GEONAMES_SEARCH_URL, params=params)
                    if resp.status_code != 200:
                        print(f"[GeoNames] HTTP {resp.status_code} for {fid}/{fc_code}")
                        continue
                    data = resp.json()

                # GeoNames returns {"status": {"message": "...", "value": N}} on errors
                if "status" in data:
                    msg = data["status"].get("message", "")
                    print(f"[GeoNames] API error for {fid}/{fc_code}: {msg}")
                    if "hourly" in msg.lower() or "limit" in msg.lower():
                        return   # stop all GeoNames calls if rate-limited
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

                    key = f"{name}_{round(f_lat,3)}_{round(f_lng,3)}"
                    if key in seen:
                        continue
                    seen.add(key)

                    elev = item.get("elevation") or item.get("srtm3", "")
                    code = item.get("fcode", fc_code)
                    type_label = GEONAMES_CODE_LABELS.get(code, label)

                    yield {
                        "name":        name,
                        "type":        type_label,
                        "type_id":     fid,
                        "lat":         f_lat,
                        "lng":         f_lng,
                        "elevation":   f"{elev}m" if elev else "",
                        "description": item.get("fcodeName", type_label),
                        "wikipedia":   f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}",
                        "website":     "",
                        "region":      item.get("adminName1", ""),
                        "country":     item.get("countryName", ""),
                        "image":       "",
                        "osm_id":      "",
                        "source":      "GeoNames",
                        "confidence":  "High",
                    }

            except Exception as e:
                print(f"[GeoNames] {fid}/{fc_code} error: {e}")
                continue

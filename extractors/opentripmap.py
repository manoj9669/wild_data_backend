"""
OpenTripMap — tourism & attractions database.
Purpose-built for tourism discovery: waterfalls, peaks, caves, viewpoints,
historic sites, national parks and more. 5,000 requests/day free.

Setup:
1. Register at https://opentripmap.com
2. Set env var: OPENTRIPMAP_API_KEY

API docs: https://dev.opentripmap.org/docs
"""

import os
import math
import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter
from utils.usage_caps import usage_caps

OPENTRIPMAP_API_KEY = os.getenv("OPENTRIPMAP_API_KEY", "")
OTM_RADIUS_URL = "https://api.opentripmap.com/0.1/en/places/radius"
OTM_XID_URL    = "https://api.opentripmap.com/0.1/en/places/xid"

# WildData feature ID → OpenTripMap kinds (comma-separated, hierarchical).
# Hiking / MTB are omitted: OTM's "natural" bucket is too broad (peaks, forests, etc.)
# and was mis-tagged as trails. Trails come from OSM + Waymarked instead.
OTM_KINDS: Dict[str, str] = {
    "waterfall":  "waterfalls",
    "peak":       "mountain_peaks",
    "cave":       "caves",
    "beach":      "beaches",
    "hot_spring": "thermal_springs",
    "glacier":    "glaciers",
    "volcano":    "volcanoes",
    "viewpoint":  "viewpoints",
    "park":       "national_parks,nature_reserves",
    "forest":     "forests,nature_reserves",
    "camp":       "campsites",
    "lake":       "lakes",
}

# OpenTripMap kind → display label
KIND_LABELS: Dict[str, str] = {
    "waterfalls":      "Waterfall",
    "mountain_peaks":  "Mountain Peak",
    "caves":           "Cave",
    "beaches":         "Beach",
    "thermal_springs": "Hot Spring",
    "glaciers":        "Glacier",
    "volcanoes":       "Volcano",
    "viewpoints":      "Viewpoint",
    "national_parks":  "National Park",
    "nature_reserves": "Nature Reserve",
    "campsites":       "Campsite",
    "rivers":          "River",
    "lakes":           "Lake",
    "forests":         "Forest",
    "natural":         "Natural Feature",
}

# Rate ratings from OTM: 0=unrated, 1=minor, 2=medium, 3=top
RATE_CONFIDENCE = {0: "Low", 1: "Low", 2: "Medium", 3: "High"}


def _otm_kind_to_type_id(kind_key: str, feature_ids: List[str]) -> Optional[str]:
    """
    Map OpenTripMap's resolved kind to a WildData type_id.
    Returns None if this result is not one of the user's selected types.
    """
    if kind_key == "nature_reserves":
        if "park" in feature_ids:
            return "park"
        if "forest" in feature_ids:
            return "forest"
        return None

    direct = {
        "waterfalls": "waterfall",
        "mountain_peaks": "peak",
        "caves": "cave",
        "beaches": "beach",
        "thermal_springs": "hot_spring",
        "glaciers": "glacier",
        "volcanoes": "volcano",
        "viewpoints": "viewpoint",
        "national_parks": "park",
        "forests": "forest",
        "campsites": "camp",
        "lakes": "lake",
        "rivers": "lake",  # OTM river POIs → lakes/water if user asked for lakes
    }
    tid = direct.get(kind_key)
    if tid == "lake" and "lake" not in feature_ids:
        return None
    if tid and tid in feature_ids:
        return tid
    return None


def _primary_kind(kinds_str: str) -> str:
    """Return the most specific kind from a comma-separated list."""
    if not kinds_str:
        return "natural"
    parts = [k.strip() for k in kinds_str.split(",")]
    # Prefer specific kinds over generic ones
    priority = [
        "waterfalls", "mountain_peaks", "caves", "beaches", "thermal_springs",
        "glaciers", "volcanoes", "viewpoints", "national_parks", "nature_reserves",
        "campsites", "rivers", "lakes", "forests",
    ]
    for p in priority:
        if p in parts:
            return p
    return parts[0]


async def _fetch_xid_detail(xid: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Fetch full detail for a single place by its xid."""
    try:
        allowed = await usage_caps.spend("opentripmap", cost=1)
        if not allowed:
            return {}
        await rate_limiter.wait("api.opentripmap.com", 0.25)
        resp = await client.get(
            f"{OTM_XID_URL}/{xid}",
            params={"apikey": OPENTRIPMAP_API_KEY},
            timeout=15,
        )
        if resp.status_code != 200:
            return {}
        return resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError, KeyError):
        return {}


OTM_MAX_RADIUS_KM = 45  # OTM hard cap ~50km; use 45 to be safe
# Max grid cells per run to protect the 5,000 req/day free tier
MAX_GRID_CELLS = 9  # 3×3 grid covers most large states/regions


def _grid_centers(
    bbox: Optional[Tuple[float, float, float, float]],
    lat: float,
    lng: float,
    radius_km: float,
) -> List[Tuple[float, float, float]]:
    """
    Return a list of (lat, lng, radius_km) search circles that tile the area.
    For small areas (radius ≤ OTM_MAX_RADIUS_KM) returns a single center point.
    For large areas, builds a grid of OTM_MAX_RADIUS_KM circles capped at MAX_GRID_CELLS.
    """
    if radius_km <= OTM_MAX_RADIUS_KM:
        return [(lat, lng, radius_km)]

    if bbox:
        south, west, north, east = bbox
    else:
        # Approximate bbox from center + radius
        deg = radius_km / 111.0
        south, north = lat - deg, lat + deg
        west,  east  = lng - deg / max(math.cos(math.radians(lat)), 0.01), \
                       lng + deg / max(math.cos(math.radians(lat)), 0.01)

    # How many cells fit across each axis
    lat_span_km = (north - south) * 111.0
    lng_span_km = (east  - west)  * 111.0 * math.cos(math.radians((north + south) / 2))
    cols = max(1, math.ceil(lng_span_km / (OTM_MAX_RADIUS_KM * 2)))
    rows = max(1, math.ceil(lat_span_km / (OTM_MAX_RADIUS_KM * 2)))

    # Cap total cells to protect API quota
    while rows * cols > MAX_GRID_CELLS:
        if rows >= cols:
            rows -= 1
        else:
            cols -= 1
    rows = max(1, rows)
    cols = max(1, cols)

    cell_lat = (north - south) / rows
    cell_lng = (east  - west)  / cols
    cell_r   = max(
        math.hypot(cell_lat * 111.0 / 2, cell_lng * 111.0 * math.cos(math.radians(lat)) / 2),
        10,
    )
    cell_r = min(cell_r, OTM_MAX_RADIUS_KM)

    centers = []
    for r in range(rows):
        for c in range(cols):
            clat = south + cell_lat * (r + 0.5)
            clng = west  + cell_lng * (c + 0.5)
            centers.append((clat, clng, cell_r))
    return centers


async def fetch_opentripmap(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch tourism features from OpenTripMap for any location worldwide.
    For large regions, tiles the area into a grid of 45km circles so the
    entire region is covered (OTM hard-caps each query at ~50km radius).
    Queries /places/radius per feature type, then fetches detail for
    notable places (rate >= 2) to get descriptions, images, and Wikipedia links.
    """
    if not OPENTRIPMAP_API_KEY:
        print("[OpenTripMap] OPENTRIPMAP_API_KEY not set — skipping")
        return

    grid = _grid_centers(bbox, lat, lng, radius_km)
    if len(grid) > 1:
        print(f"[OpenTripMap] Large region — using {len(grid)}-cell grid ({len(grid)*len(feature_ids)} queries)")

    seen: set = set()

    async with httpx.AsyncClient(timeout=30) as client:
        for fid in feature_ids:
            kinds = OTM_KINDS.get(fid)
            if not kinds:
                continue

            for (g_lat, g_lng, g_radius_km) in grid:
                g_radius_m = int(g_radius_km * 1000)

                try:
                    allowed = await usage_caps.spend("opentripmap", cost=1)
                    if not allowed:
                        print("[OpenTripMap] Daily cap reached — stopping")
                        return
                    await rate_limiter.wait("api.opentripmap.com", 0.25)
                    resp = await client.get(
                        OTM_RADIUS_URL,
                        params={
                            "radius":  g_radius_m,
                            "lon":     g_lng,
                            "lat":     g_lat,
                            "kinds":   kinds,
                            "limit":   min(limit, 500),
                            "rate":    "1",
                            "format":  "geojson",
                            "apikey":  OPENTRIPMAP_API_KEY,
                        },
                    )
                    if resp.status_code == 429:
                        print("[OpenTripMap] Rate limit hit — stopping")
                        return
                    if resp.status_code != 200:
                        print(f"[OpenTripMap] HTTP {resp.status_code} for {fid}")
                        continue

                    data = resp.json()

                except Exception as e:
                    print(f"[OpenTripMap] radius fetch error ({fid}): {e}")
                    continue

                features = data.get("features", [])
                # Sort by rate descending so we enrich the best ones first
                features.sort(key=lambda f: f.get("properties", {}).get("rate", 0), reverse=True)

                detail_count = 0
                for feat in features:
                    props = feat.get("properties", {})
                    xid   = props.get("xid", "")
                    name  = props.get("name", "").strip()

                    if not xid or xid in seen:
                        continue
                    seen.add(xid)

                    coords = feat.get("geometry", {}).get("coordinates", [])
                    if len(coords) < 2:
                        continue
                    f_lng, f_lat = float(coords[0]), float(coords[1])

                    rate      = props.get("rate", 0)
                    kinds_str = props.get("kinds", kinds)
                    kind_key  = _primary_kind(kinds_str)
                    type_id   = _otm_kind_to_type_id(kind_key, feature_ids)
                    if not type_id:
                        continue

                    type_label = KIND_LABELS.get(kind_key, type_id.replace("_", " ").title())
                    confidence = RATE_CONFIDENCE.get(rate, "Medium")

                    # Fetch detail for notable places (top 40 per feature type)
                    desc    = ""
                    wiki    = ""
                    image   = ""
                    website = ""
                    if rate >= 2 and detail_count < 40:
                        detail = await _fetch_xid_detail(xid, client)
                        detail_count += 1

                        info = detail.get("info", {})
                        desc = info.get("descr", "").strip()
                        if not desc:
                            desc = detail.get("wikipedia_extracts", {}).get("text", "").strip()
                        if desc and len(desc) > 500:
                            desc = desc[:497] + "..."

                        wiki = detail.get("wikipedia", "")
                        if not wiki:
                            url_info = detail.get("url", "")
                            if "wikipedia" in url_info:
                                wiki = url_info

                        preview = detail.get("preview", {})
                        image = preview.get("source", "") if preview else ""

                        # name fallback from detail
                        if not name:
                            name = detail.get("name", "").strip()

                    if not name:
                        continue

                    yield {
                        "name":        name,
                        "type":        type_label,
                        "type_id":     type_id,
                        "lat":         round(f_lat, 6),
                        "lng":         round(f_lng, 6),
                        "elevation":   "",
                        "description": desc,
                        "wikipedia":   wiki,
                        "website":     website,
                        "region":      "",
                        "country":     "",
                        "image":       image,
                        "osm_id":      props.get("osm", ""),
                        "source":      "OpenTripMap",
                        "confidence":  confidence,
                    }

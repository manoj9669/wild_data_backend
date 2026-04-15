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
import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

OPENTRIPMAP_API_KEY = os.getenv("OPENTRIPMAP_API_KEY", "")
OTM_RADIUS_URL = "https://api.opentripmap.com/0.1/en/places/radius"
OTM_XID_URL    = "https://api.opentripmap.com/0.1/en/places/xid"

# WildData feature ID → OpenTripMap kinds (comma-separated, hierarchical)
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
    "lake":   "rivers,lakes",
    "hiking":     "natural",
    "mtb":        "natural",
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
        await rate_limiter.wait("api.opentripmap.com", 0.25)
        resp = await client.get(
            f"{OTM_XID_URL}/{xid}",
            params={"apikey": OPENTRIPMAP_API_KEY},
            timeout=15,
        )
        if resp.status_code != 200:
            return {}
        return resp.json()
    except Exception:
        return {}


async def fetch_opentripmap(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch tourism features from OpenTripMap for any location worldwide.
    Queries /places/radius per feature type, then fetches detail for
    notable places (rate >= 2) to get descriptions, images, and Wikipedia links.
    """
    if not OPENTRIPMAP_API_KEY:
        print("[OpenTripMap] OPENTRIPMAP_API_KEY not set — skipping")
        return

    radius_m = int(min(radius_km * 1000, 50000))  # OTM max radius = 50km
    seen: set = set()

    async with httpx.AsyncClient(timeout=30) as client:
        for fid in feature_ids:
            kinds = OTM_KINDS.get(fid)
            if not kinds:
                continue

            try:
                await rate_limiter.wait("api.opentripmap.com", 0.25)
                resp = await client.get(
                    OTM_RADIUS_URL,
                    params={
                        "radius":  radius_m,
                        "lon":     lng,
                        "lat":     lat,
                        "kinds":   kinds,
                        "limit":   min(limit, 500),
                        "rate":    "1",       # only rate ≥ 1 (skip completely unrated)
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

                rate     = props.get("rate", 0)
                kinds_str = props.get("kinds", kinds)
                kind_key  = _primary_kind(kinds_str)
                type_label = KIND_LABELS.get(kind_key, fid.replace("_", " ").title())
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

                    addr = detail.get("address", {})
                    # name fallback from detail
                    if not name:
                        name = detail.get("name", "").strip()

                if not name:
                    continue

                yield {
                    "name":        name,
                    "type":        type_label,
                    "type_id":     fid,
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

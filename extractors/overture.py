"""
Overture Maps Places extractor — 100% free, no API key required.

Overture Maps Foundation (Meta + Microsoft + Amazon + TomTom) releases
global place data as open data under CDLA Permissive 2.0 license.

We query their Overture Maps API endpoint which allows bbox-based POI lookups
without authentication. The dataset has ~2.3B places globally.

API docs: https://docs.overturemaps.org/getting-data/overture-api/
"""

import httpx
from typing import AsyncGenerator, Dict, Any, List
from utils.rate_limiter import rate_limiter

# Overture Maps public tile API (GeoParquet) — use the GERS API for direct bbox queries
OVERTURE_API = "https://api.overturemaps.org/v1/places"

# Overture place category → WildData type_id mapping
# Overture uses hierarchical categories like "natural_feature.peak", "natural_feature.waterfall"
CATEGORY_MAP: Dict[str, str] = {
    # Natural features
    "natural_feature.peak":             "peak",
    "natural_feature.mountain":         "peak",
    "natural_feature.hill":             "peak",
    "natural_feature.waterfall":        "waterfall",
    "natural_feature.water":            "lake",
    "natural_feature.lake":             "lake",
    "natural_feature.beach":            "beach",
    "natural_feature.cave":             "cave",
    "natural_feature.hot_spring":       "hot_spring",
    "natural_feature.glacier":          "glacier",
    "natural_feature.volcano":          "volcano",
    "natural_feature.gorge":            "gorge",
    "natural_feature.forest":           "forest",
    # Outdoors & recreation
    "outdoors_and_recreation.nature_reserve":   "park",
    "outdoors_and_recreation.national_park":    "park",
    "outdoors_and_recreation.park":             "park",
    "outdoors_and_recreation.campground":       "camp",
    "outdoors_and_recreation.hiking_trail":     "hiking",
    "outdoors_and_recreation.viewpoint":        "viewpoint",
    "outdoors_and_recreation.scenic_lookout":   "viewpoint",
    # Landmarks & culture
    "historic_site":                    "historic",
    "landmark_and_historical_building": "historic",
    "arts_and_entertainment.historic":  "historic",
}

# Reverse map: WildData type_id → Overture categories to fetch
TYPE_TO_CATEGORIES: Dict[str, List[str]] = {}
for cat, tid in CATEGORY_MAP.items():
    TYPE_TO_CATEGORIES.setdefault(tid, []).append(cat)


def _type_from_categories(cats: List[str]) -> str | None:
    """Return the best WildData type_id for a list of Overture categories."""
    for cat in cats:
        # Try exact match first
        if cat in CATEGORY_MAP:
            return CATEGORY_MAP[cat]
        # Try prefix match (e.g. "natural_feature.peak.high" → "peak")
        for key in CATEGORY_MAP:
            if cat.startswith(key) or key.startswith(cat):
                return CATEGORY_MAP[key]
    return None


async def fetch_overture(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 200,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch outdoor places from Overture Maps API within a bounding box.
    Completely free, no API key required.
    """
    # Build bbox: south,west,north,east
    deg = radius_km / 111.0
    bbox = f"{lng - deg},{lat - deg},{lng + deg},{lat + deg}"

    # Collect categories matching requested feature types
    wanted_categories: List[str] = []
    for fid in feature_ids:
        wanted_categories.extend(TYPE_TO_CATEGORIES.get(fid, []))

    if not wanted_categories:
        return  # No matching categories for the requested feature types

    seen: set = set()

    async with httpx.AsyncClient(
        timeout=30,
        headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
    ) as client:
        for category in wanted_categories[:12]:  # Cap to avoid too many calls
            try:
                await rate_limiter.wait("api.overturemaps.org", 0.5)
                resp = await client.get(
                    OVERTURE_API,
                    params={
                        "bbox": bbox,
                        "categories": category,
                        "limit": min(limit, 100),
                        "format": "geojson",
                    },
                )
                if resp.status_code == 404:
                    # API endpoint not available in this deployment — skip silently
                    return
                if resp.status_code != 200:
                    print(f"[Overture] HTTP {resp.status_code} for {category}")
                    continue

                data = resp.json()
                features = data.get("features", []) if isinstance(data, dict) else []

                for feat in features:
                    props = feat.get("properties", {})
                    geom = feat.get("geometry", {})

                    # Coordinates
                    coords = geom.get("coordinates", [])
                    if geom.get("type") == "Point" and len(coords) >= 2:
                        f_lng, f_lat = coords[0], coords[1]
                    else:
                        continue

                    fid_key = props.get("id") or f"{f_lat:.4f},{f_lng:.4f}"
                    if fid_key in seen:
                        continue
                    seen.add(fid_key)

                    name = props.get("names", {}).get("primary") or props.get("name", "")
                    if not name:
                        continue

                    cats = props.get("categories", {})
                    primary_cat = cats.get("primary", category) if isinstance(cats, dict) else category
                    alt_cats = cats.get("alternate", []) if isinstance(cats, dict) else []
                    all_cats = [primary_cat] + (alt_cats or [])
                    type_id = _type_from_categories(all_cats)

                    if not type_id or type_id not in feature_ids:
                        continue

                    # Confidence from Overture's confidence score (0–1)
                    score = props.get("confidence", 0.5)
                    confidence = "High" if score >= 0.8 else "Medium" if score >= 0.5 else "Low"

                    # Website/social links
                    website = ""
                    for social in (props.get("socials") or []):
                        if "http" in str(social):
                            website = social
                            break

                    yield {
                        "name":        name,
                        "type":        primary_cat.replace("_", " ").title(),
                        "type_id":     type_id,
                        "lat":         round(f_lat, 6),
                        "lng":         round(f_lng, 6),
                        "elevation":   "",
                        "description": "",
                        "wikipedia":   "",
                        "website":     website,
                        "region":      props.get("addresses", [{}])[0].get("region", "") if props.get("addresses") else "",
                        "country":     props.get("addresses", [{}])[0].get("country", "") if props.get("addresses") else "",
                        "image":       "",
                        "osm_id":      props.get("sources", [{}])[0].get("record_id", "") if props.get("sources") else "",
                        "source":      "Overture Maps",
                        "confidence":  confidence,
                    }

            except httpx.TimeoutException:
                print(f"[Overture] timeout for category {category}")
            except Exception as e:
                print(f"[Overture] error for {category}: {e}")

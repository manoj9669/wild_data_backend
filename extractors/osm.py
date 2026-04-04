import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

FEATURE_TAGS = {
    "hiking":       ('relation', '"route"="hiking"'),
    "mtb":          ('relation', '"route"="mtb"'),
    "motorbiking":  ('relation', '"route"="motorcycle"'),
    "peak":         ('node', '"natural"="peak"'),
    "park":         ('relation', '"boundary"="national_park"'),
    "viewpoint":    ('node', '"tourism"="viewpoint"'),
    "camp":         ('node', '"tourism"="camp_site"'),
    "cave":         ('node', '"natural"="cave_entrance"'),
    "hot_spring":   ('node', '"natural"="hot_spring"'),
    "waterway":     ('node|way', '"natural"="water"'),   # lakes/ponds
    "beach":        ('node|way', '"natural"="beach"'),
    "glacier":      ('way', '"natural"="glacier"'),
    "volcano":      ('node', '"natural"="volcano"'),
    "forest":       ('relation', '"boundary"="protected_area"'),
}

FEATURE_LABELS = {
    "waterfall": "Waterfall", "hiking": "Hiking Route", "mtb": "MTB / Cycling",
    "motorbiking": "Motorbiking Route", "peak": "Mountain Peak", "park": "National Park",
    "viewpoint": "Viewpoint", "camp": "Campsite", "cave": "Cave",
    "hot_spring": "Hot Spring", "waterway": "Lake / Pond", "beach": "Beach",
    "glacier": "Glacier", "volcano": "Volcano", "forest": "Protected Forest",
}

# Waterfall uses a union query to include natural pools & swimming areas
WATERFALL_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  node["waterway"="waterfall"](around:{radius},{lat},{lng});
  node["natural"="water"]["water"="pool"](around:{radius},{lat},{lng});
  way["natural"="water"]["water"="pool"](around:{radius},{lat},{lng});
  node["leisure"="swimming_area"](around:{radius},{lat},{lng});
  way["leisure"="swimming_area"](around:{radius},{lat},{lng});
);
out tags center {limit};"""

WATERFALL_QUERY_BBOX_TMPL = """[out:json][timeout:{timeout}];
(
  node["waterway"="waterfall"]({south},{west},{north},{east});
  node["natural"="water"]["water"="pool"]({south},{west},{north},{east});
  way["natural"="water"]["water"="pool"]({south},{west},{north},{east});
  node["leisure"="swimming_area"]({south},{west},{north},{east});
  way["leisure"="swimming_area"]({south},{west},{north},{east});
);
out tags center {limit};"""


def _waterfall_type(tags: dict) -> str:
    """Determine display label from OSM tags for a waterfall union result."""
    if tags.get("waterway") == "waterfall":
        return "Waterfall"
    if tags.get("leisure") == "swimming_area":
        return "Swimming Area"
    if tags.get("water") == "pool":
        return "Natural Pool"
    return "Waterfall"


async def fetch_osm(
    lat: float,
    lng: float,
    radius_m: int,
    feature_ids: List[str],
    limit: int = 500,
    bbox: Optional[Tuple[float, float, float, float]] = None,  # (south, west, north, east)
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch features from OSM Overpass API.
    Yields one result dict at a time.
    """
    timeout = 60

    for fid in feature_ids:

        # ── Waterfall: union query for waterfalls + natural pools ────────────
        if fid == "waterfall":
            if bbox:
                south, west, north, east = bbox
                query = WATERFALL_QUERY_BBOX_TMPL.format(
                    timeout=timeout, south=south, west=west, north=north, east=east, limit=limit
                )
            else:
                query = WATERFALL_QUERY_TMPL.format(
                    timeout=timeout, radius=radius_m, lat=lat, lng=lng, limit=limit
                )
            try:
                await rate_limiter.wait("overpass-api.de", 1.5)
                async with httpx.AsyncClient(timeout=90) as client:
                    resp = await client.post(
                        OVERPASS_URL,
                        data={"data": query},
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    resp.raise_for_status()
                    data = resp.json()

                for el in data.get("elements", []):
                    el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
                    el_lng = el.get("lon") or (el.get("center") or {}).get("lon")
                    if not el_lat or not el_lng:
                        continue
                    tags = el.get("tags", {})
                    name = tags.get("name:en") or tags.get("name") or tags.get("int_name") or ""
                    wiki_tag = tags.get("wikipedia", "")
                    wiki_url = ""
                    if wiki_tag:
                        parts = wiki_tag.split(":", 1)
                        page = parts[1] if len(parts) == 2 else parts[0]
                        lang = parts[0] if len(parts) == 2 else "en"
                        wiki_url = f"https://{lang}.wikipedia.org/wiki/{page.replace(' ', '_')}"
                    confidence = "High" if (name and wiki_url) else "Medium" if name else "Low"
                    yield {
                        "name": name,
                        "type": _waterfall_type(tags),
                        "type_id": "waterfall",
                        "lat": el_lat,
                        "lng": el_lng,
                        "elevation": tags.get("ele", ""),
                        "description": tags.get("description") or tags.get("description:en") or "",
                        "wikipedia": wiki_url,
                        "website": tags.get("website") or tags.get("url") or "",
                        "city":   tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or "",
                        "region": tags.get("addr:state") or tags.get("is_in:state") or "",
                        "country": tags.get("addr:country") or tags.get("is_in:country") or "",
                        "image": tags.get("image") or tags.get("wikimedia_commons") or "",
                        "osm_id": f"{el.get('type','node')}/{el.get('id','')}",
                        "source": "OSM",
                        "confidence": confidence,
                    }
            except Exception as e:
                print(f"[OSM] waterfall error: {e}")
            continue

        # ── All other features: simple single-tag query ──────────────────────
        if fid not in FEATURE_TAGS:
            continue

        el_type, tag = FEATURE_TAGS[fid]
        label = FEATURE_LABELS.get(fid, fid)

        if bbox:
            south, west, north, east = bbox
            filter_str = f"({south},{west},{north},{east})"
        else:
            filter_str = f"(around:{radius_m},{lat},{lng})"

        query = f"""[out:json][timeout:{timeout}];
(
  {el_type}[{tag}]{filter_str};
);
out tags center {limit};"""

        try:
            await rate_limiter.wait("overpass-api.de", 1.5)
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    OVERPASS_URL,
                    data={"data": query},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                data = resp.json()

            for el in data.get("elements", []):
                el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
                el_lng = el.get("lon") or (el.get("center") or {}).get("lon")
                if not el_lat or not el_lng:
                    continue

                tags = el.get("tags", {})
                name = (
                    tags.get("name:en") or
                    tags.get("name") or
                    tags.get("int_name") or
                    ""
                )
                wiki_tag = tags.get("wikipedia", "")
                wiki_url = ""
                if wiki_tag:
                    parts = wiki_tag.split(":", 1)
                    page = parts[1] if len(parts) == 2 else parts[0]
                    lang = parts[0] if len(parts) == 2 else "en"
                    wiki_url = f"https://{lang}.wikipedia.org/wiki/{page.replace(' ', '_')}"

                confidence = "High" if (name and wiki_url) else "Medium" if name else "Low"

                yield {
                    "name": name,
                    "type": label,
                    "type_id": fid,
                    "lat": el_lat,
                    "lng": el_lng,
                    "elevation": tags.get("ele", ""),
                    "description": tags.get("description") or tags.get("description:en") or "",
                    "wikipedia": wiki_url,
                    "website": tags.get("website") or tags.get("url") or "",
                    "region": tags.get("addr:state") or tags.get("is_in:state") or "",
                    "country": tags.get("addr:country") or tags.get("is_in:country") or "",
                    "image": tags.get("image") or tags.get("wikimedia_commons") or "",
                    "osm_id": f"{el.get('type','node')}/{el.get('id','')}",
                    "source": "OSM",
                    "confidence": confidence,
                }

        except Exception as e:
            print(f"[OSM] {fid} error: {e}")
            continue

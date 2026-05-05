import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

SAC_SCALE_LABELS = {
    "hiking":                    "Easy (T1)",
    "mountain_hiking":           "Moderate (T2)",
    "demanding_mountain_hiking": "Hard (T3)",
    "alpine_hiking":             "Very Hard (T4)",
    "demanding_alpine_hiking":   "Expert (T5)",
    "difficult_alpine_hiking":   "Expert (T6)",
}

NETWORK_LABELS = {
    "iwn": "International Trail",
    "nwn": "National Trail",
    "rwn": "Regional Trail",
    "lwn": "Local Trail",
}

FEATURE_TAGS = {
    "motorbiking":  ('relation', '"route"="motorcycle"'),
    "peak":         ('node', '"natural"="peak"'),
    "park":         ('relation', '"boundary"="national_park"'),
    "viewpoint":    ('node', '"tourism"="viewpoint"'),
    "camp":         ('node|way', '"tourism"="camp_site"'),
    "hut":          ('node', '"tourism"="wilderness_hut"'),
    "cave":         ('node', '"natural"="cave_entrance"'),
    "hot_spring":   ('node', '"natural"="hot_spring"'),
    "lake":         ('node|way', '"natural"="water"["water"="lake"]'),
    "beach":        ('node|way', '"natural"="beach"'),
    "glacier":      ('way', '"natural"="glacier"'),
    "volcano":      ('node', '"natural"="volcano"'),
    "gorge":        ('node|way', '"natural"="gorge"'),
    "meadow":       ('way|relation', '"natural"="meadow"'),
}

FEATURE_LABELS = {
    "waterfall": "Waterfall", "pool": "Natural Pool", "hiking": "Hiking Trail",
    "mtb": "MTB / Cycling", "motorbiking": "Motorbiking Route", "peak": "Mountain Peak",
    "park": "National Park", "viewpoint": "Viewpoint", "camp": "Campsite",
    "hut": "Mountain Hut", "cave": "Cave", "hot_spring": "Hot Spring", "lake": "Lake",
    "beach": "Beach", "gorge": "Adventure Gorge/Canyon", "meadow": "Meadow",
    "glacier": "Glacier", "volcano": "Volcano", "historic": "Historical Site (Ruins, Fort)",
    "unesco": "Unesco Heritage", "forest_walk": "Forest Walk", "monastery": "Old Monastery & Temple"
}

WATERFALL_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  node["natural"="waterfall"](around:{radius},{lat},{lng});
  way["natural"="waterfall"](around:{radius},{lat},{lng});
  node["waterway"="waterfall"](around:{radius},{lat},{lng});
);
out tags center {limit};"""

POOL_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  node["natural"="water"]["water"="pool"](around:{radius},{lat},{lng});
  way["natural"="water"]["water"="pool"](around:{radius},{lat},{lng});
  node["leisure"="swimming_area"](around:{radius},{lat},{lng});
  way["leisure"="swimming_area"](around:{radius},{lat},{lng});
);
out tags center {limit};"""

HISTORIC_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  node["historic"="ruins"](around:{radius},{lat},{lng});
  way["historic"="ruins"](around:{radius},{lat},{lng});
  node["historic"="fort"](around:{radius},{lat},{lng});
  way["historic"="fort"](around:{radius},{lat},{lng});
  node["historic"="castle"](around:{radius},{lat},{lng});
  way["historic"="castle"](around:{radius},{lat},{lng});
);
out tags center {limit};"""

UNESCO_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  node["heritage"="1"](around:{radius},{lat},{lng});
  way["heritage"="1"](around:{radius},{lat},{lng});
  relation["heritage"="1"](around:{radius},{lat},{lng});
);
out tags center {limit};"""

FOREST_WALK_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  way["highway"="path"]["surface"="dirt"](around:{radius},{lat},{lng});
  way["highway"="footway"]["surface"="dirt"](around:{radius},{lat},{lng});
);
out tags center {limit};"""

MONASTERY_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  node["amenity"="monastery"](around:{radius},{lat},{lng});
  way["amenity"="monastery"](around:{radius},{lat},{lng});
  node["historic"="monastery"](around:{radius},{lat},{lng});
  node["amenity"="place_of_worship"]["religion"="buddhist"](around:{radius},{lat},{lng});
  node["amenity"="place_of_worship"]["religion"="hindu"](around:{radius},{lat},{lng});
);
out tags center {limit};"""

HIKING_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  relation["route"="hiking"](around:{radius},{lat},{lng});
  relation["route"="foot"](around:{radius},{lat},{lng});
);
out tags center {limit};"""

MTB_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  relation["route"="mtb"](around:{radius},{lat},{lng});
  relation["route"="bicycle"](around:{radius},{lat},{lng});
);
out tags center {limit};"""


def _build_trail_description(tags: dict, fid: str) -> str:
    parts = []
    sac = tags.get("sac_scale", "")
    if sac:
        parts.append(f"Difficulty: {SAC_SCALE_LABELS.get(sac, sac)}")
    dist = tags.get("distance") or tags.get("length", "")
    if dist:
        parts.append(f"Distance: {dist} km")
    ascent = tags.get("ascent", "")
    if ascent:
        parts.append(f"Ascent: {ascent}m")
    network = NETWORK_LABELS.get(tags.get("network", ""), "")
    if network:
        parts.append(network)
    return " · ".join(parts) if parts else tags.get("description") or tags.get("description:en") or ""


def _wiki_url(tags: dict) -> str:
    wiki_tag = tags.get("wikipedia", "")
    if not wiki_tag:
        return ""
    parts = wiki_tag.split(":", 1)
    page = parts[1] if len(parts) == 2 else parts[0]
    lang = parts[0] if len(parts) == 2 else "en"
    return f"https://{lang}.wikipedia.org/wiki/{page.replace(' ', '_')}"


def _make_result(el: dict, tags: dict, fid: str, el_lat: float, el_lng: float) -> dict:
    name = tags.get("name:en") or tags.get("name") or tags.get("int_name") or ""
    wiki = _wiki_url(tags)
    desc = _build_trail_description(tags, fid) if fid in ("hiking", "mtb") else \
           tags.get("description") or tags.get("description:en") or ""
    confidence = "High" if (name and wiki) else "Medium" if name else "Low"
    return {
        "name":        name,
        "type":        FEATURE_LABELS.get(fid, fid.title()),
        "type_id":     fid,
        "lat":         el_lat,
        "lng":         el_lng,
        "elevation":   tags.get("ele", ""),
        "description": desc,
        "wikipedia":   wiki,
        "website":     tags.get("website") or tags.get("url") or "",
        "city":        tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or "",
        "region":      tags.get("addr:state") or tags.get("is_in:state") or "",
        "country":     tags.get("addr:country") or tags.get("is_in:country") or "",
        "image":       tags.get("image") or tags.get("wikimedia_commons") or "",
        "osm_id":      f"{el.get('type','node')}/{el.get('id','')}",
        "source":      "OSM",
        "confidence":  confidence,
    }


async def fetch_osm(
    lat: float,
    lng: float,
    radius_m: int,
    feature_ids: List[str],
    limit: int = 500,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    timeout = 60

    UNION_QUERIES = {
        "waterfall":   WATERFALL_QUERY_TMPL,
        "pool":        POOL_QUERY_TMPL,
        "historic":    HISTORIC_QUERY_TMPL,
        "unesco":      UNESCO_QUERY_TMPL,
        "forest_walk": FOREST_WALK_QUERY_TMPL,
        "monastery":   MONASTERY_QUERY_TMPL,
        "hiking":      HIKING_QUERY_TMPL,
        "mtb":         MTB_QUERY_TMPL,
    }

    for fid in feature_ids:

        # ── Template queries (union / multi-tag) ───────────────────────────
        if fid in UNION_QUERIES:
            query = UNION_QUERIES[fid].format(
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
                    yield _make_result(el, tags, fid, el_lat, el_lng)

            except Exception as e:
                print(f"[OSM] {fid} error: {e}")
            continue

        # ── Single-tag queries ─────────────────────────────────────────────
        if fid not in FEATURE_TAGS:
            continue

        el_type, tag = FEATURE_TAGS[fid]
        filter_str = f"({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]})" if bbox else f"(around:{radius_m},{lat},{lng})"

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
                yield _make_result(el, tags, fid, el_lat, el_lng)

        except Exception as e:
            print(f"[OSM] {fid} error: {e}")
            continue

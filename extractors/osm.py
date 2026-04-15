import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

FEATURE_TAGS = {
    "hiking":       ('relation', '"route"="hiking"'),
    "mtb":          ('relation', '"route"="mtb"'),
    "motorbiking":  ('relation', '"route"="motorcycle"'),
    "peak":         ('node', '"natural"="peak"'),
    "park":         ('relation', '"boundary"="national_park"'),
    "viewpoint":    ('node', '"tourism"="viewpoint"'),
    "camp":         ('node', '"tourism"="camp_site"["fee"!="yes"]'),
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
    "waterfall": "Waterfall", "pool": "Natural Pool", "hiking": "Hike",
    "mtb": "MTB / Cycling", "motorbiking": "Motorbiking Route", "peak": "Mountain Peak",
    "park": "National Park", "viewpoint": "Viewpoint", "camp": "Free-Camping Site",
    "hut": "Hut", "cave": "Cave", "hot_spring": "Hot Spring", "lake": "Lake",
    "beach": "Beach", "gorge": "Adventure Gorge/Canyon", "meadow": "Meadow",
    "glacier": "Glacier", "volcano": "Volcano", "historic": "Historical Site (Ruins, Fort)",
    "unesco": "Unesco Heritage", "forest_walk": "Forest Walk", "monastery": "Old Monastery & Temple"
}

WATERFALL_QUERY_TMPL = """[out:json][timeout:{timeout}];
(
  node["lake"="waterfall"](around:{radius},{lat},{lng});
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


async def fetch_osm(
    lat: float,
    lng: float,
    radius_m: int,
    feature_ids: List[str],
    limit: int = 500,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch features from OSM Overpass API.
    Yields one result dict at a time.
    """
    timeout = 60

    for fid in feature_ids:

        # ── Union Queries ───────────────────────────────────────────────────
        if fid in ["waterfall", "pool", "historic", "unesco", "forest_walk", "monastery"]:
            tmpl_map = {
                "waterfall": WATERFALL_QUERY_TMPL,
                "pool": POOL_QUERY_TMPL,
                "historic": HISTORIC_QUERY_TMPL,
                "unesco": UNESCO_QUERY_TMPL,
                "forest_walk": FOREST_WALK_QUERY_TMPL,
                "monastery": MONASTERY_QUERY_TMPL,
            }
            query = tmpl_map[fid].format(
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
                        "type": FEATURE_LABELS.get(fid, fid.title()),
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

        # ── All other features: simple single-tag query ──────────────────────
        if fid not in FEATURE_TAGS:
            continue

        el_type, tag = FEATURE_TAGS[fid]
        label = FEATURE_LABELS.get(fid, fid)

        query = f"""[out:json][timeout:{timeout}];
(
  {el_type}[{tag}](around:{radius_m},{lat},{lng});
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

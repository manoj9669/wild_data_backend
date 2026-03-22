"""
Country-specific government data sources.
Each function is an async generator yielding result dicts.
"""

import httpx
from typing import AsyncGenerator, Dict, Any, List
from utils.rate_limiter import rate_limiter
from extractors.greece import fetch_greece

# ── USA — USGS + NPS ──────────────────────────────────────────────────────────
import os
NPS_API_KEY       = os.getenv("USA_NPS_KEY", "DEMO_KEY")
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME", "demo")
DATA_GOV_IN_KEY   = os.getenv("DATA_GOV_IN_KEY", "")

async def fetch_usa(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    USA: National Park Service API (free, no key needed for basic endpoints)
    + USGS Geographic Names Information System (GNIS)
    """
    # NPS — find parks near coordinates (bounding box to narrow results)
    import math as _math
    _deg = radius_km / 111.0
    _bbox = f"{lng-_deg},{lat-_deg},{lng+_deg},{lat+_deg}"

    try:
        await rate_limiter.wait("developer.nps.gov", 0.5)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://developer.nps.gov/api/v1/parks",
                params={
                    "limit": 50,
                    "start": 0,
                    "q": "",
                    "fields": "images,description,url",
                    "sort": "relevance",
                    "latLong": f"{lat},{lng}",
                    "radius": int(radius_km),
                },
                headers={"X-Api-Key": NPS_API_KEY},
            )
            if resp.status_code == 200:
                data = resp.json()
                parks = data.get("data", [])
                for park in parks:
                    try:
                        park_lat = float(park.get("latitude", 0) or 0)
                        park_lng = float(park.get("longitude", 0) or 0)
                        if park_lat == 0:
                            continue
                        # Check if within radius
                        import math
                        dlat = math.radians(park_lat - lat)
                        dlng = math.radians(park_lng - lng)
                        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(park_lat)) * math.sin(dlng/2)**2
                        dist = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
                        if dist > radius_km:
                            continue

                        images = park.get("images", [])
                        image_url = images[0].get("url", "") if images else ""

                        yield {
                            "name": park.get("fullName", park.get("name", "")),
                            "type": "National Park",
                            "type_id": "park",
                            "lat": park_lat,
                            "lng": park_lng,
                            "elevation": "",
                            "description": park.get("description", "")[:500],
                            "wikipedia": f"https://en.wikipedia.org/wiki/{park.get('fullName','').replace(' ', '_')}",
                            "website": park.get("url", ""),
                            "region": park.get("states", ""),
                            "country": "United States",
                            "image": image_url,
                            "osm_id": "",
                            "source": "NPS (USA)",
                            "confidence": "High",
                        }
                    except:
                        continue
    except Exception as e:
        print(f"[USA NPS] error: {e}")

    # GeoNames.org — mirrors USGS GNIS data, correct working endpoint
    # Free account required: geonames.org/login — set GEONAMES_USERNAME env var
    geonames_feature_map = {
        "waterfall":  ("H", "FLLS"),   # Hydrographic / Falls
        "peak":       ("T", "PK"),     # Mountain / Peak
        "cave":       ("H", "CAVE"),   # Hydrographic / Cave
        "beach":      ("H", "BCH"),    # Hydrographic / Beach
        "hot_spring": ("H", "SPNG"),   # Hydrographic / Spring
        "waterway":   ("H", "LK"),     # Hydrographic / Lake
        "park":       ("L", "PRK"),    # Area / Park
    }

    for fid in feature_ids:
        fc_pair = geonames_feature_map.get(fid)
        if not fc_pair:
            continue
        fc_class, fc_code = fc_pair
        try:
            await rate_limiter.wait("api.geonames.org", 1.0)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    "http://api.geonames.org/searchJSON",
                    params={
                        "featureClass": fc_class,
                        "featureCode":  fc_code,
                        "north": lat + radius_km / 111,
                        "south": lat - radius_km / 111,
                        "east":  lng + radius_km / 111,
                        "west":  lng - radius_km / 111,
                        "maxRows": 100,
                        "username": GEONAMES_USERNAME,
                    },
                )
                if resp.status_code != 200:
                    print(f"[USA GeoNames] HTTP {resp.status_code}: {resp.text[:100]}")
                    continue
                data = resp.json()
                import math
                for item in data.get("geonames", []):
                    try:
                        f_lat = float(item.get("lat", 0))
                        f_lng = float(item.get("lng", 0))
                    except (TypeError, ValueError):
                        continue
                    dlat = math.radians(f_lat - lat)
                    dlng_v = math.radians(f_lng - lng)
                    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng_v/2)**2
                    if 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) > radius_km:
                        continue
                    elev = item.get("elevation") or item.get("srtm3", "")
                    yield {
                        "name":        item.get("name", ""),
                        "type":        item.get("fcodeName", fid),
                        "type_id":     fid,
                        "lat":         f_lat,
                        "lng":         f_lng,
                        "elevation":   f"{elev}m" if elev else "",
                        "description": item.get("fcodeName", ""),
                        "wikipedia":   "",
                        "website":     "",
                        "region":      item.get("adminName1", ""),
                        "country":     "United States",
                        "image":       "",
                        "osm_id":      "",
                        "source":      "GeoNames/USGS GNIS (USA)",
                        "confidence":  "High",
                    }
        except Exception as e:
            print(f"[USA GeoNames] {fid} error: {e}")


# ── France — data.gouv.fr ─────────────────────────────────────────────────────

async def fetch_france(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    France: 
    1. Geoportail IGN API — official French geographic features (peaks, lakes, forests)
    2. French national parks hardcoded (stable govt data)
    3. data.gouv.fr protected areas
    """
    import math

    def in_radius(f_lat, f_lng):
        dlat = math.radians(f_lat - lat)
        dlng = math.radians(f_lng - lng)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

    # 1. EEA Natura2000 WFS — EU protected areas in France (free, no key)
    deg = radius_km / 111.0
    wfs_url = "https://bio.discomap.eea.europa.eu/arcgis/services/ProtectedSites/Natura2000Sites_WGS84/MapServer/WFSServer"
    try:
        await rate_limiter.wait("bio.discomap.eea.europa.eu", 1.0)
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.get(
                wfs_url,
                params={
                    "SERVICE":      "WFS",
                    "VERSION":      "2.0.0",
                    "REQUEST":      "GetFeature",
                    "TYPENAMES":    "ProtectedSites_Natura2000Sites_WGS84:Natura2000Sites",
                    "OUTPUTFORMAT": "application/json",
                    "CQL_FILTER":   (
                        f"BBOX(Shape,{lng-deg},{lat-deg},{lng+deg},{lat+deg},'EPSG:4326')"
                        f" AND MS_CODE LIKE 'FR%'"
                    ),
                    "COUNT": "50",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for feat in data.get("features", []):
                    props = feat.get("properties", {})
                    geom  = feat.get("geometry", {})
                    f_lat, f_lng = None, None
                    if geom.get("type") == "Point":
                        f_lng, f_lat = geom["coordinates"][:2]
                    elif geom.get("type") in ("Polygon", "MultiPolygon"):
                        coords = geom["coordinates"]
                        flat = coords[0][0] if geom["type"] == "Polygon" else coords[0][0][0]
                        f_lng, f_lat = flat[0], flat[1]
                    if f_lat is None or not in_radius(float(f_lat), float(f_lng)):
                        continue
                    site_type = props.get("MS_SITETYPE", "")
                    type_label = "Nature Reserve" if "B" in site_type else "National Park"
                    yield {
                        "name":        props.get("MS_NAME") or props.get("SITENAME", ""),
                        "type":        type_label,
                        "type_id":     "park",
                        "lat":         round(float(f_lat), 6),
                        "lng":         round(float(f_lng), 6),
                        "elevation":   "",
                        "description": f"Natura2000 protected site ({site_type}) — {props.get('MS_AREAHA','?')} ha",
                        "wikipedia":   "",
                        "website":     f"https://natura2000.eea.europa.eu/Natura2000/SDF/{props.get('MS_CODE','')}",
                        "region":      props.get("MS_CODE", "")[:4],
                        "country":     "France",
                        "image":       "",
                        "osm_id":      "",
                        "source":      "Natura2000/EEA (France)",
                        "confidence":  "High",
                    }
            else:
                print(f"[France Natura2000] HTTP {resp.status_code}")
    except Exception as e:
        print(f"[France Natura2000] error: {e}")

    # 2. French National Parks — hardcoded official list
    FRENCH_NATIONAL_PARKS = [
        {"name": "Vanoise National Park", "lat": 45.3833, "lng": 6.6667, "desc": "First national park of France, protecting the largest alpine area."},
        {"name": "Port-Cros National Park", "lat": 43.0000, "lng": 6.4000, "desc": "Marine national park in the Mediterranean."},
        {"name": "Pyrenees National Park", "lat": 42.8333, "lng": -0.1667, "desc": "Spectacular Pyrenean landscapes along the Spanish border."},
        {"name": "Cevennes National Park", "lat": 44.2167, "lng": 3.6667, "desc": "UNESCO World Heritage biosphere, granite mountains and wild rivers."},
        {"name": "Ecrins National Park", "lat": 44.9167, "lng": 6.3333, "desc": "Highest peaks in the French Alps outside Mont Blanc massif."},
        {"name": "Mercantour National Park", "lat": 44.1167, "lng": 7.0000, "desc": "Alpine park bordering Italy with wild wolves and ibex."},
        {"name": "Guadeloupe National Park", "lat": 16.1500, "lng": -61.7000, "desc": "Tropical rainforest and volcano La Soufrière."},
        {"name": "La Reunion National Park", "lat": -21.1000, "lng": 55.5000, "desc": "UNESCO World Heritage, active volcano Piton de la Fournaise."},
        {"name": "Calanques National Park", "lat": 43.2000, "lng": 5.5000, "desc": "Stunning limestone coastal cliffs near Marseille."},
        {"name": "Forets National Park", "lat": 47.8333, "lng": 4.8333, "desc": "France's newest national park, temperate broadleaf forests."},
    ]
    for park in FRENCH_NATIONAL_PARKS:
        if in_radius(park["lat"], park["lng"]):
            if not feature_ids or any(f in feature_ids for f in ("park", "national_park", "nature_reserve")):
                yield {
                    "name": park["name"],
                    "type": "National Park",
                    "lat": park["lat"],
                    "lng": park["lng"],
                    "elevation": "",
                    "description": park["desc"],
                    "wikipedia": f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
                    "website": "https://www.parcsnationaux.fr",
                    "region": "",
                    "country": "France",
                    "image": "",
                    "osm_id": "",
                    "source": "Parcs Nationaux de France (Govt)",
                    "confidence": "High",
                }


# ── UK — Ordnance Survey Open Data ───────────────────────────────────────────

OS_API_KEY = os.getenv("OS_API_KEY", "")

# OS Names API local type → WildData type mapping
OS_TYPE_MAP = {
    "waterfall":        ("Waterfall",      "waterfall"),
    "lake":             ("Lake",           "lake"),
    "loch":             ("Lake",           "lake"),
    "reservoir":        ("Lake",           "lake"),
    "mountain":         ("Peak",           "peak"),
    "hill":             ("Peak",           "peak"),
    "fell":             ("Peak",           "peak"),
    "ben":              ("Peak",           "peak"),
    "summit":           ("Peak",           "peak"),
    "forest":           ("Forest",         "forest"),
    "wood":             ("Forest",         "forest"),
    "national park":    ("National Park",  "national_park"),
    "country park":     ("Park",           "park"),
    "valley":           ("Valley",         "valley"),
    "glen":             ("Valley",         "valley"),
    "dale":             ("Valley",         "valley"),
    "gorge":            ("Canyon",         "canyon"),
    "cave":             ("Cave",           "cave"),
    "cliff":            ("Viewpoint",      "viewpoint"),
    "bay":              ("Beach",          "beach"),
    "beach":            ("Beach",          "beach"),
    "moor":             ("Moor",           "nature_reserve"),
    "heath":            ("Moor",           "nature_reserve"),
    "nature reserve":   ("Nature Reserve", "nature_reserve"),
    "river":            ("River",          "river"),
    "stream":           ("River",          "river"),
    "island":           ("Island",         "island"),
}

def _os_guess_type(local_type: str, name: str):
    """Map OS local type string to WildData type."""
    lt = (local_type or "").lower()
    nm = (name or "").lower()
    for key, val in OS_TYPE_MAP.items():
        if key in lt or key in nm:
            return val
    return ("Natural Feature", "natural")

def _os_within_radius(lat, lng, feat_lat, feat_lng, radius_km):
    import math
    R = 6371
    dlat = math.radians(feat_lat - lat)
    dlng = math.radians(feat_lng - lng)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(feat_lat)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

async def fetch_uk(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    UK: Ordnance Survey Names API — official UK geographic names database.
    Free tier: 1M transactions/month. Register at osdatahub.os.uk → set OS_API_KEY.

    Fixed bugs vs previous version:
    - 'bbox' renamed to 'bounds' (correct OS Names API parameter name)
    - LOCAL_TYPE values with spaces now correctly quoted in fq
    - Query terms per type now use real UK place name vocabulary
    """
    if not OS_API_KEY:
        print("[UK/OS] OS_API_KEY not set — skipping. Register free at osdatahub.os.uk")
        return

    deg = radius_km / 111.0
    # 'bounds' is the correct parameter name (not 'bbox') — WGS84: minLng,minLat,maxLng,maxLat
    bounds = f"{lng-deg},{lat-deg},{lng+deg},{lat+deg}"
    seen = set()

    # For each WildData feature_id: list of (OS_LOCAL_TYPE, [search_terms])
    # Search terms are real vocabulary that appears in UK place names of each type.
    # OS Names API searches term against feature names, fq filters by exact LOCAL_TYPE.
    OS_FEATURE_QUERIES: Dict[str, list] = {
        "waterfall": [
            ("Waterfall", ["waterfall", "falls", "force", "foss", "linn", "spout", "ghyll"]),
        ],
        "peak": [
            ("Mountain", ["mountain", "mount", "ben", "beinn", "carn", "cairn", "craig",
                          "creag", "meall", "sgurr", "stob", "binnein", "bidean"]),
            ("Hill",     ["hill", "tor", "knoll", "nab", "law", "down", "hump"]),
            ("Fell",     ["fell", "pike", "hause", "raise", "rigg", "crag"]),
            ("Summit",   ["summit", "top", "peak", "head"]),
        ],
        "waterway": [
            ("Lake",      ["lake", "mere", "water", "tarn", "pool", "llyn", "llwyn"]),
            ("Loch",      ["loch", "lochan"]),
            ("Reservoir", ["reservoir"]),
        ],
        "park": [
            ("National Park",                    ["park", "national", "dartmoor", "exmoor",
                                                  "snowdonia", "brecon", "cairngorm"]),
            ("Country Park",                     ["park", "country"]),
            ("Area Of Outstanding Natural Beauty", ["beauty", "outstanding", "aonb"]),
            ("Nature Reserve",                   ["reserve", "nature", "sanctuary"]),
            ("National Scenic Area",             ["scenic", "national"]),
        ],
        "forest": [
            ("Forest Or Woodland", ["forest", "wood", "woodland", "copse", "grove"]),
        ],
        "cave": [
            ("Cave", ["cave", "cavern", "hole", "pot", "grotto", "sinkhole"]),
        ],
        "beach": [
            ("Beach", ["beach", "sands", "strand"]),
            ("Bay",   ["bay", "cove", "inlet"]),
        ],
        "viewpoint": [
            ("Cliff", ["cliff", "crag", "scar", "edge", "bluff", "scarp"]),
        ],
        "hot_spring": [
            ("Other Hydrological Feature", ["spring", "well", "spa"]),
        ],
        "glacier": [
            ("Other Topographic Feature", ["glacier", "icefield", "neve"]),
        ],
    }

    # Only query feature types the user actually requested
    queries_to_run = []
    for fid in feature_ids:
        if fid in OS_FEATURE_QUERIES:
            for local_type, search_terms in OS_FEATURE_QUERIES[fid]:
                for term in search_terms:
                    queries_to_run.append((fid, local_type, term))

    async with httpx.AsyncClient(timeout=30) as client:
        for fid, local_type, term in queries_to_run:
            try:
                await rate_limiter.wait("api.os.uk", 0.5)
                resp = await client.get(
                    "https://api.os.uk/search/names/v1/find",
                    params={
                        "query":      term,
                        "fq":         f'LOCAL_TYPE:"{local_type}"',  # quoted for multi-word types
                        "bounds":     bounds,                         # correct param (was 'bbox')
                        "maxresults": 100,
                        "key":        OS_API_KEY,
                    },
                )
                if resp.status_code != 200:
                    print(f"[UK/OS] {local_type}/{term} → HTTP {resp.status_code}: {resp.text[:200]}")
                    continue

                data = resp.json()
                for feat in data.get("results", []):
                    g = feat.get("GAZETTEER_ENTRY", {})
                    name = g.get("NAME1", "") or g.get("NAME2", "")
                    if not name or name in seen:
                        continue
                    f_lat = g.get("LAT")
                    f_lng = g.get("LNG")
                    if f_lat is None or f_lng is None:
                        continue
                    try:
                        f_lat, f_lng = float(f_lat), float(f_lng)
                    except (TypeError, ValueError):
                        continue
                    if not (49 < f_lat < 62 and -9 < f_lng < 3):  # rough UK bounds check
                        continue
                    if not _os_within_radius(lat, lng, f_lat, f_lng, radius_km):
                        continue
                    seen.add(name)
                    type_label, type_id = _os_guess_type(local_type, name)
                    yield {
                        "name":        name,
                        "type":        type_label,
                        "type_id":     type_id,
                        "lat":         round(f_lat, 6),
                        "lng":         round(f_lng, 6),
                        "elevation":   "",
                        "region":      (g.get("DISTRICT_BOROUGH") or g.get("COUNTY_UNITARY")
                                        or g.get("COUNTY_UNITARY_BOROUGH") or ""),
                        "country":     "United Kingdom",
                        "description": local_type,
                        "wikipedia":   "",
                        "website":     "https://osdatahub.os.uk",
                        "image":       "",
                        "osm_id":      g.get("OS_ID", ""),
                        "source":      "Ordnance Survey (OS Names API)",
                        "confidence":  "High",
                    }
            except Exception as e:
                print(f"[UK/OS] {local_type}/{term} error: {e}")
                continue


# ── New Zealand — DOC API ─────────────────────────────────────────────────────

async def fetch_newzealand(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    New Zealand: Department of Conservation (DOC) API — completely free.
    Has every hut, trail, campsite, park in NZ wilderness.
    """
    DOC_BASE = "https://api.doc.govt.nz/v2"

    endpoint_map = {
        "hiking": "/tracks",
        "camp": "/campsites",
        "park": "/parks",
        "viewpoint": "/tracks",  # closest match
    }

    headers = {"x-api-key": ""}  # DOC API is fully open, no key needed

    for fid in feature_ids:
        ep = endpoint_map.get(fid)
        if not ep:
            continue

        try:
            await rate_limiter.wait("api.doc.govt.nz", 0.5)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{DOC_BASE}{ep}",
                    params={
                        "lat": lat,
                        "lon": lng,
                        "radius": radius_km,
                        "limit": 100,
                    },
                )
                if resp.status_code != 200:
                    continue
                items = resp.json()

            for item in (items if isinstance(items, list) else []):
                coords = item.get("location", {})
                item_lat = coords.get("lat", 0) or 0
                item_lng = coords.get("lon", 0) or coords.get("lng", 0) or 0
                if not item_lat:
                    continue

                yield {
                    "name": item.get("name", ""),
                    "type": {"hiking": "Hiking Route", "camp": "Campsite", "park": "National Park"}.get(fid, fid),
                    "type_id": fid,
                    "lat": item_lat,
                    "lng": item_lng,
                    "elevation": "",
                    "description": item.get("introductory", item.get("description", ""))[:500],
                    "wikipedia": "",
                    "website": f"https://www.doc.govt.nz{item.get('url', '')}",
                    "region": item.get("region", ""),
                    "country": "New Zealand",
                    "image": (item.get("images") or [{}])[0].get("url", "") if item.get("images") else "",
                    "osm_id": "",
                    "source": "DOC (New Zealand)",
                    "confidence": "High",
                }

        except Exception as e:
            print(f"[NZ DOC] {fid} error: {e}")


# ── Australia — Protected Planet + hardcoded parks ───────────────────────────

async def fetch_australia(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Australia: 
    1. Protected Planet API (IUCN) — free, no key, global protected areas
    2. Hardcoded major Australian national parks
    """
    import math

    def in_radius(f_lat, f_lng):
        dlat = math.radians(f_lat - lat)
        dlng = math.radians(f_lng - lng)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

    # 1. Protected Planet API — IUCN protected areas (free, no key for basic)
    try:
        await rate_limiter.wait("api.protectedplanet.net", 1.0)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.protectedplanet.net/v3/protected_areas/search",
                params={
                    "with_geometry": False,
                    "latitude": lat,
                    "longitude": lng,
                    "radius": int(radius_km),
                    "country": "AU",
                    "per_page": 50,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for area in data.get("protected_areas", []):
                    f_lat = area.get("centroid", {}).get("lat")
                    f_lng = area.get("centroid", {}).get("long")
                    if not f_lat or not f_lng:
                        continue
                    if not in_radius(float(f_lat), float(f_lng)):
                        continue
                    yield {
                        "name": area.get("name", ""),
                        "type": "Nature Reserve",
                        "lat": round(float(f_lat), 6),
                        "lng": round(float(f_lng), 6),
                        "elevation": "",
                        "description": f"IUCN Category {area.get('iucn_category', {}).get('name', '')} — {area.get('marine', 'Land')} protected area",
                        "wikipedia": "",
                        "website": f"https://www.protectedplanet.net/{area.get('wdpa_id', '')}",
                        "region": area.get("sub_location", ""),
                        "country": "Australia",
                        "image": "",
                        "osm_id": "",
                        "source": "Protected Planet/IUCN (AU)",
                        "confidence": "High",
                    }
            else:
                print(f"[AU ProtectedPlanet] HTTP {resp.status_code}")
    except Exception as e:
        print(f"[AU ProtectedPlanet] error: {e}")

    # 2. Major Australian National Parks — hardcoded official list
    AU_NATIONAL_PARKS = [
        {"name": "Kakadu National Park", "lat": -12.9252, "lng": 132.4196, "desc": "Australia's largest national park, UNESCO World Heritage."},
        {"name": "Blue Mountains National Park", "lat": -33.6333, "lng": 150.3000, "desc": "Dramatic sandstone escarpments, waterfalls and eucalypt forests."},
        {"name": "Great Barrier Reef Marine Park", "lat": -18.2861, "lng": 147.7000, "desc": "World's largest coral reef system, UNESCO World Heritage."},
        {"name": "Uluru-Kata Tjuta National Park", "lat": -25.3444, "lng": 131.0369, "desc": "Sacred Aboriginal site, iconic red sandstone monolith."},
        {"name": "Daintree National Park", "lat": -16.1700, "lng": 145.4200, "desc": "World's oldest tropical rainforest, UNESCO World Heritage."},
        {"name": "Flinders Ranges National Park", "lat": -31.9167, "lng": 138.6833, "desc": "Ancient mountain ranges, Aboriginal rock art, unique wildlife."},
        {"name": "Cradle Mountain National Park", "lat": -41.6500, "lng": 145.9333, "desc": "Iconic Tasmanian wilderness, glacial lakes and alpine heathlands."},
        {"name": "Purnululu National Park", "lat": -17.5000, "lng": 128.4000, "desc": "Beehive-shaped Bungle Bungle sandstone formations."},
        {"name": "Wilsons Promontory National Park", "lat": -39.0833, "lng": 146.3667, "desc": "Southernmost point of mainland Australia, pristine beaches."},
        {"name": "Lamington National Park", "lat": -28.2167, "lng": 153.1500, "desc": "Ancient volcanic rim, subtropical rainforest, World Heritage."},
        {"name": "Shark Bay Marine Park", "lat": -25.9667, "lng": 113.8500, "desc": "UNESCO World Heritage, stromatolites and dugong sanctuary."},
        {"name": "Namadgi National Park", "lat": -35.5667, "lng": 148.7833, "desc": "Alpine wilderness adjoining Kosciuszko National Park."},
    ]
    for park in AU_NATIONAL_PARKS:
        if in_radius(park["lat"], park["lng"]):
            if not feature_ids or any(f in feature_ids for f in ("park", "national_park", "nature_reserve")):
                yield {
                    "name": park["name"],
                    "type": "National Park",
                    "lat": park["lat"],
                    "lng": park["lng"],
                    "elevation": "",
                    "description": park["desc"],
                    "wikipedia": f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
                    "website": "https://www.environment.gov.au/land/nrs/national-parks",
                    "region": "",
                    "country": "Australia",
                    "image": "",
                    "osm_id": "",
                    "source": "Parks Australia (Govt)",
                    "confidence": "High",
                }


# ── Japan — GSI Feature Search + National Parks ───────────────────────────────

async def fetch_japan(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Japan:
    1. GSI (Geospatial Information Authority) place name search
    2. Hardcoded Japan National Parks (Ministry of Environment official list)
    """
    import math

    def in_radius(f_lat, f_lng):
        dlat = math.radians(f_lat - lat)
        dlng = math.radians(f_lng - lng)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

    # 1. GSI Place name search — Japanese govt topo feature names
    GSI_FEATURE_TYPES = ["滝", "山", "湖", "峠", "渓谷", "海岸", "岬", "洞窟"]  # falls, mountain, lake, pass, valley, coast, cape, cave
    GSI_TYPE_MAP = {"滝": "Waterfall", "山": "Peak", "湖": "Lake", "峠": "Pass", "渓谷": "Valley", "海岸": "Beach", "岬": "Viewpoint", "洞窟": "Cave"}

    try:
        await rate_limiter.wait("msearch.gsi.go.jp", 0.5)
        async with httpx.AsyncClient(timeout=20) as client:
            deg = radius_km / 111.0
            for jp_type in GSI_FEATURE_TYPES:
                resp = await client.get(
                    "https://msearch.gsi.go.jp/address-search/AddressSearch",
                    params={
                        "q": jp_type,
                        "lon": lng,
                        "lat": lat,
                        "distance": radius_km * 1000,
                    },
                )
                if resp.status_code != 200:
                    continue
                items = resp.json()
                for item in (items if isinstance(items, list) else []):
                    props = item.get("properties", {})
                    geom = item.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if len(coords) < 2:
                        continue
                    f_lng, f_lat = float(coords[0]), float(coords[1])
                    if not in_radius(f_lat, f_lng):
                        continue
                    name = props.get("title", "")
                    if not name:
                        continue
                    yield {
                        "name": name,
                        "type": GSI_TYPE_MAP.get(jp_type, "Natural Feature"),
                        "lat": round(f_lat, 6),
                        "lng": round(f_lng, 6),
                        "elevation": "",
                        "description": f"GSI official feature — {jp_type}",
                        "wikipedia": "",
                        "website": "https://maps.gsi.go.jp",
                        "region": props.get("addressCode", "")[:2],
                        "country": "Japan",
                        "image": "",
                        "osm_id": "",
                        "source": "GSI Japan (Govt)",
                        "confidence": "High",
                    }
                await rate_limiter.wait("msearch.gsi.go.jp", 0.5)
    except Exception as e:
        print(f"[Japan GSI search] error: {e}")

    # 2. Japan National Parks — Ministry of Environment official list
    JAPAN_NATIONAL_PARKS = [
        {"name": "Daisetsuzan National Park", "lat": 43.6667, "lng": 142.8333, "desc": "Japan's largest national park, volcanic peaks and hot springs."},
        {"name": "Shiretoko National Park", "lat": 44.1000, "lng": 145.0000, "desc": "UNESCO World Heritage, remote peninsula with bears and eagles."},
        {"name": "Akan-Mashu National Park", "lat": 43.4500, "lng": 144.3500, "desc": "Volcanic calderas, rare marimo algae, Lake Mashu."},
        {"name": "Nikko National Park", "lat": 36.7500, "lng": 139.5000, "desc": "Ancient shrines, waterfalls, alpine lakes and volcanic peaks."},
        {"name": "Joshinetsu Kogen National Park", "lat": 36.7000, "lng": 138.5000, "desc": "Volcanic plateau, skiing, alpine flora."},
        {"name": "Fuji-Hakone-Izu National Park", "lat": 35.3607, "lng": 138.7274, "desc": "Mount Fuji, hot springs, Izu Peninsula coastline."},
        {"name": "Chubu Sangaku National Park", "lat": 36.3000, "lng": 137.6500, "desc": "Japanese Alps — Tateyama, Hotaka, dramatic mountain scenery."},
        {"name": "Yoshino-Kumano National Park", "lat": 34.0000, "lng": 135.8333, "desc": "UNESCO World Heritage pilgrimage routes through ancient forests."},
        {"name": "San-in Kaigan National Park", "lat": 35.6333, "lng": 134.8000, "desc": "Rugged Sea of Japan coastline, sea caves and rock formations."},
        {"name": "Daisen-Oki National Park", "lat": 35.3667, "lng": 133.5333, "desc": "Volcanic Mount Daisen and remote Oki Islands."},
        {"name": "Setonaikai National Park", "lat": 34.2833, "lng": 133.5000, "desc": "Japan's first national park, scenic island-dotted inland sea."},
        {"name": "Aso-Kuju National Park", "lat": 32.8833, "lng": 131.1000, "desc": "World's largest volcanic caldera, active Mount Aso."},
        {"name": "Kirishima-Kinkowan National Park", "lat": 31.9333, "lng": 130.8667, "desc": "Volcanic mountains, crater lakes, and Kagoshima Bay."},
        {"name": "Yakushima National Park", "lat": 30.3500, "lng": 130.5333, "desc": "UNESCO World Heritage, ancient cedar forests, Jomon Sugi tree."},
        {"name": "Iriomote-Ishigaki National Park", "lat": 24.3500, "lng": 123.8000, "desc": "Tropical jungle, mangroves and coral reefs of the Yaeyama Islands."},
    ]
    for park in JAPAN_NATIONAL_PARKS:
        if in_radius(park["lat"], park["lng"]):
            if not feature_ids or any(f in feature_ids for f in ("park", "national_park", "nature_reserve")):
                yield {
                    "name": park["name"],
                    "type": "National Park",
                    "lat": park["lat"],
                    "lng": park["lng"],
                    "elevation": "",
                    "description": park["desc"],
                    "wikipedia": f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
                    "website": "https://www.env.go.jp/nature/np/",
                    "region": "",
                    "country": "Japan",
                    "image": "",
                    "osm_id": "",
                    "source": "Ministry of Environment Japan (Govt)",
                    "confidence": "High",
                }


# ── India — Bhuvan + data.gov.in ─────────────────────────────────────────────

async def fetch_india(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    India: Bhuvan NRSC + data.gov.in + Wikipedia GeoSearch.
    Wikidata is primary for India (OSM is sparse).
    """
    # India Wikipedia GeoSearch — best source for India
    try:
        await rate_limiter.wait("en.wikipedia.org", 0.5)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "geosearch",
                    "gscoord": f"{lat}|{lng}",
                    "gsradius": min(radius_km * 1000, 10000),
                    "gslimit": 50,
                    "format": "json",
                    "origin": "*",
                },
            )
            data = resp.json()
            india_keywords = [
                'falls', 'waterfall', 'peak', 'pass', 'trek', 'trail',
                'lake', 'river', 'valley', 'forest', 'reserve', 'sanctuary',
                'national park', 'wildlife', 'beach', 'cave', 'temple',
                'kund', 'tal', 'dhar', 'ghati', 'nala', 'jharna',
            ]
            for page in data.get("query", {}).get("geosearch", []):
                title = page.get("title", "")
                if any(kw in title.lower() for kw in india_keywords):
                    from extractors.wikipedia import guess_type
                    type_label, type_id = guess_type(title)
                    yield {
                        "name": title,
                        "type": type_label,
                        "type_id": type_id,
                        "lat": page.get("lat", 0),
                        "lng": page.get("lon", 0),
                        "elevation": "",
                        "description": "",
                        "wikipedia": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                        "website": "",
                        "region": "",
                        "country": "India",
                        "image": "",
                        "osm_id": "",
                        "source": "Wikipedia (India)",
                        "confidence": "High",
                    }
    except Exception as e:
        print(f"[India Wikipedia] error: {e}")

    # data.gov.in — Protected Areas (requires DATA_GOV_IN_KEY env var)
    if "park" in feature_ids and DATA_GOV_IN_KEY:
        try:
            await rate_limiter.wait("api.data.gov.in", 1.0)
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://api.data.gov.in/resource/f1a8c4ca-186e-4b46-be9e-ae32fd54e9fa",
                    params={
                        "api-key": DATA_GOV_IN_KEY,
                        "format": "json",
                        "limit": 100,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for record in data.get("records", []):
                        try:
                            r_lat = float(record.get("latitude", 0) or 0)
                            r_lng = float(record.get("longitude", 0) or 0)
                            if r_lat == 0:
                                continue
                            import math
                            dlat = math.radians(r_lat - lat)
                            dlng_r = math.radians(r_lng - lng)
                            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(r_lat)) * math.sin(dlng_r/2)**2
                            dist = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
                            if dist > radius_km:
                                continue
                            yield {
                                "name": record.get("name", ""),
                                "type": "National Park",
                                "type_id": "park",
                                "lat": r_lat,
                                "lng": r_lng,
                                "elevation": "",
                                "description": f"Protected area in {record.get('state', 'India')}",
                                "wikipedia": "",
                                "website": "https://data.gov.in",
                                "region": record.get("state", ""),
                                "country": "India",
                                "image": "",
                                "osm_id": "",
                                "source": "data.gov.in (India)",
                                "confidence": "High",
                            }
                        except:
                            continue
        except Exception as e:
            print(f"[India data.gov.in] error: {e}")


# ── Norway — Kartverket (Norwegian Mapping Authority) ─────────────────────────

async def fetch_norway(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Norway: Kartverket SSR (Sentralt stedsnavnregister) — official Norwegian
    place name register. Free, no key required.
    Covers: peaks, waterfalls, lakes, glaciers, valleys, passes, caves.
    """
    import math

    def in_radius(f_lat, f_lng):
        dlat = math.radians(f_lat - lat)
        dlng = math.radians(f_lng - lng)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

    deg = radius_km / 111.0

    # Kartverket SSR name type codes → WildData feature IDs
    SSR_TYPES = {
        "waterfall":  ["Foss", "Stryk"],
        "peak":       ["Fjell", "Topp", "Nut", "Tind", "Horn"],
        "waterway":   ["Innsjø", "Vatn", "Tjern", "Elv"],
        "glacier":    ["Bre", "Isbre", "Jøkel"],
        "cave":       ["Grotte", "Hule"],
        "viewpoint":  ["Utsiktspunkt"],
        "camp":       ["Camping"],
        "hot_spring": ["Kilde"],
    }

    for fid in feature_ids:
        search_terms = SSR_TYPES.get(fid)
        if not search_terms:
            continue

        for term in search_terms:
            try:
                await rate_limiter.wait("ws.geonorge.no", 0.5)
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(
                        "https://ws.geonorge.no/stedsnavn/v1/sted",
                        params={
                            "sok":         f"*{term}*",
                            "utkoordsys":  4258,    # WGS84
                            "treffPerSide": 50,
                            "side":        0,
                            "nord":  lat + deg,
                            "sor":   lat - deg,
                            "aust":  lng + deg,
                            "vest":  lng - deg,
                        },
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()

                for place in data.get("navn", []):
                    coords = (place.get("representasjonspunkt") or {})
                    f_lat = coords.get("nord") or coords.get("lat")
                    f_lng = coords.get("ost") or coords.get("lon")
                    if not f_lat or not f_lng:
                        continue
                    try:
                        f_lat, f_lng = float(f_lat), float(f_lng)
                    except (TypeError, ValueError):
                        continue
                    if not in_radius(f_lat, f_lng):
                        continue

                    name = place.get("stedsnavn", [{}])[0].get("skrivemåte", "") if place.get("stedsnavn") else place.get("skrivemåte", "")
                    if not name:
                        continue

                    type_label = {
                        "waterfall": "Waterfall", "peak": "Mountain Peak",
                        "waterway": "Lake / River", "glacier": "Glacier",
                        "cave": "Cave", "viewpoint": "Viewpoint",
                        "camp": "Campsite", "hot_spring": "Hot Spring",
                    }.get(fid, "Natural Feature")

                    yield {
                        "name": name,
                        "type": type_label,
                        "type_id": fid,
                        "lat": round(f_lat, 6),
                        "lng": round(f_lng, 6),
                        "elevation": "",
                        "description": f"Norwegian official place name — {place.get('navneobjekttype', term)}",
                        "wikipedia": "",
                        "website": "https://kartverket.no",
                        "region": (place.get("kommuner") or [{}])[0].get("kommunenavn", "") if place.get("kommuner") else "",
                        "country": "Norway",
                        "image": "",
                        "osm_id": "",
                        "source": "Kartverket SSR (Norway)",
                        "confidence": "High",
                    }

            except Exception as e:
                print(f"[Norway Kartverket] {fid}/{term} error: {e}")
                continue


# ── Canada — Parks Canada + hardcoded major parks ─────────────────────────────

async def fetch_canada(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Canada: Parks Canada open data API + hardcoded national parks list.
    """
    import math

    def in_radius(f_lat, f_lng):
        dlat = math.radians(f_lat - lat)
        dlng = math.radians(f_lng - lng)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

    if not any(f in feature_ids for f in ("park", "hiking", "camp")):
        return

    # Parks Canada open data — heritage places dataset (CKAN API)
    try:
        await rate_limiter.wait("open.canada.ca", 1.0)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://open.canada.ca/data/api/3/action/datastore_search",
                params={
                    "resource_id": "a0f47b06-3ccc-421c-b42c-fc9c5cbd7d0d",
                    "limit": 100,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for record in (data.get("result", {}).get("records", [])):
                    try:
                        r_lat = float(record.get("LATITUDE") or record.get("latitude") or 0)
                        r_lng = float(record.get("LONGITUDE") or record.get("longitude") or 0)
                    except (TypeError, ValueError):
                        continue
                    if r_lat == 0 or not in_radius(r_lat, r_lng):
                        continue
                    name = record.get("NAME_E") or record.get("name", "")
                    if not name:
                        continue
                    yield {
                        "name": name,
                        "type": "National Park",
                        "type_id": "park",
                        "lat": r_lat,
                        "lng": r_lng,
                        "elevation": "",
                        "description": record.get("DESCRIPTION_E", ""),
                        "wikipedia": f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}",
                        "website": "https://www.pc.gc.ca",
                        "region": record.get("PROVINCE_E", ""),
                        "country": "Canada",
                        "image": "",
                        "osm_id": "",
                        "source": "Parks Canada (Open Data)",
                        "confidence": "High",
                    }
    except Exception as e:
        print(f"[Canada Parks] open data error: {e}")

    # Hardcoded Canadian National Parks
    CANADA_PARKS = [
        {"name": "Banff National Park", "lat": 51.4968, "lng": -115.9281, "desc": "Canada's oldest national park, Rocky Mountain scenery, hot springs."},
        {"name": "Jasper National Park", "lat": 52.8734, "lng": -117.9543, "desc": "Canada's largest Rocky Mountain park, Columbia Icefield, dark sky preserve."},
        {"name": "Yoho National Park", "lat": 51.5, "lng": -116.5, "desc": "Dramatic waterfalls, Burgess Shale fossils, emerald lakes."},
        {"name": "Kootenay National Park", "lat": 50.6, "lng": -116.0, "desc": "Hot springs, painted pots, canyon hikes in the Rockies."},
        {"name": "Pacific Rim National Park Reserve", "lat": 48.99, "lng": -125.49, "desc": "Wild Pacific coastline, rainforest, surfing beaches on Vancouver Island."},
        {"name": "Gwaii Haanas National Park Reserve", "lat": 52.5, "lng": -131.5, "desc": "Remote Haida Gwaii archipelago, ancient Haida culture, wildlife."},
        {"name": "Gros Morne National Park", "lat": 49.5, "lng": -57.8, "desc": "UNESCO World Heritage, fiords, tablelands, and coastal wilderness."},
        {"name": "Algonquin Provincial Park", "lat": 45.5, "lng": -78.3, "desc": "Ontario's iconic canoe country, moose, wolves, ancient forests."},
        {"name": "Cape Breton Highlands National Park", "lat": 46.75, "lng": -60.75, "desc": "Dramatic coastal highlands, Cabot Trail, bald eagles."},
        {"name": "Kluane National Park", "lat": 61.0, "lng": -138.5, "desc": "UNESCO World Heritage, largest non-polar icefields, Dall sheep."},
        {"name": "Wood Buffalo National Park", "lat": 59.5, "lng": -113.0, "desc": "UNESCO World Heritage, world's largest national park, bison herds."},
        {"name": "Nahanni National Park Reserve", "lat": 61.0, "lng": -125.0, "desc": "UNESCO World Heritage, Virginia Falls, wild canyon wilderness."},
    ]
    for park in CANADA_PARKS:
        if in_radius(park["lat"], park["lng"]):
            if any(f in feature_ids for f in ("park",)):
                yield {
                    "name": park["name"], "type": "National Park", "type_id": "park",
                    "lat": park["lat"], "lng": park["lng"], "elevation": "",
                    "description": park["desc"],
                    "wikipedia": f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
                    "website": "https://www.pc.gc.ca", "region": "", "country": "Canada",
                    "image": "", "osm_id": "", "source": "Parks Canada (Govt)", "confidence": "High",
                }


# ── Spain — IGN España + hardcoded protected areas ───────────────────────────

async def fetch_spain(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Spain: IGN España IDEE WFS (geographic names) + hardcoded national parks.
    """
    import math

    def in_radius(f_lat, f_lng):
        dlat = math.radians(f_lat - lat)
        dlng = math.radians(f_lng - lng)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

    deg = radius_km / 111.0

    # IGN España NGBE WFS — Named Places (Nomenclátor Geográfico Básico de España)
    # Free, no key — official Spanish geographic names
    if any(f in feature_ids for f in ("peak", "waterfall", "waterway", "cave", "beach")):
        try:
            await rate_limiter.wait("www.ign.es", 1.0)
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.get(
                    "https://www.ign.es/wfs-inspire/ngbe",
                    params={
                        "SERVICE":      "WFS",
                        "VERSION":      "2.0.0",
                        "REQUEST":      "GetFeature",
                        "TYPENAMES":    "gn:NamedPlace",
                        "OUTPUTFORMAT": "application/json",
                        "BBOX":         f"{lat-deg},{lng-deg},{lat+deg},{lng+deg},urn:ogc:def:crs:EPSG::4326",
                        "COUNT":        100,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for feat in data.get("features", []):
                        props = feat.get("properties", {})
                        geom  = feat.get("geometry", {})
                        if geom.get("type") == "Point":
                            f_lng_v, f_lat_v = geom["coordinates"][:2]
                        elif geom.get("type") == "MultiPoint":
                            f_lng_v, f_lat_v = geom["coordinates"][0][:2]
                        else:
                            continue
                        try:
                            f_lat_v, f_lng_v = float(f_lat_v), float(f_lng_v)
                        except (TypeError, ValueError):
                            continue
                        if not in_radius(f_lat_v, f_lng_v):
                            continue
                        name = props.get("name", "") or props.get("text", "")
                        if not name:
                            continue
                        feat_type = (props.get("featureType") or props.get("placeType") or "").lower()
                        if "peak" in feat_type or "summit" in feat_type or "mountain" in feat_type:
                            type_label, type_id = "Mountain Peak", "peak"
                        elif "waterfall" in feat_type or "fall" in feat_type:
                            type_label, type_id = "Waterfall", "waterfall"
                        elif "lake" in feat_type or "river" in feat_type or "water" in feat_type:
                            type_label, type_id = "Lake / River", "waterway"
                        elif "cave" in feat_type or "grotto" in feat_type:
                            type_label, type_id = "Cave", "cave"
                        elif "beach" in feat_type or "coast" in feat_type:
                            type_label, type_id = "Beach", "beach"
                        else:
                            type_label, type_id = "Natural Feature", "viewpoint"

                        if type_id not in feature_ids:
                            continue

                        yield {
                            "name": name, "type": type_label, "type_id": type_id,
                            "lat": round(f_lat_v, 6), "lng": round(f_lng_v, 6), "elevation": "",
                            "description": props.get("placeType", ""),
                            "wikipedia": "", "website": "https://www.ign.es",
                            "region": props.get("municipality", ""), "country": "Spain",
                            "image": "", "osm_id": "",
                            "source": "IGN España (NGBE)", "confidence": "High",
                        }
                else:
                    print(f"[Spain IGN] HTTP {resp.status_code}")
        except Exception as e:
            print(f"[Spain IGN] error: {e}")

    # Hardcoded Spanish National Parks
    SPAIN_PARKS = [
        {"name": "Teide National Park", "lat": 28.2726, "lng": -16.6421, "desc": "UNESCO World Heritage, highest peak in Spain — Mount Teide volcano."},
        {"name": "Garajonay National Park", "lat": 28.1167, "lng": -17.2333, "desc": "UNESCO World Heritage, ancient laurel forest on La Gomera."},
        {"name": "Caldera de Taburiente National Park", "lat": 28.7167, "lng": -17.8833, "desc": "Giant volcanic crater on La Palma, deep ravines and pine forests."},
        {"name": "Timanfaya National Park", "lat": 29.0, "lng": -13.75, "desc": "Volcanic moonscape on Lanzarote, still geothermally active."},
        {"name": "Ordesa y Monte Perdido", "lat": 42.6333, "lng": -0.05, "desc": "Pyrenean canyon, UNESCO World Heritage, chamois and bearded vultures."},
        {"name": "Aigüestortes i Estany de Sant Maurici", "lat": 42.5667, "lng": 1.0, "desc": "Pyrenean lakes and glacial valleys, unique twisted streams."},
        {"name": "Sierra Nevada National Park", "lat": 37.05, "lng": -3.3167, "desc": "Highest peak in mainland Spain — Mulhacén 3479m."},
        {"name": "Doñana National Park", "lat": 36.9333, "lng": -6.4333, "desc": "UNESCO World Heritage, critical Iberian lynx and flamingo habitat."},
        {"name": "Sierra de Guadarrama National Park", "lat": 40.85, "lng": -3.9667, "desc": "Mountain range north of Madrid, pine forests and granite peaks."},
        {"name": "Picos de Europa National Park", "lat": 43.2, "lng": -4.9, "desc": "Dramatic limestone massif, bears, wolves and famous gorges."},
        {"name": "Cabañeros National Park", "lat": 39.4, "lng": -4.4, "desc": "Mediterranean scrubland, large herds of deer, imperial eagles."},
        {"name": "Monfragüe National Park", "lat": 39.8333, "lng": -5.9, "desc": "Raptor paradise — black vultures, Spanish imperial eagles, lynx."},
        {"name": "Tablas de Daimiel National Park", "lat": 39.1333, "lng": -3.7, "desc": "Wetland in La Mancha, migratory waterbirds."},
        {"name": "Sierra de las Nieves National Park", "lat": 36.7, "lng": -4.95, "desc": "Newest national park, pinsapo fir forests and limestone karst."},
    ]
    for park in SPAIN_PARKS:
        if in_radius(park["lat"], park["lng"]) and "park" in feature_ids:
            yield {
                "name": park["name"], "type": "National Park", "type_id": "park",
                "lat": park["lat"], "lng": park["lng"], "elevation": "",
                "description": park["desc"],
                "wikipedia": f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
                "website": "https://www.miteco.gob.es/es/red-parques-nacionales/",
                "region": "", "country": "Spain", "image": "", "osm_id": "",
                "source": "Red de Parques Nacionales (Spain Govt)", "confidence": "High",
            }


# ── Brazil — ICMBio + IBGE national parks ─────────────────────────────────────

async def fetch_brazil(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Brazil: ICMBio API (protected areas) + hardcoded major national parks.
    """
    import math

    def in_radius(f_lat, f_lng):
        dlat = math.radians(f_lat - lat)
        dlng = math.radians(f_lng - lng)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

    # ICMBio CNUC API — National Conservation Units registry
    if any(f in feature_ids for f in ("park", "forest")):
        try:
            await rate_limiter.wait("sistemas.mma.gov.br", 1.0)
            deg = radius_km / 111.0
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://sistemas.mma.gov.br/cnuc/api/v1/uc",
                    params={
                        "bbox": f"{lng-deg},{lat-deg},{lng+deg},{lat+deg}",
                        "categoria": "PI",   # Parques de proteção integral
                        "limit": 50,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for area in data.get("features", data if isinstance(data, list) else []):
                        props = (area.get("properties", {}) if isinstance(area, dict) and "properties" in area else area)
                        geom = area.get("geometry", {}) if isinstance(area, dict) else {}
                        if geom.get("type") == "Point":
                            f_lng_v, f_lat_v = geom["coordinates"][:2]
                        else:
                            f_lat_v = props.get("latitude") or props.get("lat_centroide")
                            f_lng_v = props.get("longitude") or props.get("lon_centroide")
                        if not f_lat_v or not f_lng_v:
                            continue
                        try:
                            f_lat_v, f_lng_v = float(f_lat_v), float(f_lng_v)
                        except (TypeError, ValueError):
                            continue
                        if not in_radius(f_lat_v, f_lng_v):
                            continue
                        name = props.get("nome_uc") or props.get("nome", "")
                        if not name:
                            continue
                        yield {
                            "name": name, "type": "National Park", "type_id": "park",
                            "lat": round(f_lat_v, 6), "lng": round(f_lng_v, 6), "elevation": "",
                            "description": f"Brazilian Conservation Unit — {props.get('categoria_uc', '')}",
                            "wikipedia": "", "website": "https://www.icmbio.gov.br",
                            "region": props.get("uf_uc", ""), "country": "Brazil",
                            "image": "", "osm_id": "",
                            "source": "ICMBio / CNUC (Brazil)", "confidence": "High",
                        }
        except Exception as e:
            print(f"[Brazil ICMBio] error: {e}")

    BRAZIL_PARKS = [
        {"name": "Amazon National Park", "lat": -4.5, "lng": -56.5, "desc": "Largest national park in Brazil, heart of the Amazon rainforest."},
        {"name": "Iguaçu National Park", "lat": -25.695, "lng": -54.436, "desc": "UNESCO World Heritage, world's largest waterfall system."},
        {"name": "Fernando de Noronha", "lat": -3.855, "lng": -32.423, "desc": "UNESCO World Heritage, pristine marine reserve and diving paradise."},
        {"name": "Chapada Diamantina National Park", "lat": -12.5, "lng": -41.5, "desc": "Dramatic tablelands, waterfalls, caves and rivers in Bahia."},
        {"name": "Chapada dos Veadeiros National Park", "lat": -14.0, "lng": -47.5, "desc": "UNESCO World Heritage, cerrado savanna, quartz crystal formations."},
        {"name": "Lençóis Maranhenses National Park", "lat": -2.5, "lng": -43.0, "desc": "White sand dunes with seasonal turquoise lagoons."},
        {"name": "Pantanal Matogrossense National Park", "lat": -17.8, "lng": -57.5, "desc": "UNESCO World Heritage, world's largest tropical wetland."},
        {"name": "Serra da Capivara National Park", "lat": -8.5, "lng": -42.5, "desc": "UNESCO World Heritage, prehistoric rock art spanning 50,000 years."},
        {"name": "Tijuca National Park", "lat": -22.95, "lng": -43.28, "desc": "World's largest urban forest, within Rio de Janeiro."},
        {"name": "Jaú National Park", "lat": -2.0, "lng": -62.0, "desc": "UNESCO World Heritage, black-water rivers and flooded forest."},
    ]
    for park in BRAZIL_PARKS:
        if in_radius(park["lat"], park["lng"]) and "park" in feature_ids:
            yield {
                "name": park["name"], "type": "National Park", "type_id": "park",
                "lat": park["lat"], "lng": park["lng"], "elevation": "",
                "description": park["desc"],
                "wikipedia": f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
                "website": "https://www.icmbio.gov.br", "region": "", "country": "Brazil",
                "image": "", "osm_id": "", "source": "ICMBio (Brazil Govt)", "confidence": "High",
            }


# ── South Africa — SANParks ───────────────────────────────────────────────────

async def fetch_south_africa(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    South Africa: SANParks national parks + SANBI protected areas.
    """
    import math

    def in_radius(f_lat, f_lng):
        dlat = math.radians(f_lat - lat)
        dlng = math.radians(f_lng - lng)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(f_lat)) * math.sin(dlng/2)**2
        return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km

    SA_PARKS = [
        {"name": "Kruger National Park", "lat": -23.989, "lng": 31.554, "desc": "South Africa's flagship game reserve, Big Five wildlife."},
        {"name": "Table Mountain National Park", "lat": -34.0, "lng": 18.4, "desc": "UNESCO World Heritage, iconic flat-topped mountain above Cape Town."},
        {"name": "Kgalagadi Transfrontier Park", "lat": -26.0, "lng": 20.5, "desc": "Vast semi-desert, black-maned Kalahari lions and raptors."},
        {"name": "iSimangaliso Wetland Park", "lat": -28.0, "lng": 32.5, "desc": "UNESCO World Heritage, estuary, coral reefs, hippos, crocs."},
        {"name": "Drakensberg (uKhahlamba) Park", "lat": -29.5, "lng": 29.2, "desc": "UNESCO World Heritage, dramatic Basotho Highlands escarpment."},
        {"name": "Garden Route National Park", "lat": -33.9, "lng": 22.6, "desc": "Forests, lakes, beaches and whales along the southern Cape coast."},
        {"name": "Richtersveld National Park", "lat": -28.5, "lng": 17.0, "desc": "UNESCO World Heritage, desert mountain wilderness, succulent plants."},
        {"name": "Addo Elephant National Park", "lat": -33.5, "lng": 25.75, "desc": "Dense elephant herds, Big Seven including sharks and whales."},
        {"name": "Mapungubwe National Park", "lat": -22.2, "lng": 29.3, "desc": "UNESCO World Heritage, Iron Age kingdom ruins, baobabs, elephants."},
        {"name": "Agulhas National Park", "lat": -34.8, "lng": 20.0, "desc": "Southernmost tip of Africa, rocky reefs, migrating whales."},
        {"name": "Camdeboo National Park", "lat": -32.25, "lng": 24.5, "desc": "Karoo landscape, Valley of Desolation, black eagles."},
        {"name": "Golden Gate Highlands National Park", "lat": -28.5, "lng": 28.6, "desc": "Sandstone cliffs glowing gold at sunset, bearded vultures."},
    ]

    for park in SA_PARKS:
        if in_radius(park["lat"], park["lng"]) and any(f in feature_ids for f in ("park",)):
            yield {
                "name": park["name"], "type": "National Park", "type_id": "park",
                "lat": park["lat"], "lng": park["lng"], "elevation": "",
                "description": park["desc"],
                "wikipedia": f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
                "website": "https://www.sanparks.org", "region": "", "country": "South Africa",
                "image": "", "osm_id": "", "source": "SANParks (South Africa Govt)", "confidence": "High",
            }


# ── Dispatcher ────────────────────────────────────────────────────────────────

# Country code → fetch function
COUNTRY_EXTRACTORS = {
    "US": fetch_usa,
    "FR": fetch_france,
    "GB": fetch_uk,
    "NZ": fetch_newzealand,
    "AU": fetch_australia,
    "JP": fetch_japan,
    "IN": fetch_india,
    "GR": fetch_greece,
    "NO": fetch_norway,
    "CA": fetch_canada,
    "ES": fetch_spain,
    "BR": fetch_brazil,
    "ZA": fetch_south_africa,
}

async def fetch_country_specific(
    country_code: str,
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
) -> AsyncGenerator[Dict[str, Any], None]:
    """Route to the correct country extractor."""
    fn = COUNTRY_EXTRACTORS.get(country_code.upper())
    if fn:
        async for item in fn(lat, lng, radius_km, feature_ids):
            yield item

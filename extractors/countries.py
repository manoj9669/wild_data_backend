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
    UK: Ordnance Survey Names API (official UK geographic names database)
    Covers: peaks, lakes, waterfalls, forests, national parks, valleys, caves,
            beaches, rivers, moors — all official OS named features.
    API key: OS Data Hub free tier (1M transactions/month)
    """

    # OS Names categories relevant to outdoor features
    OS_LOCAL_TYPES = [
        "Waterfall", "Lake", "Loch", "Reservoir", "Mountain", "Hill", "Fell",
        "Forest", "Wood", "National Park", "Country Park", "Valley", "Glen",
        "Dale", "Gorge", "Cave", "Cliff", "Bay", "Beach", "Moor", "Heath",
        "Nature Reserve", "River", "Stream", "Island", "Summit", "Nature Reserve",
        "Area of Outstanding Natural Beauty", "Site of Special Scientific Interest",
    ]

    if not OS_API_KEY:
        print("[UK/OS] OS_API_KEY not set — skipping Ordnance Survey. Register free at osdatahub.os.uk and set OS_API_KEY env var.")
        return

    seen = set()
    radius_m = int(min(radius_km * 1000, 100000))  # OS max 100km

    # OS Names API /find with bbox — returns up to 100 results per query
    # Correct format: bbox=minX,minY,maxX,maxY (lng/lat order)
    deg = radius_km / 111.0
    bbox = f"{lng-deg},{lat-deg},{lng+deg},{lat+deg}"

    # Search terms covering all outdoor feature types
    # OS Names API local types to query
    # Using LOCAL_TYPE filter via fq parameter — correct OS API format
    local_types = [
        "Waterfall", "Lake", "Loch", "Reservoir", "Mountain", "Hill", "Fell",
        "Forest Or Woodland", "National Park", "Country Park", "Valley", "Glen",
        "Dale", "Gorge", "Cave", "Cliff", "Bay", "Beach", "Moor Or Heath",
        "Nature Reserve", "River", "Island", "Summit",
        "Area Of Outstanding Natural Beauty", "National Scenic Area",
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        for local_type in local_types:
            try:
                await rate_limiter.wait("api.os.uk", 0.7)
                # OS Names API: query = simplified type name, fq filters by exact LOCAL_TYPE
                query_term = local_type.split()[0].lower()  # e.g. "Moor Or Heath" → "moor"
                resp = await client.get(
                    "https://api.os.uk/search/names/v1/find",
                    params={
                        "query":      query_term,
                        "fq":         f"LOCAL_TYPE:{local_type}",
                        "bbox":       bbox,
                        "maxresults": 100,
                        "key":        OS_API_KEY,
                    },
                )
                if resp.status_code != 200:
                    print(f"[UK/OS] {local_type} → HTTP {resp.status_code}: {resp.text[:300]}")
                    continue

                data = resp.json()
                features = data.get("results", [])
                print(f"[UK/OS] {local_type} → {len(features)} results")

                for feat in features:
                    g = feat.get("GAZETTEER_ENTRY", {})
                    name = g.get("NAME1", "") or g.get("NAME2", "")
                    if not name or name in seen:
                        continue
                    f_lat = g.get("LAT")
                    f_lng = g.get("LNG")
                    if f_lat is None or f_lng is None:
                        # fallback to GEOMETRY coords (BNG - skip, unreliable for lat/lng)
                        continue
                    try:
                        f_lat, f_lng = float(f_lat), float(f_lng)
                    except (TypeError, ValueError):
                        continue
                    if not (49 < f_lat < 61 and -8 < f_lng < 2):
                        continue
                    if not _os_within_radius(lat, lng, f_lat, f_lng, radius_km):
                        continue
                    seen.add(name)
                    type_label, type_id = _os_guess_type(local_type, name)
                    yield {
                        "name":        name,
                        "type":        type_label,
                        "lat":         round(f_lat, 6),
                        "lng":         round(f_lng, 6),
                        "elevation":   "",
                        "region":      g.get("DISTRICT_BOROUGH", "") or g.get("COUNTY_UNITARY", "") or g.get("COUNTY_UNITARY_BOROUGH", ""),
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
                print(f"[UK/OS] {local_type} error: {e}")
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

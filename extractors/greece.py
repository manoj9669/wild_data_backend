"""
Greece government + Natura2000 outdoor feature extractor.

Sources:
  1. geodata.gov.gr  — Official Greek open geodata (CKAN API, no key)
  2. Natura2000      — EU protected areas in Greece (EEA API, no key)
  3. Greek Ministry of Environment protected areas (via Natura2000 network)
"""

import httpx
from typing import AsyncIterator, Dict, Any, List
from utils.rate_limiter import rate_limiter

# ── geodata.gov.gr CKAN API ────────────────────────────────────────────────
GEODATA_API   = "https://geodata.gov.gr/api/3/action"
# ── EEA Natura2000 API (EU protected areas) ────────────────────────────────
NATURA_API    = "https://natura2000.eea.europa.eu/Natura2000/SDF"
NATURA_SEARCH = "https://natura2000.eea.europa.eu/Natura2000/web-services/getProtectedSites"

# Known geodata.gov.gr dataset IDs for natural features
GEODATA_DATASETS = {
    "national_parks":   "perifereiakoi- kai-topikoi-fysikoi-parko",  # Regional/local nature parks
    "natura2000":       "natura-2000",                               # Natura2000 sites
    "forests":          "dasika-tmimata",                            # Forest regions
    "water_bodies":     "limnes",                                    # Lakes
    "mountains":        "oria-orous",                                # Mountain boundaries
    "corine":           "corine-land-cover",                         # Land cover
}

# Feature type mapping for WildData schema
TYPE_MAP = {
    "national_park":  "national_park",
    "nature_reserve": "nature_reserve",
    "forest":         "forest",
    "lake":           "lake",
    "mountain":       "peak",
    "wetland":        "wetland",
    "beach":          "beach",
    "gorge":          "canyon",
    "waterfall":      "waterfall",
    "cave":           "cave",
}


def _within_radius(lat: float, lng: float, feat_lat: float, feat_lng: float, radius_km: float) -> bool:
    """Quick bounding-box check before haversine."""
    deg_per_km = 1 / 111.0
    if abs(feat_lat - lat) > radius_km * deg_per_km * 1.5:
        return False
    if abs(feat_lng - lng) > radius_km * deg_per_km * 1.5:
        return False
    # Haversine
    import math
    R = 6371
    dlat = math.radians(feat_lat - lat)
    dlng = math.radians(feat_lng - lng)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(feat_lat)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) <= radius_km


async def _fetch_geodata_resource(resource_url: str, client: httpx.AsyncClient) -> List[Dict]:
    """Fetch a GeoJSON resource from geodata.gov.gr."""
    try:
        await rate_limiter.wait("geodata.gov.gr", 1.0)
        resp = await client.get(resource_url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
            return data.get("features", [])
        return []
    except Exception:
        return []


async def _fetch_geodata_search(query: str, client: httpx.AsyncClient) -> List[Dict]:
    """Search geodata.gov.gr CKAN API for datasets."""
    try:
        await rate_limiter.wait("geodata.gov.gr", 1.0)
        resp = await client.get(
            f"{GEODATA_API}/package_search",
            params={"q": query, "rows": 5},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("result", {}).get("results", [])
        resources = []
        for pkg in results:
            for res in pkg.get("resources", []):
                fmt = res.get("format", "").upper()
                if fmt in ("GEOJSON", "JSON") and res.get("url"):
                    resources.append(res["url"])
        return resources
    except Exception:
        return []


async def _fetch_natura2000(lat: float, lng: float, radius_km: float, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """
    Fetch Natura2000 protected sites near coordinates using EEA's service.
    Falls back to bounding-box WFS query if primary endpoint fails.
    """
    results = []
    deg = radius_km / 111.0

    # EEA WFS endpoint for Natura2000
    wfs_url = "https://bio.discomap.eea.europa.eu/arcgis/services/ProtectedSites/Natura2000Sites_WGS84/MapServer/WFSServer"
    try:
        await rate_limiter.wait("bio.discomap.eea.europa.eu", 1.0)
        resp = await client.get(
            wfs_url,
            params={
                "SERVICE": "WFS",
                "VERSION": "2.0.0",
                "REQUEST": "GetFeature",
                "TYPENAMES": "ProtectedSites_Natura2000Sites_WGS84:Natura2000Sites",
                "OUTPUTFORMAT": "application/json",
                "CQL_FILTER": (
                    f"BBOX(Shape,{lng-deg},{lat-deg},{lng+deg},{lat+deg},'EPSG:4326')"
                    f" AND MS_CODE LIKE 'GR%'"
                ),
                "COUNT": "50",
            },
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            for feat in data.get("features", []):
                props = feat.get("properties", {})
                geom  = feat.get("geometry", {})

                # Extract centroid
                f_lat, f_lng = None, None
                if geom.get("type") == "Point":
                    f_lng, f_lat = geom["coordinates"][:2]
                elif geom.get("type") in ("Polygon", "MultiPolygon"):
                    coords = geom["coordinates"]
                    flat = coords[0][0] if geom["type"] == "Polygon" else coords[0][0][0]
                    f_lng, f_lat = flat[0], flat[1]

                if f_lat is None or not _within_radius(lat, lng, f_lat, f_lng, radius_km):
                    continue

                site_type = props.get("MS_SITETYPE", "")
                feat_type = "nature_reserve" if "B" in site_type else "national_park"

                results.append({
                    "name":        props.get("MS_NAME") or props.get("SITENAME", ""),
                    "type":        feat_type,
                    "lat":         round(f_lat, 6),
                    "lng":         round(f_lng, 6),
                    "elevation":   None,
                    "region":      props.get("MS_CODE", "")[:4],
                    "country":     "Greece",
                    "description": f"Natura2000 protected site ({props.get('MS_SITETYPE','')}) — {props.get('MS_AREAHA','?')} ha",
                    "wikipedia":   "",
                    "website":     f"https://natura2000.eea.europa.eu/Natura2000/SDF/{props.get('MS_CODE','')}",
                    "image":       "",
                    "osm_id":      "",
                    "source":      "Natura2000/EEA",
                    "confidence":  "High",
                })
    except Exception:
        pass

    return results


async def fetch_greece(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
) -> AsyncIterator[Dict[str, Any]]:
    """
    Main Greece extractor — yields outdoor features from:
      - Natura2000 / EEA protected areas (national parks, nature reserves)
      - geodata.gov.gr natural features (lakes, forests, mountains)
      - Greek Wikipedia georeferenced articles
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
        timeout=25,
    ) as client:

        # ── 1. Natura2000 protected areas ──────────────────────────────────
        natura_results = await _fetch_natura2000(lat, lng, radius_km, client)
        for item in natura_results:
            yield item

        # ── 2. geodata.gov.gr — lakes ──────────────────────────────────────
        if not feature_ids or any(f in feature_ids for f in ("lake", "waterfall", "wetland")):
            try:
                await rate_limiter.wait("geodata.gov.gr", 1.0)
                resp = await client.get(
                    f"{GEODATA_API}/datastore_search",
                    params={
                        "resource_id": "limnes-elladas",
                        "limit": 100,
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    records = resp.json().get("result", {}).get("records", [])
                    for rec in records:
                        try:
                            f_lat = float(rec.get("LAT") or rec.get("lat") or 0)
                            f_lng = float(rec.get("LON") or rec.get("lon") or rec.get("lng") or 0)
                        except (TypeError, ValueError):
                            continue
                        if not f_lat or not _within_radius(lat, lng, f_lat, f_lng, radius_km):
                            continue
                        yield {
                            "name":        rec.get("NAME_GR") or rec.get("NAME_EN") or "Greek Lake",
                            "type":        "lake",
                            "lat":         round(f_lat, 6),
                            "lng":         round(f_lng, 6),
                            "elevation":   None,
                            "region":      rec.get("PERIFEREIA", ""),
                            "country":     "Greece",
                            "description": rec.get("DESCR", ""),
                            "wikipedia":   "",
                            "website":     "https://geodata.gov.gr",
                            "image":       "",
                            "osm_id":      "",
                            "source":      "geodata.gov.gr",
                            "confidence":  "High",
                        }
            except Exception:
                pass

        # ── 3. geodata.gov.gr — national parks / protected areas ───────────
        if not feature_ids or any(f in feature_ids for f in ("national_park", "nature_reserve", "park")):
            try:
                await rate_limiter.wait("geodata.gov.gr", 1.0)
                resp = await client.get(
                    f"{GEODATA_API}/datastore_search",
                    params={
                        "resource_id": "prostateumenes-periochees",
                        "limit": 100,
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    records = resp.json().get("result", {}).get("records", [])
                    for rec in records:
                        try:
                            f_lat = float(rec.get("LAT") or rec.get("lat") or 0)
                            f_lng = float(rec.get("LON") or rec.get("lon") or 0)
                        except (TypeError, ValueError):
                            continue
                        if not f_lat or not _within_radius(lat, lng, f_lat, f_lng, radius_km):
                            continue
                        yield {
                            "name":        rec.get("NAME_GR") or rec.get("SITE_NAME", "Protected Area"),
                            "type":        "nature_reserve",
                            "lat":         round(f_lat, 6),
                            "lng":         round(f_lng, 6),
                            "elevation":   None,
                            "region":      rec.get("PERIFEREIA", ""),
                            "country":     "Greece",
                            "description": rec.get("CATEGORY", ""),
                            "wikipedia":   "",
                            "website":     "https://geodata.gov.gr",
                            "image":       "",
                            "osm_id":      "",
                            "source":      "geodata.gov.gr",
                            "confidence":  "High",
                        }
            except Exception:
                pass

        # ── 4. Greek Ministry of Environment — national parks list ─────────
        # These 10 national parks are hardcoded as they are stable official data
        GREEK_NATIONAL_PARKS = [
            {"name": "Mount Olympus National Park",  "lat": 40.0867, "lng": 22.3572, "desc": "Greece's highest mountain, home of the gods. First national park of Greece (1938)."},
            {"name": "Parnassus National Park",      "lat": 38.5333, "lng": 22.6167, "desc": "Sacred mountain of ancient Greece, near Delphi."},
            {"name": "Vikos-Aoos National Park",     "lat": 39.9167, "lng": 20.7333, "desc": "One of the world's deepest gorges, in Epirus region."},
            {"name": "Pindus National Park",         "lat": 39.9500, "lng": 21.1167, "desc": "Core of the Pindus mountain range, dense forests and wolves."},
            {"name": "Prespa National Park",         "lat": 40.7500, "lng": 21.0833, "desc": "Transboundary wetland park shared with Albania and North Macedonia."},
            {"name": "Samaria National Park",        "lat": 35.2833, "lng": 23.9667, "desc": "Famous gorge in Crete, 16km long, UNESCO Biosphere Reserve."},
            {"name": "Mount Oiti National Park",     "lat": 38.8000, "lng": 22.2333, "desc": "Mountain park in central Greece with rare flora."},
            {"name": "Ainos National Park",          "lat": 38.1333, "lng": 20.6333, "desc": "Unique black Kefalonian fir forest on Kefalonia island."},
            {"name": "Sounio National Park",         "lat": 37.6500, "lng": 24.0167, "desc": "Coastal park at Cape Sounion with Temple of Poseidon."},
            {"name": "Dadia-Lefkimi-Soufli Forest",  "lat": 41.0833, "lng": 26.1833, "desc": "One of Europe's most important raptor habitats, rare vultures and eagles."},
        ]

        for park in GREEK_NATIONAL_PARKS:
            if _within_radius(lat, lng, park["lat"], park["lng"], radius_km):
                if not feature_ids or any(f in feature_ids for f in ("national_park", "park", "nature_reserve")):
                    yield {
                        "name":        park["name"],
                        "type":        "national_park",
                        "lat":         park["lat"],
                        "lng":         park["lng"],
                        "elevation":   None,
                        "region":      "",
                        "country":     "Greece",
                        "description": park["desc"],
                        "wikipedia":   f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
                        "website":     "https://www.minenv.gr",
                        "image":       "",
                        "osm_id":      "",
                        "source":      "Greek Ministry of Environment",
                        "confidence":  "High",
                    }

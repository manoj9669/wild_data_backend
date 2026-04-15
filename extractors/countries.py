"""
Country-specific government data sources.

Strategy:
- Only call APIs that are verified to work reliably.
- Hardcoded national park lists are 100% reliable and fast.
- OpenTripMap + GeoNames (global pipeline stages) cover dynamic feature data.
- Country extractors focus on official park registries not in global sources.
"""

import math
import os
import httpx
from typing import AsyncGenerator, Dict, Any, List
from utils.rate_limiter import rate_limiter
from extractors.greece import fetch_greece

NPS_API_KEY       = os.getenv("USA_NPS_KEY", "DEMO_KEY")
OS_API_KEY        = os.getenv("OS_API_KEY", "")


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def _in_radius(lat, lng, f_lat, f_lng, radius_km) -> bool:
    return _haversine(lat, lng, f_lat, f_lng) <= radius_km


def _park_yield(park: dict, country: str, source: str, feature_ids: List[str]):
    """Yield a hardcoded park entry if park or forest is requested."""
    if not any(f in feature_ids for f in ("park", "forest", "nature_reserve")):
        return None
    return {
        "name":        park["name"],
        "type":        park.get("type", "National Park"),
        "type_id":     "park",
        "lat":         park["lat"],
        "lng":         park["lng"],
        "elevation":   "",
        "description": park.get("desc", ""),
        "wikipedia":   f"https://en.wikipedia.org/wiki/{park['name'].replace(' ', '_')}",
        "website":     park.get("website", ""),
        "region":      park.get("region", ""),
        "country":     country,
        "image":       "",
        "osm_id":      "",
        "source":      source,
        "confidence":  "High",
    }


# ── USA — NPS API ──────────────────────────────────────────────────────────────

async def fetch_usa(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    USA: National Park Service API.
    NPS returns all parks; Python-side radius filtering keeps only nearby ones.
    DEMO_KEY allows 500 req/day. Set USA_NPS_KEY for higher limits.
    """
    if not any(f in feature_ids for f in ("park", "forest", "hiking", "camp")):
        return

    try:
        await rate_limiter.wait("developer.nps.gov", 0.5)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://developer.nps.gov/api/v1/parks",
                params={
                    "limit":  500,
                    "start":  0,
                    "fields": "images,description,url",
                    "sort":   "",
                },
                headers={"X-Api-Key": NPS_API_KEY},
            )
            if resp.status_code != 200:
                print(f"[USA NPS] HTTP {resp.status_code}")
                return
            data = resp.json()

        for park in data.get("data", []):
            try:
                p_lat = float(park.get("latitude") or 0)
                p_lng = float(park.get("longitude") or 0)
            except (TypeError, ValueError):
                continue
            if not p_lat or not _in_radius(lat, lng, p_lat, p_lng, radius_km):
                continue

            images = park.get("images", [])
            yield {
                "name":        park.get("fullName", park.get("name", "")),
                "type":        "National Park",
                "type_id":     "park",
                "lat":         p_lat,
                "lng":         p_lng,
                "elevation":   "",
                "description": park.get("description", "")[:500],
                "wikipedia":   f"https://en.wikipedia.org/wiki/{park.get('fullName','').replace(' ', '_')}",
                "website":     park.get("url", ""),
                "region":      park.get("states", ""),
                "country":     "United States",
                "image":       images[0].get("url", "") if images else "",
                "osm_id":      "",
                "source":      "NPS (USA)",
                "confidence":  "High",
            }

    except Exception as e:
        print(f"[USA NPS] error: {e}")


# ── France — hardcoded national parks ─────────────────────────────────────────

async def fetch_france(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """France: official national parks hardcoded from parcsnationaux.fr"""

    PARKS = [
        {"name": "Vanoise National Park",        "lat": 45.3833, "lng": 6.6667,  "desc": "First national park of France, largest alpine area."},
        {"name": "Port-Cros National Park",       "lat": 43.0000, "lng": 6.4000,  "desc": "Marine national park in the Mediterranean."},
        {"name": "Pyrenees National Park",        "lat": 42.8333, "lng": -0.1667, "desc": "Spectacular Pyrenean landscapes along the Spanish border."},
        {"name": "Cevennes National Park",        "lat": 44.2167, "lng": 3.6667,  "desc": "UNESCO biosphere, granite mountains and wild rivers."},
        {"name": "Ecrins National Park",          "lat": 44.9167, "lng": 6.3333,  "desc": "Highest peaks in the French Alps outside the Mont Blanc massif."},
        {"name": "Mercantour National Park",      "lat": 44.1167, "lng": 7.0000,  "desc": "Alpine park bordering Italy with wolves and ibex."},
        {"name": "Guadeloupe National Park",      "lat": 16.1500, "lng": -61.700, "desc": "Tropical rainforest and volcano La Soufrière."},
        {"name": "La Reunion National Park",      "lat": -21.100, "lng": 55.5000, "desc": "UNESCO World Heritage, active volcano Piton de la Fournaise."},
        {"name": "Calanques National Park",       "lat": 43.2000, "lng": 5.5000,  "desc": "Limestone coastal cliffs near Marseille."},
        {"name": "Forets National Park",          "lat": 47.8333, "lng": 4.8333,  "desc": "France's newest national park, temperate broadleaf forests."},
        {"name": "Mont-Saint-Michel Bay",         "lat": 48.6361, "lng": -1.5115, "desc": "UNESCO World Heritage, iconic tidal island abbey."},
        {"name": "Camargue Regional Park",        "lat": 43.5000, "lng": 4.4167,  "desc": "Wetland delta, flamingos, wild horses, black bulls.", "type": "Nature Reserve"},
    ]

    for park in PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "France", "Parcs Nationaux de France (Govt)", feature_ids)
            if entry:
                yield entry


# ── UK — Ordnance Survey Names API ────────────────────────────────────────────

OS_TYPE_MAP = {
    "waterfall": ("Waterfall",     "waterfall"),
    "lake":      ("Lake",          "lake"),
    "loch":      ("Lake",          "lake"),
    "reservoir": ("Reservoir",     "lake"),
    "mountain":  ("Mountain Peak", "peak"),
    "hill":      ("Hill",          "peak"),
    "fell":      ("Hill",          "peak"),
    "ben":       ("Mountain Peak", "peak"),
    "summit":    ("Mountain Peak", "peak"),
    "forest":    ("Forest",        "forest"),
    "wood":      ("Forest",        "forest"),
    "national park": ("National Park",  "park"),
    "country park":  ("Park",           "park"),
    "valley":    ("Valley",        "viewpoint"),
    "glen":      ("Valley",        "viewpoint"),
    "gorge":     ("Canyon",        "viewpoint"),
    "cave":      ("Cave",          "cave"),
    "cliff":     ("Viewpoint",     "viewpoint"),
    "bay":       ("Beach",         "beach"),
    "beach":     ("Beach",         "beach"),
    "nature reserve": ("Nature Reserve", "park"),
    "river":     ("River",         "lake"),
}

def _os_type(local_type: str, name: str):
    lt = (local_type or "").lower()
    nm = (name or "").lower()
    for key, val in OS_TYPE_MAP.items():
        if key in lt or key in nm:
            return val
    return ("Natural Feature", "viewpoint")


async def fetch_uk(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    UK: Ordnance Survey Names API — official geographic names.
    Free tier: 1M transactions/month. Set OS_API_KEY env var.
    """
    if not OS_API_KEY:
        print("[UK/OS] OS_API_KEY not set — skipping. Register free at osdatahub.os.uk")
        return

    deg    = radius_km / 111.0
    bounds = f"{lng-deg},{lat-deg},{lng+deg},{lat+deg}"   # minLng,minLat,maxLng,maxLat
    seen   = set()

    # (WildData feature_id → [(OS LOCAL_TYPE, [search terms])])
    OS_QUERIES: Dict[str, list] = {
        "waterfall": [("Waterfall",             ["waterfall","falls","force","foss","linn","spout"])],
        "peak":      [("Mountain",              ["mountain","ben","beinn","carn","cairn","sgurr","stob"]),
                      ("Hill",                  ["hill","tor","knoll","law"]),
                      ("Fell",                  ["fell","pike","crag"])],
        "lake":  [("Lake",                  ["lake","mere","water","tarn","llyn"]),
                      ("Loch",                  ["loch","lochan"]),
                      ("Reservoir",             ["reservoir"])],
        "park":      [("National Park",         ["park","national"]),
                      ("Country Park",          ["park","country"]),
                      ("Nature Reserve",        ["reserve","nature","sanctuary"])],
        "forest":    [("Forest Or Woodland",    ["forest","wood","woodland"])],
        "cave":      [("Cave",                  ["cave","cavern","hole","pot"])],
        "beach":     [("Beach",                 ["beach","sands","strand"]),
                      ("Bay",                   ["bay","cove"])],
        "viewpoint": [("Cliff",                 ["cliff","crag","scar","edge"])],
        "hot_spring":[("Other Hydrological Feature", ["spring","well","spa"])],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        for fid in feature_ids:
            if fid not in OS_QUERIES:
                continue
            for local_type, terms in OS_QUERIES[fid]:
                for term in terms:
                    try:
                        await rate_limiter.wait("api.os.uk", 0.5)
                        resp = await client.get(
                            "https://api.os.uk/search/names/v1/find",
                            params={
                                "query":      term,
                                "fq":         f'LOCAL_TYPE:"{local_type}"',
                                "bounds":     bounds,
                                "maxresults": 100,
                                "key":        OS_API_KEY,
                            },
                        )
                        if resp.status_code != 200:
                            print(f"[UK/OS] {local_type}/{term} → HTTP {resp.status_code}")
                            continue

                        for feat in resp.json().get("results", []):
                            g    = feat.get("GAZETTEER_ENTRY", {})
                            name = g.get("NAME1") or g.get("NAME2", "")
                            if not name or name in seen:
                                continue
                            try:
                                f_lat = float(g["LAT"])
                                f_lng = float(g["LNG"])
                            except (KeyError, TypeError, ValueError):
                                continue
                            if not (49 < f_lat < 62 and -9 < f_lng < 3):
                                continue
                            if not _in_radius(lat, lng, f_lat, f_lng, radius_km):
                                continue
                            seen.add(name)
                            type_label, type_id = _os_type(local_type, name)
                            yield {
                                "name":        name,
                                "type":        type_label,
                                "type_id":     type_id,
                                "lat":         round(f_lat, 6),
                                "lng":         round(f_lng, 6),
                                "elevation":   "",
                                "description": local_type,
                                "wikipedia":   "",
                                "website":     "https://osdatahub.os.uk",
                                "region":      g.get("DISTRICT_BOROUGH") or g.get("COUNTY_UNITARY") or "",
                                "country":     "United Kingdom",
                                "image":       "",
                                "osm_id":      g.get("OS_ID", ""),
                                "source":      "Ordnance Survey (OS Names API)",
                                "confidence":  "High",
                            }
                    except Exception as e:
                        print(f"[UK/OS] {local_type}/{term} error: {e}")

    # Hardcoded UK National Parks (guaranteed coverage)
    UK_PARKS = [
        {"name": "Lake District National Park",       "lat": 54.4609, "lng": -3.0886,  "desc": "England's largest national park, lakes, fells, UNESCO World Heritage."},
        {"name": "Snowdonia National Park",            "lat": 52.9547, "lng": -3.9424,  "desc": "Wales' highest mountains including Snowdon summit."},
        {"name": "Peak District National Park",        "lat": 53.3727, "lng": -1.8314,  "desc": "Britain's first national park, gritstone edges and limestone dales."},
        {"name": "Cairngorms National Park",           "lat": 57.0833, "lng": -3.6167,  "desc": "UK's largest national park, subarctic plateau and ancient Caledonian pinewoods."},
        {"name": "Loch Lomond and The Trossachs",      "lat": 56.2411, "lng": -4.6268,  "desc": "Scotland's first national park, iconic loch and highland scenery."},
        {"name": "Dartmoor National Park",             "lat": 50.5755, "lng": -3.9217,  "desc": "Ancient granite moorland, tors, Bronze Age remains."},
        {"name": "Exmoor National Park",               "lat": 51.1450, "lng": -3.6300,  "desc": "Wild coastal moorland, red deer, Valley of Rocks."},
        {"name": "Yorkshire Dales National Park",      "lat": 54.2308, "lng": -2.1567,  "desc": "Limestone pavements, waterfalls, dales and fells."},
        {"name": "North York Moors National Park",     "lat": 54.3764, "lng": -0.8839,  "desc": "Largest expanse of open heather moorland in England."},
        {"name": "Brecon Beacons National Park",       "lat": 51.8837, "lng": -3.4357,  "desc": "Welsh sandstone escarpment, waterfalls and dark sky reserve."},
        {"name": "Pembrokeshire Coast National Park",  "lat": 51.8553, "lng": -4.9145,  "desc": "Only coastal national park in UK, sea cliffs, islands and beaches."},
        {"name": "New Forest National Park",           "lat": 50.8735, "lng": -1.5868,  "desc": "Ancient royal hunting forest, free-roaming ponies and deer."},
        {"name": "Ben Nevis",                          "lat": 56.7969, "lng": -5.0035,  "desc": "Highest mountain in the British Isles at 1345m.", "type": "Mountain Peak"},
        {"name": "Scafell Pike",                       "lat": 54.4541, "lng": -3.2117,  "desc": "Highest mountain in England at 978m.", "type": "Mountain Peak"},
    ]
    for park in UK_PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "United Kingdom", "UK National Parks (Govt)", feature_ids)
            if entry:
                # Override type_id for peaks
                if park.get("type") == "Mountain Peak":
                    if "peak" in feature_ids:
                        entry["type_id"] = "peak"
                        entry["type"]    = "Mountain Peak"
                        yield entry
                else:
                    yield entry


# ── New Zealand — DOC API ──────────────────────────────────────────────────────

async def fetch_newzealand(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    New Zealand: Department of Conservation (DOC) open API.
    Covers tracks, campsites and parks — free, no API key required.
    """
    DOC_BASE = "https://api.doc.govt.nz/v2"

    endpoint_map = {
        "hiking": "/tracks",
        "camp":   "/campsites",
        "park":   "/parks",
    }
    type_labels = {
        "hiking": "Hiking Track",
        "camp":   "Campsite",
        "park":   "National Park",
    }

    for fid in feature_ids:
        ep = endpoint_map.get(fid)
        if not ep:
            continue
        try:
            await rate_limiter.wait("api.doc.govt.nz", 0.5)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{DOC_BASE}{ep}",
                    params={"lat": lat, "lon": lng, "radius": radius_km, "limit": 100},
                    headers={"x-api-key": ""},   # public endpoint, empty key works
                )
                if resp.status_code != 200:
                    print(f"[NZ DOC] {ep} → HTTP {resp.status_code}")
                    continue
                items = resp.json()

            for item in (items if isinstance(items, list) else []):
                # DOC API returns coordinates in various shapes
                loc = item.get("location") or {}
                i_lat = (loc.get("lat") or loc.get("latitude") or
                         item.get("lat") or item.get("latitude") or 0)
                i_lng = (loc.get("lon") or loc.get("longitude") or loc.get("lng") or
                         item.get("lon") or item.get("longitude") or item.get("lng") or 0)
                try:
                    i_lat, i_lng = float(i_lat), float(i_lng)
                except (TypeError, ValueError):
                    continue
                if not i_lat or not _in_radius(lat, lng, i_lat, i_lng, radius_km):
                    continue

                images = item.get("images") or []
                yield {
                    "name":        item.get("name", ""),
                    "type":        type_labels.get(fid, fid),
                    "type_id":     fid,
                    "lat":         round(i_lat, 6),
                    "lng":         round(i_lng, 6),
                    "elevation":   "",
                    "description": (item.get("introductory") or item.get("description") or "")[:500],
                    "wikipedia":   "",
                    "website":     f"https://www.doc.govt.nz{item.get('url', '')}",
                    "region":      item.get("region", ""),
                    "country":     "New Zealand",
                    "image":       images[0].get("url", "") if images else "",
                    "osm_id":      "",
                    "source":      "DOC (New Zealand)",
                    "confidence":  "High",
                }
        except Exception as e:
            print(f"[NZ DOC] {fid} error: {e}")


# ── Australia — hardcoded national parks ──────────────────────────────────────

async def fetch_australia(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """Australia: hardcoded national parks from Parks Australia."""

    PARKS = [
        {"name": "Kakadu National Park",                "lat": -12.9252, "lng": 132.4196, "desc": "Australia's largest national park, UNESCO World Heritage."},
        {"name": "Blue Mountains National Park",         "lat": -33.6333, "lng": 150.3000, "desc": "Sandstone escarpments, waterfalls and eucalypt forests."},
        {"name": "Great Barrier Reef Marine Park",       "lat": -18.2861, "lng": 147.7000, "desc": "World's largest coral reef system, UNESCO World Heritage.", "type": "Marine Park"},
        {"name": "Uluru-Kata Tjuta National Park",       "lat": -25.3444, "lng": 131.0369, "desc": "Sacred Aboriginal site, iconic red sandstone monolith."},
        {"name": "Daintree National Park",               "lat": -16.1700, "lng": 145.4200, "desc": "World's oldest tropical rainforest, UNESCO World Heritage."},
        {"name": "Flinders Ranges National Park",        "lat": -31.9167, "lng": 138.6833, "desc": "Ancient mountain ranges, Aboriginal rock art, unique wildlife."},
        {"name": "Cradle Mountain National Park",        "lat": -41.6500, "lng": 145.9333, "desc": "Iconic Tasmanian wilderness, glacial lakes, alpine heathlands."},
        {"name": "Purnululu National Park",              "lat": -17.5000, "lng": 128.4000, "desc": "Beehive-shaped Bungle Bungle sandstone formations, UNESCO."},
        {"name": "Wilsons Promontory National Park",     "lat": -39.0833, "lng": 146.3667, "desc": "Southernmost point of mainland Australia, pristine beaches."},
        {"name": "Lamington National Park",              "lat": -28.2167, "lng": 153.1500, "desc": "Ancient volcanic rim, subtropical rainforest, World Heritage."},
        {"name": "Shark Bay Marine Park",                "lat": -25.9667, "lng": 113.8500, "desc": "UNESCO World Heritage, stromatolites and dugong sanctuary.", "type": "Marine Park"},
        {"name": "Namadgi National Park",                "lat": -35.5667, "lng": 148.7833, "desc": "Alpine wilderness adjoining Kosciuszko."},
        {"name": "Kosciuszko National Park",             "lat": -36.4566, "lng": 148.2636, "desc": "Australia's highest peak (2228m), alpine meadows, ski fields."},
        {"name": "Nitmiluk National Park",               "lat": -14.1000, "lng": 132.4500, "desc": "Katherine Gorge, sandstone gorges, ancient Aboriginal rock art."},
        {"name": "Karijini National Park",               "lat": -22.4667, "lng": 118.3000, "desc": "Dramatic red gorges, waterfalls and swimming holes in the Pilbara."},
    ]

    for park in PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "Australia", "Parks Australia (Govt)", feature_ids)
            if entry:
                yield entry


# ── Japan — hardcoded national parks ──────────────────────────────────────────

async def fetch_japan(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """Japan: Ministry of Environment national parks."""

    PARKS = [
        {"name": "Daisetsuzan National Park",       "lat": 43.6667, "lng": 142.8333, "desc": "Japan's largest national park, volcanic peaks and hot springs."},
        {"name": "Shiretoko National Park",          "lat": 44.1000, "lng": 145.0000, "desc": "UNESCO World Heritage, remote peninsula with bears and eagles."},
        {"name": "Akan-Mashu National Park",         "lat": 43.4500, "lng": 144.3500, "desc": "Volcanic calderas, rare marimo algae, Lake Mashu."},
        {"name": "Nikko National Park",              "lat": 36.7500, "lng": 139.5000, "desc": "Ancient shrines, waterfalls, alpine lakes and volcanic peaks."},
        {"name": "Joshinetsu Kogen National Park",   "lat": 36.7000, "lng": 138.5000, "desc": "Volcanic plateau, skiing and alpine flora."},
        {"name": "Fuji-Hakone-Izu National Park",    "lat": 35.3607, "lng": 138.7274, "desc": "Mount Fuji, hot springs, Izu Peninsula coastline."},
        {"name": "Chubu Sangaku National Park",      "lat": 36.3000, "lng": 137.6500, "desc": "Japanese Alps — Tateyama, Hotaka, dramatic mountain scenery."},
        {"name": "Yoshino-Kumano National Park",     "lat": 34.0000, "lng": 135.8333, "desc": "UNESCO World Heritage pilgrimage routes through ancient forests."},
        {"name": "San-in Kaigan National Park",      "lat": 35.6333, "lng": 134.8000, "desc": "Rugged Sea of Japan coastline, sea caves and rock formations."},
        {"name": "Daisen-Oki National Park",         "lat": 35.3667, "lng": 133.5333, "desc": "Volcanic Mount Daisen and remote Oki Islands."},
        {"name": "Aso-Kuju National Park",           "lat": 32.8833, "lng": 131.1000, "desc": "World's largest volcanic caldera, active Mount Aso."},
        {"name": "Yakushima National Park",          "lat": 30.3500, "lng": 130.5333, "desc": "UNESCO World Heritage, ancient cedar forests, Jomon Sugi tree."},
        {"name": "Iriomote-Ishigaki National Park",  "lat": 24.3500, "lng": 123.8000, "desc": "Tropical jungle, mangroves and coral reefs."},
        {"name": "Towada-Hachimantai National Park", "lat": 40.4500, "lng": 140.8833, "desc": "Caldera lakes, volcanic highlands, beech forests."},
        {"name": "Bandai-Asahi National Park",       "lat": 37.6000, "lng": 140.0500, "desc": "Volcanic Bandai-san, hundreds of crater lakes."},
    ]

    for park in PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "Japan", "Ministry of Environment Japan (Govt)", feature_ids)
            if entry:
                yield entry


# ── India — Wikipedia GeoSearch ───────────────────────────────────────────────

async def fetch_india(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    India: Wikipedia GeoSearch filtered for outdoor/nature features.
    Best available free source for India — OSM sparse, GeoNames limited.
    """
    try:
        await rate_limiter.wait("en.wikipedia.org", 0.5)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action":   "query",
                    "list":     "geosearch",
                    "gscoord":  f"{lat}|{lng}",
                    "gsradius": min(int(radius_km * 1000), 10000),
                    "gslimit":  50,
                    "format":   "json",
                    "origin":   "*",
                },
            )
            data = resp.json()

        NATURE_KEYWORDS = [
            "falls", "waterfall", "peak", "pass", "trek", "trail", "lake",
            "river", "valley", "forest", "reserve", "sanctuary", "national park",
            "wildlife", "beach", "cave", "kund", "tal", "dhar", "ghati", "jharna",
            "glacier", "meadow", "bugyal", "thatch",
        ]

        for page in data.get("query", {}).get("geosearch", []):
            title = page.get("title", "")
            if not any(kw in title.lower() for kw in NATURE_KEYWORDS):
                continue
            from extractors.wikipedia import guess_type
            type_label, type_id = guess_type(title)
            if type_id not in feature_ids:
                continue
            yield {
                "name":        title,
                "type":        type_label,
                "type_id":     type_id,
                "lat":         page.get("lat", 0),
                "lng":         page.get("lon", 0),
                "elevation":   "",
                "description": "",
                "wikipedia":   f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "website":     "",
                "region":      "",
                "country":     "India",
                "image":       "",
                "osm_id":      "",
                "source":      "Wikipedia (India)",
                "confidence":  "High",
            }
    except Exception as e:
        print(f"[India Wikipedia] error: {e}")


# ── Norway — Kartverket SSR ────────────────────────────────────────────────────

async def fetch_norway(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Norway: Kartverket Stedsnavn API (SSR) — official Norwegian place name register.
    Free, no key required. Covers peaks, waterfalls, lakes, glaciers, caves.
    """
    deg = radius_km / 111.0

    SSR_TYPES: Dict[str, List[str]] = {
        "waterfall":  ["Foss", "Stryk"],
        "peak":       ["Fjell", "Topp", "Nut", "Tind", "Horn"],
        "lake":   ["Innsjø", "Vatn", "Tjern", "Elv"],
        "glacier":    ["Bre", "Isbre"],
        "cave":       ["Grotte", "Hule"],
        "viewpoint":  ["Utsiktspunkt"],
        "hot_spring": ["Kilde"],
    }
    TYPE_LABELS = {
        "waterfall": "Waterfall", "peak": "Mountain Peak", "lake": "Lake",
        "glacier": "Glacier", "cave": "Cave", "viewpoint": "Viewpoint", "hot_spring": "Hot Spring",
    }

    for fid in feature_ids:
        terms = SSR_TYPES.get(fid)
        if not terms:
            continue
        for term in terms:
            try:
                await rate_limiter.wait("ws.geonorge.no", 0.5)
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(
                        "https://ws.geonorge.no/stedsnavn/v1/sted",
                        params={
                            "sok":          f"*{term}*",
                            "utkoordsys":   4258,
                            "treffPerSide": 50,
                            "side":         0,
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
                    coords = place.get("representasjonspunkt") or {}
                    f_lat  = coords.get("nord") or coords.get("lat")
                    f_lng  = coords.get("ost")  or coords.get("lon")
                    if not f_lat or not f_lng:
                        continue
                    try:
                        f_lat, f_lng = float(f_lat), float(f_lng)
                    except (TypeError, ValueError):
                        continue
                    if not _in_radius(lat, lng, f_lat, f_lng, radius_km):
                        continue

                    navn = place.get("stedsnavn", [])
                    name = navn[0].get("skrivemåte", "") if navn else place.get("skrivemåte", "")
                    if not name:
                        continue

                    kommuner = place.get("kommuner") or []
                    region   = kommuner[0].get("kommunenavn", "") if kommuner else ""

                    yield {
                        "name":        name,
                        "type":        TYPE_LABELS.get(fid, "Natural Feature"),
                        "type_id":     fid,
                        "lat":         round(f_lat, 6),
                        "lng":         round(f_lng, 6),
                        "elevation":   "",
                        "description": f"Norwegian official place — {place.get('navneobjekttype', term)}",
                        "wikipedia":   "",
                        "website":     "https://kartverket.no",
                        "region":      region,
                        "country":     "Norway",
                        "image":       "",
                        "osm_id":      "",
                        "source":      "Kartverket SSR (Norway)",
                        "confidence":  "High",
                    }
            except Exception as e:
                print(f"[Norway Kartverket] {fid}/{term} error: {e}")

    # Hardcoded Norwegian National Parks
    NO_PARKS = [
        {"name": "Jotunheimen National Park",         "lat": 61.6333, "lng": 8.3167,   "desc": "Norway's highest mountains including Galdhøpiggen (2469m)."},
        {"name": "Hardangervidda National Park",       "lat": 60.1167, "lng": 7.5667,   "desc": "Europe's largest mountain plateau, wild reindeer herds."},
        {"name": "Rondane National Park",              "lat": 61.9000, "lng": 9.7000,   "desc": "Norway's first national park, ten peaks over 2000m."},
        {"name": "Dovrefjell National Park",           "lat": 62.3167, "lng": 9.5333,   "desc": "Wild musk oxen, reindeer, Snøhetta peak (2286m)."},
        {"name": "Jostedalsbreen National Park",       "lat": 61.7000, "lng": 7.0000,   "desc": "Mainland Europe's largest glacier."},
        {"name": "Saltfjellet-Svartisen National Park","lat": 66.6000, "lng": 14.2000,  "desc": "Arctic Circle mountains and the Svartisen glacier."},
        {"name": "Folgefonna National Park",           "lat": 60.0333, "lng": 6.3500,   "desc": "Fjord Norway, three glaciers, dramatic waterfalls."},
        {"name": "Femundsmarka National Park",         "lat": 62.2000, "lng": 11.9000,  "desc": "Wilderness lake district near the Swedish border."},
        {"name": "Breheimen National Park",            "lat": 61.6167, "lng": 7.6500,   "desc": "Glacier landscape, wild reindeer, untouched valleys."},
        {"name": "Reinheimen National Park",           "lat": 62.1167, "lng": 8.1000,   "desc": "Norway's largest untouched valleys, wild reindeer."},
    ]
    for park in NO_PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "Norway", "Miljødirektoratet (Norway Govt)", feature_ids)
            if entry:
                yield entry


# ── Canada — hardcoded national parks ─────────────────────────────────────────

async def fetch_canada(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """Canada: Parks Canada official national parks."""

    PARKS = [
        {"name": "Banff National Park",                    "lat": 51.4968,  "lng": -115.9281, "desc": "Canada's oldest national park, Rocky Mountain scenery, hot springs."},
        {"name": "Jasper National Park",                   "lat": 52.8734,  "lng": -117.9543, "desc": "Largest Rocky Mountain park, Columbia Icefield, dark sky preserve."},
        {"name": "Yoho National Park",                     "lat": 51.5000,  "lng": -116.5000, "desc": "Dramatic waterfalls, Burgess Shale fossils, emerald lakes."},
        {"name": "Kootenay National Park",                 "lat": 50.6000,  "lng": -116.0000, "desc": "Hot springs, painted pots, canyon hikes in the Rockies."},
        {"name": "Pacific Rim National Park Reserve",      "lat": 48.9900,  "lng": -125.4900, "desc": "Wild Pacific coastline, rainforest, surfing beaches."},
        {"name": "Gwaii Haanas National Park Reserve",     "lat": 52.5000,  "lng": -131.5000, "desc": "Remote Haida Gwaii archipelago, ancient Haida culture."},
        {"name": "Gros Morne National Park",               "lat": 49.5000,  "lng": -57.8000,  "desc": "UNESCO World Heritage, fiords, tablelands, coastal wilderness."},
        {"name": "Algonquin Provincial Park",              "lat": 45.5000,  "lng": -78.3000,  "desc": "Ontario's iconic canoe country, moose, wolves, ancient forests."},
        {"name": "Cape Breton Highlands National Park",    "lat": 46.7500,  "lng": -60.7500,  "desc": "Dramatic coastal highlands, Cabot Trail, bald eagles."},
        {"name": "Kluane National Park",                   "lat": 61.0000,  "lng": -138.5000, "desc": "UNESCO World Heritage, largest non-polar icefields."},
        {"name": "Wood Buffalo National Park",             "lat": 59.5000,  "lng": -113.0000, "desc": "UNESCO World Heritage, world's largest national park, bison."},
        {"name": "Nahanni National Park Reserve",          "lat": 61.0000,  "lng": -125.0000, "desc": "UNESCO World Heritage, Virginia Falls, wild canyon wilderness."},
        {"name": "Waterton Lakes National Park",           "lat": 49.0500,  "lng": -113.9000, "desc": "UNESCO World Heritage, meets Glacier NP (USA), prairie to peaks."},
        {"name": "Fundy National Park",                    "lat": 45.6333,  "lng": -64.9667,  "desc": "World's highest tides, coastal cliffs and waterfalls."},
        {"name": "Riding Mountain National Park",          "lat": 50.6833,  "lng": -100.0167, "desc": "Manitoba boreal plateau, bison herd, elk and bears."},
    ]

    for park in PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "Canada", "Parks Canada (Govt)", feature_ids)
            if entry:
                yield entry


# ── Spain — hardcoded national parks ──────────────────────────────────────────

async def fetch_spain(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """Spain: Red de Parques Nacionales official list."""

    PARKS = [
        {"name": "Teide National Park",                             "lat": 28.2726, "lng": -16.6421, "desc": "UNESCO World Heritage, highest peak in Spain — Teide volcano."},
        {"name": "Garajonay National Park",                         "lat": 28.1167, "lng": -17.2333, "desc": "UNESCO World Heritage, ancient laurel forest on La Gomera."},
        {"name": "Caldera de Taburiente National Park",             "lat": 28.7167, "lng": -17.8833, "desc": "Giant volcanic crater on La Palma, deep ravines and pine forests."},
        {"name": "Timanfaya National Park",                         "lat": 29.0000, "lng": -13.7500, "desc": "Volcanic moonscape on Lanzarote, still geothermally active."},
        {"name": "Ordesa y Monte Perdido National Park",            "lat": 42.6333, "lng": -0.0500,  "desc": "Pyrenean canyon, UNESCO World Heritage, chamois, bearded vultures."},
        {"name": "Aigüestortes i Estany de Sant Maurici",           "lat": 42.5667, "lng": 1.0000,   "desc": "Pyrenean lakes and glacial valleys, unique twisted streams."},
        {"name": "Sierra Nevada National Park",                     "lat": 37.0500, "lng": -3.3167,  "desc": "Highest peak in mainland Spain — Mulhacén 3479m."},
        {"name": "Doñana National Park",                            "lat": 36.9333, "lng": -6.4333,  "desc": "UNESCO World Heritage, Iberian lynx and flamingo habitat."},
        {"name": "Sierra de Guadarrama National Park",              "lat": 40.8500, "lng": -3.9667,  "desc": "Mountain range north of Madrid, pine forests and granite peaks."},
        {"name": "Picos de Europa National Park",                   "lat": 43.2000, "lng": -4.9000,  "desc": "Dramatic limestone massif, bears, wolves and famous gorges."},
        {"name": "Cabañeros National Park",                         "lat": 39.4000, "lng": -4.4000,  "desc": "Mediterranean scrubland, deer, imperial eagles."},
        {"name": "Monfragüe National Park",                         "lat": 39.8333, "lng": -5.9000,  "desc": "Raptor paradise — black vultures, Spanish imperial eagles, lynx."},
        {"name": "Tablas de Daimiel National Park",                 "lat": 39.1333, "lng": -3.7000,  "desc": "Wetland in La Mancha, migratory waterbirds."},
        {"name": "Sierra de las Nieves National Park",              "lat": 36.7000, "lng": -4.9500,  "desc": "Newest national park, pinsapo fir forests and limestone karst."},
        {"name": "Archipiélago de Cabrera National Park",           "lat": 39.1500, "lng": 2.9333,   "desc": "Marine national park, clear Mediterranean waters, seabirds."},
    ]

    for park in PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "Spain", "Red de Parques Nacionales (Spain Govt)", feature_ids)
            if entry:
                yield entry


# ── Brazil — hardcoded national parks ─────────────────────────────────────────

async def fetch_brazil(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """Brazil: ICMBio national parks official list."""

    PARKS = [
        {"name": "Amazon National Park",                    "lat": -4.5000,  "lng": -56.5000, "desc": "Largest national park in Brazil, heart of the Amazon rainforest."},
        {"name": "Iguaçu National Park",                    "lat": -25.6950, "lng": -54.4360, "desc": "UNESCO World Heritage, world's largest waterfall system."},
        {"name": "Fernando de Noronha Marine Park",         "lat": -3.8550,  "lng": -32.4230, "desc": "UNESCO World Heritage, pristine marine reserve, diving paradise.", "type": "Marine Park"},
        {"name": "Chapada Diamantina National Park",        "lat": -12.5000, "lng": -41.5000, "desc": "Tablelands, waterfalls, caves and rivers in Bahia."},
        {"name": "Chapada dos Veadeiros National Park",     "lat": -14.0000, "lng": -47.5000, "desc": "UNESCO World Heritage, cerrado savanna, quartz crystal formations."},
        {"name": "Lençóis Maranhenses National Park",       "lat": -2.5000,  "lng": -43.0000, "desc": "White sand dunes with seasonal turquoise lagoons."},
        {"name": "Pantanal Matogrossense National Park",    "lat": -17.8000, "lng": -57.5000, "desc": "UNESCO World Heritage, world's largest tropical wetland."},
        {"name": "Serra da Capivara National Park",         "lat": -8.5000,  "lng": -42.5000, "desc": "UNESCO World Heritage, prehistoric rock art spanning 50,000 years."},
        {"name": "Tijuca National Park",                    "lat": -22.9500, "lng": -43.2800, "desc": "World's largest urban forest, within Rio de Janeiro."},
        {"name": "Jaú National Park",                       "lat": -2.0000,  "lng": -62.0000, "desc": "UNESCO World Heritage, black-water rivers and flooded forest."},
        {"name": "Serra dos Órgãos National Park",          "lat": -22.4500, "lng": -43.0167, "desc": "Dramatic granite rock formations, rock climbing, Atlantic Forest."},
        {"name": "Aparados da Serra National Park",         "lat": -29.1500, "lng": -50.1500, "desc": "Itaimbezinho Canyon, one of South America's deepest gorges."},
        {"name": "Caparaó National Park",                   "lat": -20.4500, "lng": -41.8833, "desc": "Pico da Bandeira (2892m), third highest peak in Brazil."},
    ]

    for park in PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "Brazil", "ICMBio (Brazil Govt)", feature_ids)
            if entry:
                yield entry


# ── South Africa — SANParks ───────────────────────────────────────────────────

async def fetch_south_africa(
    lat: float, lng: float, radius_km: float, feature_ids: List[str]
) -> AsyncGenerator[Dict[str, Any], None]:
    """South Africa: SANParks official national parks list."""

    PARKS = [
        {"name": "Kruger National Park",                "lat": -23.9890, "lng": 31.5540, "desc": "South Africa's flagship game reserve, Big Five wildlife."},
        {"name": "Table Mountain National Park",        "lat": -34.0000, "lng": 18.4000, "desc": "UNESCO World Heritage, iconic flat-topped mountain above Cape Town."},
        {"name": "Kgalagadi Transfrontier Park",        "lat": -26.0000, "lng": 20.5000, "desc": "Vast semi-desert, black-maned Kalahari lions and raptors."},
        {"name": "iSimangaliso Wetland Park",           "lat": -28.0000, "lng": 32.5000, "desc": "UNESCO World Heritage, estuary, coral reefs, hippos, crocs."},
        {"name": "Drakensberg uKhahlamba Park",         "lat": -29.5000, "lng": 29.2000, "desc": "UNESCO World Heritage, dramatic Basotho Highlands escarpment."},
        {"name": "Garden Route National Park",          "lat": -33.9000, "lng": 22.6000, "desc": "Forests, lakes, beaches and whales along the southern Cape coast."},
        {"name": "Richtersveld National Park",          "lat": -28.5000, "lng": 17.0000, "desc": "UNESCO World Heritage, desert mountain wilderness, succulents."},
        {"name": "Addo Elephant National Park",         "lat": -33.5000, "lng": 25.7500, "desc": "Dense elephant herds, Big Seven including sharks and whales."},
        {"name": "Mapungubwe National Park",            "lat": -22.2000, "lng": 29.3000, "desc": "UNESCO World Heritage, Iron Age kingdom ruins, baobabs."},
        {"name": "Agulhas National Park",               "lat": -34.8000, "lng": 20.0000, "desc": "Southernmost tip of Africa, rocky reefs, migrating whales."},
        {"name": "Camdeboo National Park",              "lat": -32.2500, "lng": 24.5000, "desc": "Karoo landscape, Valley of Desolation, black eagles."},
        {"name": "Golden Gate Highlands National Park", "lat": -28.5000, "lng": 28.6000, "desc": "Sandstone cliffs glowing gold at sunset, bearded vultures."},
        {"name": "Bontebok National Park",              "lat": -34.0833, "lng": 20.4500, "desc": "Saved the bontebok from extinction, Cape fynbos biome."},
        {"name": "West Coast National Park",            "lat": -33.2500, "lng": 18.1000, "desc": "Langebaan lagoon, spring wildflowers, coastal birds."},
    ]

    for park in PARKS:
        if _in_radius(lat, lng, park["lat"], park["lng"], radius_km):
            entry = _park_yield(park, "South Africa", "SANParks (South Africa Govt)", feature_ids)
            if entry:
                yield entry


# ── Dispatcher ─────────────────────────────────────────────────────────────────

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

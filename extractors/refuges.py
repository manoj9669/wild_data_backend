"""
Refuges.info API — mountain huts, bivouacs and shelters.
Free, no API key required. Covers Alps, Pyrenees, and all European mountain ranges.
API docs: https://www.refuges.info/api/doc/
"""

import math
import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

REFUGES_URL = "https://www.refuges.info/api/bbox"

# type_points values:
# 7  = cabane non gardée (unguarded hut)
# 9  = bivouac
# 10 = refuge gardé (guarded refuge)
# 11 = gîte d'étape (hostel)
# 16 = abri (shelter)
TYPE_LABELS = {
    "cabane non gardée": "Mountain Hut (Unguarded)",
    "bivouac":           "Bivouac",
    "refuge gardé":      "Mountain Refuge (Guarded)",
    "gîte d'étape":      "Mountain Hostel",
    "abri":              "Shelter",
}


def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


async def fetch_refuges(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch mountain huts and bivouacs from Refuges.info.
    Only runs when 'hut' or 'camp' is in selected features.
    Free, no API key required.
    """
    if not any(f in feature_ids for f in ("hut", "camp")):
        return

    deg = radius_km / 111.0
    bbox = f"{lng-deg},{lat-deg},{lng+deg},{lat+deg}"  # minLng,minLat,maxLng,maxLat

    try:
        await rate_limiter.wait("www.refuges.info", 1.0)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                REFUGES_URL,
                params={
                    "bbox":         bbox,
                    "type_points":  "7,9,10,11,16",
                    "format":       "geojson",
                },
                headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
            )
            if resp.status_code != 200:
                print(f"[Refuges.info] HTTP {resp.status_code}")
                return
            data = resp.json()

        for feat in data.get("features", []):
            props = feat.get("properties", {})
            coords = feat.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                continue

            f_lng, f_lat = float(coords[0]), float(coords[1])
            if _haversine(lat, lng, f_lat, f_lng) > radius_km:
                continue

            nom = props.get("nom", {})
            name = nom.get("fr") or nom.get("en") or "" if isinstance(nom, dict) else str(nom)
            if not name:
                continue

            type_val = (props.get("type") or {}).get("valeur", "")
            type_label = TYPE_LABELS.get(type_val, "Mountain Hut")
            fid = "hut" if "bivouac" not in type_val else "camp"

            capacite = props.get("capacite", {})
            capacity = capacite.get("valeur", "") if isinstance(capacite, dict) else ""
            desc = f"{type_label}. Capacity: {capacity}" if capacity else type_label

            altitude = props.get("coord", {}).get("alt", "")
            refuge_id = props.get("id", "")
            website = f"https://www.refuges.info/point/{refuge_id}" if refuge_id else "https://www.refuges.info"

            yield {
                "name":        name,
                "type":        type_label,
                "type_id":     fid,
                "lat":         round(f_lat, 6),
                "lng":         round(f_lng, 6),
                "elevation":   f"{altitude}m" if altitude else "",
                "description": desc,
                "wikipedia":   "",
                "website":     website,
                "region":      "",
                "country":     "",
                "image":       "",
                "osm_id":      "",
                "source":      "Refuges.info",
                "confidence":  "High",
            }

    except Exception as e:
        print(f"[Refuges.info] error: {e}")

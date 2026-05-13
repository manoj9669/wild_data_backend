"""
iNaturalist API — community-sourced nature & wildlife observation database.
Completely FREE, no API key required. Rate limit: ~100 req/min (unauthenticated).
API docs: https://api.inaturalist.org/v1/docs/
"""

import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter

INAT_BASE = "https://api.inaturalist.org/v1"
MAX_CALLS_PER_RUN = 5

INAT_RELEVANT = {"park", "forest", "waterfall", "cave", "waterway", "beach",
                 "glacier", "volcano", "hot_spring", "peak", "viewpoint",
                 "wildlife_sighting"}

PLACE_TYPES = {
    15: "National Park",
    14: "Nature Reserve",
    13: "Island",
    8:  "Open Space",
    97: "Open Space",
    9:  "Territory",
}

# Map iNaturalist place_type → WildData feature_id
_PLACE_TYPE_FID = {15: "park", 14: "forest", 13: "waterway"}


def _place_params(lat: float, lng: float, radius_km: float,
                  limit: int, bbox: Optional[Tuple]) -> dict:
    if bbox:
        south, west, north, east = bbox
        return {"nelat": north, "nelng": east, "swlat": south,
                "swlng": west, "per_page": min(limit, 200)}
    return {"lat": lat, "lng": lng, "radius": int(radius_km),
            "per_page": min(limit, 200)}


def _obs_params(lat: float, lng: float, radius_km: float,
                limit: int, bbox: Optional[Tuple]) -> dict:
    base = {"quality_grade": "research", "order_by": "votes", "order": "desc",
            "per_page": min(limit // 2, 100), "photos": True,
            "iconic_taxa": "Mammalia,Aves,Reptilia,Amphibia"}
    if bbox:
        south, west, north, east = bbox
        return {**base, "nelat": north, "nelng": east, "swlat": south, "swlng": west}
    return {**base, "lat": lat, "lng": lng, "radius": int(min(radius_km, 50))}


def _wiki_url_from_place(place: dict) -> str:
    wiki = place.get("wikipedia_url", "")
    if not wiki:
        return ""
    return wiki if wiki.startswith("http") else \
        f"https://en.wikipedia.org/wiki/{wiki.replace(' ', '_')}"


async def _fetch_places(client: httpx.AsyncClient, lat: float, lng: float,
                        radius_km: float, limit: int, bbox: Optional[Tuple],
                        feature_ids: List[str]) -> AsyncGenerator:
    """Fetch named nature places from /places/nearby."""
    params = _place_params(lat, lng, radius_km, limit, bbox)
    await rate_limiter.wait("api.inaturalist.org", 0.6)
    resp = await client.get(f"{INAT_BASE}/places/nearby", params=params)
    if resp.status_code != 200:
        print(f"[iNaturalist] places/nearby HTTP {resp.status_code}")
        return

    data = resp.json()
    places = data.get("results", {}).get("standard", []) + \
             data.get("results", {}).get("community", [])

    for place in places:
        place_id = place.get("id")
        name = (place.get("display_name") or place.get("name") or "").strip()
        place_type = place.get("place_type", 0)

        if not name or not place_id:
            continue
        if place_type not in PLACE_TYPES and place_type not in (8, 97, 14, 15, 13):
            continue

        f_lat = place.get("latitude")
        f_lng = place.get("longitude")
        if not f_lat or not f_lng:
            continue

        fid = _PLACE_TYPE_FID.get(place_type)
        if not fid or fid not in feature_ids:
            continue

        yield {
            "name":        name,
            "type":        PLACE_TYPES.get(place_type, "Nature Area"),
            "type_id":     fid,
            "lat":         round(float(f_lat), 6),
            "lng":         round(float(f_lng), 6),
            "elevation":   "",
            "description": "",
            "wikipedia":   _wiki_url_from_place(place),
            "website":     f"https://www.inaturalist.org/places/{place_id}",
            "region":      "",
            "country":     "",
            "image":       "",
            "osm_id":      "",
            "source":      "iNaturalist",
            "confidence":  "High" if place.get("bbox_area", 0) > 0 else "Medium",
        }


async def _fetch_observations(client: httpx.AsyncClient, lat: float, lng: float,
                               radius_km: float, limit: int,
                               bbox: Optional[Tuple]) -> AsyncGenerator:
    """Fetch research-grade wildlife observations."""
    params = _obs_params(lat, lng, radius_km, limit, bbox)
    await rate_limiter.wait("api.inaturalist.org", 0.6)
    resp = await client.get(f"{INAT_BASE}/observations", params=params)
    if resp.status_code != 200:
        print(f"[iNaturalist] observations HTTP {resp.status_code}")
        return

    seen_coords: set = set()
    for obs in resp.json().get("results", []):
        geo = obs.get("location", "")
        if not geo:
            continue
        try:
            f_lat, f_lng = round(float(geo.split(",")[0]), 4), round(float(geo.split(",")[1]), 4)
        except (ValueError, AttributeError, IndexError):
            continue

        cell = (round(f_lat, 2), round(f_lng, 2))
        if cell in seen_coords:
            continue
        seen_coords.add(cell)

        taxon = obs.get("taxon", {}) or {}
        species = taxon.get("preferred_common_name") or taxon.get("name") or ""
        if not species:
            continue

        place_guess = obs.get("place_guess", "")
        photo = obs.get("photos", [{}])[0] if obs.get("photos") else {}
        image = photo.get("url", "").replace("square", "medium") if photo else ""

        yield {
            "name":        f"{species} sighting" + (f" — {place_guess}" if place_guess else ""),
            "type":        "Wildlife Sighting",
            "type_id":     "wildlife_sighting",
            "lat":         f_lat,
            "lng":         f_lng,
            "elevation":   "",
            "description": obs.get("description", "") or f"Research-grade {species} observation.",
            "wikipedia":   "",
            "website":     f"https://www.inaturalist.org/observations/{obs.get('id','')}",
            "region":      place_guess,
            "country":     "",
            "image":       image,
            "osm_id":      "",
            "source":      "iNaturalist",
            "confidence":  "High",
        }


async def fetch_inaturalist(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch nature areas and wildlife hotspots from iNaturalist.
    No API key required. Yields one result dict at a time.
    """
    relevant = [f for f in feature_ids if f in INAT_RELEVANT]
    if not relevant:
        return

    calls_made = 0
    async with httpx.AsyncClient(timeout=20) as client:

        if calls_made < MAX_CALLS_PER_RUN:
            try:
                async for item in _fetch_places(client, lat, lng, radius_km,
                                                limit, bbox, feature_ids):
                    yield item
                calls_made += 1
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
                print(f"[iNaturalist] places error: {e}")

        if calls_made < MAX_CALLS_PER_RUN and "wildlife_sighting" in feature_ids:
            try:
                async for item in _fetch_observations(client, lat, lng,
                                                      radius_km, limit, bbox):
                    yield item
                calls_made += 1
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as e:
                print(f"[iNaturalist] observations error: {e}")

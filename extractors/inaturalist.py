"""
iNaturalist API — community-sourced nature & wildlife observation database.
Completely FREE, no API key required. Rate limit: ~100 req/min (unauthenticated).
Per-extraction cap: MAX_CALLS_PER_RUN (default 5) — very generous API, conservative cap.

Two data types:
  1. iNaturalist Places  (/places/nearby)   — named parks, nature reserves, wilderness areas
  2. Wildlife Hotspots   (/observations)    — top research-grade sightings (grouped by location)

Perfect for GoWild: wildlife spots, nature reserves, biodiversity hotspots, rare species areas.

API docs: https://api.inaturalist.org/v1/docs/
"""

import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter

INAT_BASE = "https://api.inaturalist.org/v1"

# No API key needed for read-only access.
# Conservative per-run cap to be a good citizen of the free API.
MAX_CALLS_PER_RUN = 5

# WildData feature IDs that benefit from iNaturalist data
INAT_RELEVANT = {"park", "forest", "waterfall", "cave", "waterway", "beach",
                 "glacier", "volcano", "hot_spring", "peak", "viewpoint"}

# iNaturalist place type IDs
# 1=country, 2=state, 3=county, 8=open space, 9=territory, 10=continent,
# 12=municipality, 13=island, 14=reserve, 15=national_park, 97=open_space,
# 100=admin_boundary, 101=conservation_area
PLACE_TYPES = {
    15: "National Park",
    14: "Nature Reserve",
    13: "Island",
    8:  "Open Space",
    97: "Open Space",
    9:  "Territory",
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

    Stage 1: Named places (parks, reserves, wilderness) from /places/nearby
    Stage 2: Wildlife observation hotspot locations from /observations (research-grade)
    """
    # Only run if relevant feature types were selected
    relevant = [f for f in feature_ids if f in INAT_RELEVANT]
    if not relevant:
        return

    calls_made = 0

    async with httpx.AsyncClient(timeout=20) as client:

        # ── Stage 1: Named nature places ──────────────────────────────────────
        if calls_made < MAX_CALLS_PER_RUN:
            try:
                if bbox:
                    south, west, north, east = bbox
                    place_params = {
                        "nelat":    north,
                        "nelng":    east,
                        "swlat":    south,
                        "swlng":    west,
                        "per_page": min(limit, 200),
                    }
                else:
                    place_params = {
                        "lat":      lat,
                        "lng":      lng,
                        "radius":   int(radius_km),
                        "per_page": min(limit, 200),
                    }

                await rate_limiter.wait("api.inaturalist.org", 0.6)
                resp = await client.get(f"{INAT_BASE}/places/nearby", params=place_params)
                calls_made += 1

                if resp.status_code == 200:
                    data = resp.json()
                    for place in data.get("results", {}).get("standard", []) + \
                                  data.get("results", {}).get("community", []):
                        place_id   = place.get("id")
                        name       = (place.get("display_name") or place.get("name") or "").strip()
                        place_type = place.get("place_type", 0)

                        if not name or not place_id:
                            continue

                        # Only yield place types relevant to outdoor/nature
                        if place_type not in PLACE_TYPES and place_type not in (8, 97, 14, 15, 13):
                            continue

                        f_lat = place.get("latitude")
                        f_lng = place.get("longitude")
                        if not f_lat or not f_lng:
                            continue

                        type_label = PLACE_TYPES.get(place_type, "Nature Area")

                        # Map iNaturalist place type to WildData feature_id
                        if place_type == 15:
                            fid = "park"
                        elif place_type == 14:
                            fid = "forest"
                        elif place_type == 13:
                            fid = "waterway"
                        else:
                            fid = relevant[0]

                        # Only yield if user selected a matching feature
                        if fid not in feature_ids and "park" not in feature_ids and "forest" not in feature_ids:
                            continue

                        bbox_place = place.get("bbox_area", 0)
                        confidence = "High" if bbox_place > 0 else "Medium"

                        wiki_url = ""
                        wiki_name = place.get("wikipedia_url", "")
                        if wiki_name:
                            wiki_url = wiki_name if wiki_name.startswith("http") else \
                                       f"https://en.wikipedia.org/wiki/{wiki_name.replace(' ', '_')}"

                        yield {
                            "name":        name,
                            "type":        type_label,
                            "type_id":     fid,
                            "lat":         round(float(f_lat), 6),
                            "lng":         round(float(f_lng), 6),
                            "elevation":   "",
                            "description": "",
                            "wikipedia":   wiki_url,
                            "website":     f"https://www.inaturalist.org/places/{place_id}",
                            "region":      "",
                            "country":     "",
                            "image":       "",
                            "osm_id":      "",
                            "source":      "iNaturalist",
                            "confidence":  confidence,
                        }
                else:
                    print(f"[iNaturalist] places/nearby HTTP {resp.status_code}")

            except Exception as e:
                print(f"[iNaturalist] places error: {e}")

        # ── Stage 2: Wildlife observation hotspots ─────────────────────────────
        # Fetch top research-grade observations to surface notable wildlife spots
        if calls_made < MAX_CALLS_PER_RUN and any(f in feature_ids for f in ("park", "forest", "waterway")):
            try:
                if bbox:
                    south, west, north, east = bbox
                    obs_params = {
                        "nelat":        north,
                        "nelng":        east,
                        "swlat":        south,
                        "swlng":        west,
                        "quality_grade": "research",
                        "order_by":     "votes",
                        "order":        "desc",
                        "per_page":     min(limit // 2, 100),
                        "photos":       True,
                        "iconic_taxa":  "Mammalia,Aves,Reptilia,Amphibia",  # charismatic wildlife
                    }
                else:
                    obs_params = {
                        "lat":           lat,
                        "lng":           lng,
                        "radius":        int(min(radius_km, 50)),
                        "quality_grade": "research",
                        "order_by":      "votes",
                        "order":         "desc",
                        "per_page":      min(limit // 2, 100),
                        "photos":        True,
                        "iconic_taxa":   "Mammalia,Aves,Reptilia,Amphibia",
                    }

                await rate_limiter.wait("api.inaturalist.org", 0.6)
                resp = await client.get(f"{INAT_BASE}/observations", params=obs_params)
                calls_made += 1

                if resp.status_code == 200:
                    data = resp.json()
                    seen_coords: set = set()

                    for obs in data.get("results", []):
                        geo = obs.get("location", "")
                        if not geo:
                            continue
                        try:
                            f_lat_s, f_lng_s = geo.split(",")
                            f_lat, f_lng = round(float(f_lat_s), 4), round(float(f_lng_s), 4)
                        except Exception:
                            continue

                        # Group nearby observations — only 1 marker per ~500m cell
                        cell = (round(f_lat, 2), round(f_lng, 2))
                        if cell in seen_coords:
                            continue
                        seen_coords.add(cell)

                        taxon   = obs.get("taxon", {}) or {}
                        species = taxon.get("preferred_common_name") or taxon.get("name") or ""
                        if not species:
                            continue

                        place_guess = obs.get("place_guess", "")
                        name = f"{species} sighting" + (f" — {place_guess}" if place_guess else "")

                        photo = obs.get("photos", [{}])[0] if obs.get("photos") else {}
                        image = photo.get("url", "").replace("square", "medium") if photo else ""

                        obs_url = f"https://www.inaturalist.org/observations/{obs.get('id','')}"

                        yield {
                            "name":        name,
                            "type":        "Wildlife Sighting",
                            "type_id":     "park",
                            "lat":         f_lat,
                            "lng":         f_lng,
                            "elevation":   "",
                            "description": obs.get("description", "") or f"Research-grade {species} observation.",
                            "wikipedia":   "",
                            "website":     obs_url,
                            "region":      place_guess,
                            "country":     "",
                            "image":       image,
                            "osm_id":      "",
                            "source":      "iNaturalist",
                            "confidence":  "High",
                        }
                else:
                    print(f"[iNaturalist] observations HTTP {resp.status_code}")

            except Exception as e:
                print(f"[iNaturalist] observations error: {e}")

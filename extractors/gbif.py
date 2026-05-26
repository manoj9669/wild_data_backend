"""
GBIF — Global Biodiversity Information Facility extractor.
100% free, no API key required, Creative Commons open data.

GBIF aggregates biodiversity observations from 1,900+ institutions worldwide.
For WildData we use two endpoints:

1. /occurrence/search — geotagged species observations (research-grade)
   → yields wildlife-rich locations (birding hotspots, wildlife areas, etc.)

2. /species/match + occurrence density  — finds nature areas worth visiting

The most useful signal: clusters of diverse observations = a notable
wildlife/nature spot worth pinning as an outdoor place of interest.

Rate limit: no hard limit but be polite (1 req/sec, max 300 results/call).
Docs: https://www.gbif.org/developer/occurrence
"""

import httpx
from collections import defaultdict
from typing import AsyncGenerator, Dict, Any, List
from utils.rate_limiter import rate_limiter

GBIF_OCCURRENCE  = "https://api.gbif.org/v1/occurrence/search"
GBIF_AREAS       = "https://api.gbif.org/v1/geocode/gadm"

# GBIF basis of record — only use verified field observations
ACCEPTED_BASIS = {"HUMAN_OBSERVATION", "MACHINE_OBSERVATION", "PRESERVED_SPECIMEN"}

# Feature types that map well to GBIF nature hotspots
GBIF_COMPATIBLE = {"park", "forest", "lake", "wetland", "beach", "waterfall", "viewpoint", "attraction"}


def _cluster_to_place(cluster_lat: float, cluster_lng: float,
                      observations: List[Dict]) -> Dict[str, Any] | None:
    """
    Convert a cluster of GBIF observations into a single outdoor place record.
    Uses the most-observed location as the pin, builds a description from
    the most notable species found there.
    """
    if len(observations) < 5:
        return None

    # Build species list — pick the most distinctive ones
    species_set = set()
    for obs in observations:
        sp = obs.get("species") or obs.get("genericName")
        if sp and len(sp) > 3:
            species_set.add(sp)

    # Derive a place name from locality or dataset title
    names = [
        obs.get("locality") or obs.get("stateProvince") or obs.get("county")
        for obs in observations
        if obs.get("locality") or obs.get("stateProvince")
    ]
    place_name = names[0] if names else None
    if not place_name:
        return None

    # Build description
    sp_sample = sorted(species_set)[:5]
    sp_text = ", ".join(sp_sample) if sp_sample else "various species"
    description = (
        f"Wildlife observation hotspot with {len(observations)}+ recorded sightings "
        f"including {sp_text}."
    )

    country = observations[0].get("countryCode", "")

    return {
        "name":        place_name,
        "type":        "Nature Hotspot",
        "type_id":     "park",
        "lat":         round(cluster_lat, 5),
        "lng":         round(cluster_lng, 5),
        "elevation":   "",
        "description": description,
        "wikipedia":   "",
        "website":     f"https://www.gbif.org/occurrence/search?decimalLatitude={cluster_lat-0.1},{cluster_lat+0.1}&decimalLongitude={cluster_lng-0.1},{cluster_lng+0.1}",
        "region":      observations[0].get("stateProvince", ""),
        "country":     country,
        "image":       "",
        "osm_id":      "",
        "source":      "GBIF",
        "confidence":  "Medium",
    }


async def fetch_gbif(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch nature hotspots from GBIF occurrence data.
    Clusters geotagged observations into distinct outdoor places.
    Only runs when outdoor/nature feature types are requested.
    """
    # Only useful for nature-type features
    if not any(f in GBIF_COMPATIBLE for f in feature_ids):
        return

    try:
        await rate_limiter.wait("api.gbif.org", 1.0)
        async with httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
        ) as client:
            resp = await client.get(GBIF_OCCURRENCE, params={
                "decimalLatitude":  f"{lat - radius_km/111},{lat + radius_km/111}",
                "decimalLongitude": f"{lng - radius_km/111},{lng + radius_km/111}",
                "basisOfRecord":    "HUMAN_OBSERVATION",
                "hasCoordinate":    "true",
                "hasGeospatialIssue": "false",
                "occurrenceStatus": "PRESENT",
                "limit":            300,
                "offset":           0,
            })

            if resp.status_code != 200:
                print(f"[GBIF] HTTP {resp.status_code}")
                return

            results = resp.json().get("results", [])

    except Exception as e:
        print(f"[GBIF] fetch error: {e}")
        return

    if not results:
        return

    # ── Cluster observations by 0.05° grid cell (~5 km) ───────────────────
    clusters: Dict[tuple, List[Dict]] = defaultdict(list)
    for obs in results:
        obs_lat = obs.get("decimalLatitude")
        obs_lng = obs.get("decimalLongitude")
        if obs_lat is None or obs_lng is None:
            continue
        # Grid cell key at 0.05° resolution
        cell = (round(obs_lat * 20) / 20, round(obs_lng * 20) / 20)
        clusters[cell].append(obs)

    # Sort clusters by observation count (richest first)
    sorted_clusters = sorted(clusters.items(), key=lambda x: len(x[1]), reverse=True)

    seen_names: set = set()
    yielded = 0

    for (cell_lat, cell_lng), obs_list in sorted_clusters:
        if yielded >= limit:
            break
        place = _cluster_to_place(cell_lat, cell_lng, obs_list)
        if not place:
            continue
        if place["name"] in seen_names:
            continue
        seen_names.add(place["name"])
        yield place
        yielded += 1

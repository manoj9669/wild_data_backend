import httpx
from typing import List, Dict, Any
from utils.rate_limiter import rate_limiter

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
TOPO_URL = "https://api.opentopodata.org/v1/srtm30m"

async def reverse_geocode(lat: float, lng: float) -> Dict[str, str]:
    """Get region, city + country for a coordinate using Nominatim."""
    try:
        await rate_limiter.wait("nominatim.openstreetmap.org", 1.2)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                NOMINATIM_URL,
                params={
                    "lat": lat,
                    "lon": lng,
                    "format": "json",
                    "zoom": 10,
                    "accept-language": "en",
                },
                headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
            )
            resp.raise_for_status()
            data = resp.json()

        addr = data.get("address", {})
        region = (
            addr.get("state") or
            addr.get("province") or
            addr.get("region") or
            ""
        )
        country = addr.get("country", "")

        # Most specific populated place — useful for "which city/town is this near?"
        city = (
            addr.get("village") or        # rural: most specific
            addr.get("town") or           # small town
            addr.get("city") or           # city
            addr.get("suburb") or         # suburb within a city
            addr.get("municipality") or   # municipality
            addr.get("county") or         # district/county fallback
            ""
        )
        # nearest is used for auto-naming unnamed places
        nearest = city or (addr.get("county") or "")
        return {"region": region, "country": country, "city": city, "nearest": nearest}
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError, KeyError):
        return {"region": "", "country": "", "city": "", "nearest": ""}


async def enrich_geocoding(
    results: List[Dict[str, Any]],
    max_calls: int = 50,
) -> List[Dict[str, Any]]:
    """
    Reverse geocode results missing region/country.
    Strictly rate limited to 1 call/sec (Nominatim policy).
    Caps at max_calls to avoid long waits.
    Auto-generates name for unnamed results using nearest place.
    """
    calls = 0
    for r in results:
        if calls >= max_calls:
            break
        needs_geo = not r.get("region") or not r.get("country")
        needs_name = not r.get("name")
        if not needs_geo and not needs_name:
            continue

        geo = await reverse_geocode(r["lat"], r["lng"])
        calls += 1

        if geo["region"]:
            r["region"] = geo["region"]
        if geo["country"]:
            r["country"] = geo["country"]
        if geo["city"] and not r.get("city"):
            r["city"] = geo["city"]

        # Auto-generate name if still unnamed
        if not r.get("name"):
            nearest = geo.get("nearest", "")
            if nearest:
                r["name"] = f"{r['type']} near {nearest}"
                r["confidence"] = "Medium"
            else:
                lat_str = f"{abs(r['lat']):.3f}{'N' if r['lat'] >= 0 else 'S'}"
                lng_str = f"{abs(r['lng']):.3f}{'E' if r['lng'] >= 0 else 'W'}"
                region_str = geo.get("region", "")
                r["name"] = f"{r['type']} {lat_str} {lng_str}" + (f" ({region_str})" if region_str else "")
                r["confidence"] = "Low"

    return results


async def enrich_elevation(
    results: List[Dict[str, Any]],
    max_points: int = 200,
) -> List[Dict[str, Any]]:
    """
    Fetch elevation for results missing it using OpenTopoData (SRTM30m).
    Batches up to 100 points per request.
    """
    no_elev = [r for r in results if not r.get("elevation")][:max_points]
    if not no_elev:
        return results

    BATCH = 100
    for i in range(0, len(no_elev), BATCH):
        batch = no_elev[i:i + BATCH]
        locs = "|".join(f"{r['lat']},{r['lng']}" for r in batch)

        try:
            await rate_limiter.wait("api.opentopodata.org", 1.0)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    TOPO_URL,
                    params={"locations": locs},
                )
                resp.raise_for_status()
                data = resp.json()

            elevations = data.get("results", [])
            for j, elev_data in enumerate(elevations):
                elev = elev_data.get("elevation")
                if elev is not None:
                    # batch[j] is a reference to the same dict object in results —
                    # mutate directly instead of O(n) list.index() search
                    batch[j]["elevation"] = f"{round(elev)}m"

        except Exception as e:
            print(f"[Elevation] batch {i//BATCH + 1} error: {e}")
            continue

    return results


async def geocode_place(place_name: str) -> Dict[str, Any]:
    """Convert place name to coordinates + bounding box using Nominatim."""
    try:
        await rate_limiter.wait("nominatim.openstreetmap.org", 1.2)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": place_name,
                    "format": "json",
                    "limit": 5,
                    "addressdetails": 1,
                },
                headers={
                    "Accept-Language": "en",
                    "User-Agent": "WildDataExtractor/1.0 (gowild.co.in)",
                },
            )
            resp.raise_for_status()
            return resp.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
        return []

import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from utils.rate_limiter import rate_limiter

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary"
DEFAULT_USER_AGENT = "WildData/1.0 (gowild.co.in)"

OUTDOOR_KEYWORDS = [
    'fall', 'falls', 'cascade', 'waterfall',
    'peak', 'mount', 'mountain', 'hill', 'summit',
    'pass', 'col', 'saddle',
    'trail', 'trek', 'hike', 'hiking', 'path',
    'park', 'reserve', 'sanctuary', 'refuge', 'conservation',
    'forest', 'wood', 'jungle',
    'cave', 'cavern', 'grotto',
    'lake', 'gorge', 'canyon', 'valley',
    'beach', 'coast', 'bay', 'cove',
    'spring', 'thermal', 'geyser',
    'glacier', 'icefield',
    'volcano', 'crater',
    'viewpoint', 'lookout', 'overlook',
    'island', 'archipelago',
    'desert', 'dune',
    'reef', 'lagoon',
    'hot spring', 'national park', 'wildlife',
]

def is_outdoor_relevant(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in OUTDOOR_KEYWORDS)

# Lookup table replaces 11 if-statements — reduces cyclomatic complexity from D(23) to A
_TYPE_RULES: List[tuple] = [
    (('fall', 'falls', 'cascade', 'waterfall'),          'Waterfall',     'waterfall'),
    (('peak', 'mount', 'mountain', 'summit', 'hill'),    'Mountain Peak', 'peak'),
    (('park', 'reserve', 'sanctuary', 'conservation'),   'National Park', 'park'),
    (('trail', 'trek', 'hike', 'pass', 'col'),           'Hiking Route',  'hiking'),
    (('cave', 'cavern', 'grotto'),                       'Cave',          'cave'),
    (('lake', 'reservoir', 'pond', 'tarn', 'loch'),       'Lake',          'lake'),
    (('beach', 'coast', 'bay', 'cove'),                  'Beach',         'beach'),
    (('spring', 'thermal', 'geyser'),                    'Hot Spring',    'hot_spring'),
    (('glacier', 'icefield'),                            'Glacier',       'glacier'),
    (('volcano', 'crater'),                              'Volcano',       'volcano'),
    (('viewpoint', 'lookout', 'overlook'),               'Viewpoint',     'viewpoint'),
]

def guess_type(title: str) -> tuple:
    """Returns (type_label, type_id) by matching title against keyword rules."""
    t = title.lower()
    for keywords, label, type_id in _TYPE_RULES:
        if any(w in t for w in keywords):
            return label, type_id
    return 'Natural Feature', 'viewpoint'

async def fetch_wikipedia_geo(
    lat: float,
    lng: float,
    radius_m: int,
    feature_ids: List[str],
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Wikipedia GeoSearch — finds Wikipedia articles near coordinates or within bbox.
    Max radius per call is 10000m. For larger areas, does multiple offset calls.
    Strictly filters results within the bbox and requested feature_ids.
    """
    # Wikipedia max radius is 10km per call
    max_radius = min(radius_m, 10000)

    # For large radius, do multiple calls with offsets
    offsets = [(0, 0)]
    if radius_m > 10000:
        deg = radius_m / 111000
        offsets += [
            (deg * 0.5, 0),
            (0, deg * 0.5),
            (-deg * 0.5, 0),
            (0, -deg * 0.5),
        ]

    seen_titles = set()

    for dlat, dlng in offsets:
        try:
            await rate_limiter.wait("en.wikipedia.org", 0.5)
            async with httpx.AsyncClient(timeout=30, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}) as client:
                resp = await client.get(WIKI_API, params={
                    "action": "query",
                    "list": "geosearch",
                    "gscoord": f"{lat + dlat}|{lng + dlng}",
                    "gsradius": max_radius,
                    "gslimit": 50,
                    "format": "json",
                    "origin": "*",
                })
                resp.raise_for_status()
                data = resp.json()

            pages = data.get("query", {}).get("geosearch", [])

            for page in pages:
                title = page.get("title", "")
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                if not is_outdoor_relevant(title):
                    continue
                
                p_lat = page.get("lat", 0)
                p_lng = page.get("lon", 0)
                
                # Strict BBOX filtering
                if bbox:
                    s, w, n, e = bbox
                    if not (s <= p_lat <= n and w <= p_lng <= e):
                        continue

                type_label, type_id = guess_type(title)

                # Only keep articles whose inferred type is in the user's selection.
                # (Previously, selecting "viewpoint" incorrectly allowed every article through.)
                if type_id not in feature_ids:
                    continue

                wiki_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"

                yield {
                    "name": title,
                    "type": type_label,
                    "type_id": type_id,
                    "lat": p_lat,
                    "lng": p_lng,
                    "elevation": "",
                    "description": "",  # fetched separately in enrichment
                    "wikipedia": wiki_url,
                    "wikipedia_title": title,
                    "website": "",
                    "region": "",
                    "country": "",
                    "image": "",
                    "osm_id": "",
                    "source": "Wikipedia",
                    "confidence": "High",  # Wikipedia always has a proper name
                }

        except Exception as e:
            print(f"[Wikipedia GeoSearch] error: {e}")
            continue

async def fetch_wikipedia_summary(title: str) -> Dict[str, str]:
    """Fetch summary + image for a Wikipedia article title."""
    try:
        await rate_limiter.wait("en.wikipedia.org", 0.3)
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}) as client:
            resp = await client.get(
                f"{WIKI_SUMMARY}/{title.replace(' ', '_')}",
            )
            if resp.status_code != 200:
                return {}
            data = resp.json()
            return {
                "description": data.get("extract", "")[:500],
                "image": data.get("thumbnail", {}).get("source", ""),
            }
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError, KeyError):
        return {}

async def enrich_wikipedia_descriptions(
    results: List[Dict[str, Any]],
    max_enrichments: int = 100,
) -> List[Dict[str, Any]]:
    """
    For results that have a Wikipedia URL but no description,
    fetch the summary. Capped at max_enrichments to respect rate limits.
    """
    enriched = 0
    for r in results:
        if enriched >= max_enrichments:
            break
        if r.get("wikipedia") and not r.get("description"):
            title = r.get("wikipedia_title") or r.get("wikipedia", "").split("/wiki/")[-1]
            if not title:  # Skip if no valid title
                continue
            try:
                info = await fetch_wikipedia_summary(title)
                if info.get("description"):
                    r["description"] = info["description"]
                    enriched += 1
                if info.get("image") and not r.get("image"):
                    r["image"] = info["image"]
                    enriched += 1
            except Exception as e:
                print(f"[Wikipedia] Error fetching summary for {title}: {e}")
                continue
    return results

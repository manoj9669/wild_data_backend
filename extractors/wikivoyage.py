"""
WikiVoyage extractor — 100% free, no API key required.

WikiVoyage is Wikimedia's open travel guide (Creative Commons licence).
It has geo-referenced articles for outdoor destinations, national parks,
hiking regions, mountain ranges, beaches, and adventure travel spots
with rich on-the-ground descriptions written by travellers.

Two-step process:
1. geosearch   → articles near coordinates (Wikipedia geosearch works on WikiVoyage too)
2. extracts    → first section of each matching article (the intro description)
"""

import re
import httpx
from typing import AsyncGenerator, Dict, Any, List
from utils.rate_limiter import rate_limiter

BASE = "https://en.wikivoyage.org/w/api.php"

# Keywords in the article title / intro that signal an outdoor destination
OUTDOOR_SIGNALS = [
    "national park", "nature reserve", "wildlife", "mountain", "peak", "trek",
    "hike", "hiking", "trail", "waterfall", "falls", "lake", "beach", "forest",
    "gorge", "canyon", "valley", "glacier", "volcano", "hot spring", "cave",
    "camp", "camping", "refuge", "viewpoint", "summit", "ridge", "island",
    "bay", "cape", "coast", "dunes", "savanna", "jungle", "rainforest",
]

# Type signals → WildData type_id
TITLE_TYPE_MAP = [
    (["national park", "nature reserve", "wildlife reserve", "nature park"], "park"),
    (["waterfall", " falls", "cascade"],                                     "waterfall"),
    (["mountain", "peak", "summit", "massif", "range", "ridge"],            "peak"),
    (["lake", " lagoon", "reservoir"],                                       "lake"),
    (["beach", "coast", "bay", "cape", "cove"],                             "beach"),
    (["glacier", "icefield"],                                                "glacier"),
    (["volcano", "volcanic"],                                                "volcano"),
    (["cave", "cavern", "grotto"],                                           "cave"),
    (["hot spring", "thermal"],                                              "hot_spring"),
    (["gorge", "canyon", "ravine", "chasm"],                                "gorge"),
    (["forest", "jungle", "rainforest", "woodland"],                        "forest"),
    (["viewpoint", "lookout", "overlook", "vista", "scenic"],               "viewpoint"),
    (["hiking", "trek", "trail"],                                            "hiking"),
]


def _infer_type(title: str, extract: str) -> str:
    text = (title + " " + extract[:300]).lower()
    for keywords, type_id in TITLE_TYPE_MAP:
        if any(kw in text for kw in keywords):
            return type_id
    return "attraction"


def _clean_extract(raw: str) -> str:
    """Strip WikiVoyage markup remnants and trim to 3 sentences."""
    text = re.sub(r'\[\[.*?\]\]', '', raw)       # wikilinks
    text = re.sub(r'\{\{.*?\}\}', '', text)       # templates
    text = re.sub(r"'''?", '', text)              # bold/italic
    text = re.sub(r'==.*?==', '', text)           # headings
    text = re.sub(r'\s+', ' ', text).strip()
    # Keep first 3 sentences
    sents = re.split(r'(?<=[.!?])\s+', text)
    return ' '.join(sents[:3]).strip()


async def fetch_wikivoyage(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 150,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Geo-search WikiVoyage for outdoor destination articles near coordinates.
    Returns places with rich travel descriptions.
    """
    radius_m = min(int(radius_km * 1000), 10000)  # WikiVoyage geosearch caps at 10 km
    # For large radii, run multiple offset calls
    offsets = [0]
    if radius_km > 10:
        offsets = [0, 1, 2, 3, 4]  # 5 × 20 results = up to 100 articles

    seen_titles: set = set()

    async with httpx.AsyncClient(
        timeout=20,
        headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
    ) as client:

        # ── Step 1: Geo-search ──────────────────────────────────────────────
        hits = []
        for offset in offsets:
            try:
                await rate_limiter.wait("en.wikivoyage.org", 1.0)
                resp = await client.get(BASE, params={
                    "action":   "query",
                    "list":     "geosearch",
                    "gscoord":  f"{lat}|{lng}",
                    "gsradius": radius_m,
                    "gslimit":  "20",
                    "gsoffset": offset * 20,
                    "format":   "json",
                    "origin":   "*",
                })
                if resp.status_code != 200:
                    break
                data = resp.json()
                batch = data.get("query", {}).get("geosearch", [])
                if not batch:
                    break
                hits.extend(batch)
            except Exception as e:
                print(f"[WikiVoyage] geosearch error: {e}")
                break

        if not hits:
            return

        # Filter to outdoor-relevant articles only
        outdoor_hits = [
            h for h in hits
            if any(sig in h.get("title", "").lower() for sig in OUTDOOR_SIGNALS)
        ]
        if not outdoor_hits:
            outdoor_hits = hits  # fall back to all hits if none match keywords

        # ── Step 2: Fetch extracts in batches of 10 ────────────────────────
        BATCH = 10
        for i in range(0, min(len(outdoor_hits), limit), BATCH):
            batch = outdoor_hits[i:i + BATCH]
            titles = "|".join(h["title"] for h in batch if h["title"] not in seen_titles)
            if not titles:
                continue

            try:
                await rate_limiter.wait("en.wikivoyage.org", 1.0)
                resp = await client.get(BASE, params={
                    "action":      "query",
                    "prop":        "extracts|coordinates|pageimages",
                    "titles":      titles,
                    "exintro":     "true",
                    "explaintext": "true",
                    "exsentences": "5",
                    "piprop":      "thumbnail",
                    "pithumbsize": "400",
                    "format":      "json",
                    "origin":      "*",
                })
                if resp.status_code != 200:
                    continue

                pages = resp.json().get("query", {}).get("pages", {}).values()
                for page in pages:
                    title = page.get("title", "")
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)

                    extract_raw = page.get("extract", "")
                    if not extract_raw or len(extract_raw) < 40:
                        continue

                    # Skip disambiguation and "may refer to" pages
                    if "may refer to" in extract_raw.lower():
                        continue

                    description = _clean_extract(extract_raw)
                    if not description:
                        continue

                    # Infer type
                    type_id = _infer_type(title, description)
                    if feature_ids and type_id not in feature_ids:
                        # Relax: still yield if title has strong outdoor signal
                        if not any(sig in title.lower() for sig in OUTDOOR_SIGNALS):
                            continue

                    # Coordinates — prefer page coords, fall back to geosearch hit
                    coords = page.get("coordinates", [{}])
                    hit = next((h for h in batch if h.get("title") == title), {})
                    p_lat = coords[0].get("lat") if coords else hit.get("lat")
                    p_lng = coords[0].get("lon") if coords else hit.get("lon")
                    if not p_lat or not p_lng:
                        continue

                    image = (page.get("thumbnail") or {}).get("source", "")
                    wiki_url = f"https://en.wikivoyage.org/wiki/{title.replace(' ', '_')}"

                    yield {
                        "name":        title,
                        "type":        type_id.replace("_", " ").title(),
                        "type_id":     type_id,
                        "lat":         round(float(p_lat), 6),
                        "lng":         round(float(p_lng), 6),
                        "elevation":   "",
                        "description": description,
                        "wikipedia":   wiki_url,
                        "website":     wiki_url,
                        "region":      "",
                        "country":     "",
                        "image":       image,
                        "osm_id":      "",
                        "source":      "WikiVoyage",
                        "confidence":  "Medium",
                    }

            except Exception as e:
                print(f"[WikiVoyage] extract error: {e}")

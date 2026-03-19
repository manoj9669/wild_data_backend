import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

SPARQL_URL = "https://query.wikidata.org/sparql"

# Wikidata entity IDs for outdoor features
WIKIDATA_CLASSES = {
    "waterfall":  "wd:Q瀑布 wd:Q901648",   # replaced below
    "peak":       "wd:Q8502",
    "park":       "wd:Q46169",
    "cave":       "wd:Q35509",
    "hot_spring": "wd:Q177380",
    "viewpoint":  "wd:Q578439",
    "beach":      "wd:Q40080",
    "waterway":   "wd:Q23397",
    "glacier":    "wd:Q35666",
    "volcano":    "wd:Q8072",
    "forest":     "wd:Q4421",
    "camp":       "wd:Q832778",
}

# Correct Wikidata IDs
WIKIDATA_IDS = {
    "waterfall":  "wd:Q瀑布",
    "peak":       "wd:Q8502",
    "park":       "wd:Q46169",
    "cave":       "wd:Q35509",
    "hot_spring": "wd:Q177380",
    "viewpoint":  "wd:Q578439",
    "beach":      "wd:Q40080",
    "waterway":   "wd:Q23397",
    "glacier":    "wd:Q35666",
    "volcano":    "wd:Q8072",
    "forest":     "wd:Q4421",
}

# Corrected IDs (Chinese char was a placeholder)
CORRECT_IDS = {
    "waterfall":  "wd:Q瀑布",
}

REAL_IDS = {
    "waterfall":  "wd:Q355304",   # waterfall
    "peak":       "wd:Q8502",     # mountain
    "park":       "wd:Q46169",    # national park
    "cave":       "wd:Q35509",    # cave
    "hot_spring": "wd:Q177380",   # hot spring
    "viewpoint":  "wd:Q578439",   # viewpoint
    "beach":      "wd:Q40080",    # beach
    "waterway":   "wd:Q23397",    # lake
    "glacier":    "wd:Q35666",    # glacier
    "volcano":    "wd:Q8072",     # volcano
    "forest":     "wd:Q4421",     # forest
    "camp":       "wd:Q832778",   # campsite
}

FEATURE_LABELS = {
    "waterfall": "Waterfall", "peak": "Mountain Peak", "park": "National Park",
    "cave": "Cave", "hot_spring": "Hot Spring", "viewpoint": "Viewpoint",
    "beach": "Beach", "waterway": "River / Lake", "glacier": "Glacier",
    "volcano": "Volcano", "forest": "Forest", "camp": "Campsite",
}

async def fetch_wikidata(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 300,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch features from Wikidata SPARQL endpoint.
    Yields one result dict at a time.
    """

    for fid in feature_ids:
        wd_class = REAL_IDS.get(fid)
        if not wd_class:
            continue

        label = FEATURE_LABELS.get(fid, fid)
        effective_limit = min(limit, 500)

        sparql = f"""
SELECT DISTINCT ?item ?itemLabel ?coord ?elev ?desc ?image ?article WHERE {{
  SERVICE wikibase:around {{
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:center "Point({lng} {lat})"^^geo:wktLiteral .
    bd:serviceParam wikibase:radius "{radius_km}" .
  }}
  ?item wdt:P31/wdt:P279* {wd_class} .
  OPTIONAL {{ ?item wdt:P2044 ?elev }}
  OPTIONAL {{ ?item schema:description ?desc . FILTER(LANG(?desc) = "en") }}
  OPTIONAL {{ ?item wdt:P18 ?image }}
  OPTIONAL {{
    ?article schema:about ?item ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,hi,fr,de,es,it,ja,zh". }}
}}
LIMIT {effective_limit}
""".strip()

        try:
            await rate_limiter.wait("query.wikidata.org", 0.5)
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(
                    SPARQL_URL,
                    params={"query": sparql, "format": "json"},
                    headers={
                        "Accept": "application/sparql-results+json",
                        "User-Agent": "WildDataExtractor/1.0 (gowild.co.in)",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            bindings = data.get("results", {}).get("bindings", [])

            for b in bindings:
                coord_str = b.get("coord", {}).get("value", "")
                import re
                match = re.search(r"Point\(([^ ]+) ([^)]+)\)", coord_str)
                if not match:
                    continue

                item_lng = float(match.group(1))
                item_lat = float(match.group(2))
                name = b.get("itemLabel", {}).get("value", "")
                # Skip if name is just the Wikidata ID (Q123456)
                if name.startswith("Q") and name[1:].isdigit():
                    name = ""

                wiki_url = ""
                article = b.get("article", {}).get("value", "")
                if article:
                    wiki_url = article
                elif name:
                    wiki_url = f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}"

                elev = b.get("elev", {}).get("value", "")
                if elev:
                    try:
                        elev = str(round(float(elev))) + "m"
                    except:
                        elev = ""

                image = b.get("image", {}).get("value", "")
                # Convert Wikidata image to thumbnail URL
                if image and "Special:FilePath" not in image:
                    image = f"https://commons.wikimedia.org/wiki/Special:FilePath/{image.split('/')[-1]}?width=400"

                yield {
                    "name": name,
                    "type": label,
                    "type_id": fid,
                    "lat": item_lat,
                    "lng": item_lng,
                    "elevation": elev,
                    "description": b.get("desc", {}).get("value", ""),
                    "wikipedia": wiki_url,
                    "website": "",
                    "region": "",
                    "country": "",
                    "image": image,
                    "osm_id": "",
                    "source": "Wikidata",
                    "confidence": "High" if name else "Low",
                }

        except Exception as e:
            print(f"[Wikidata] {fid} error: {e}")
            continue

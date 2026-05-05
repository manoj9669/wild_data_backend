import re
import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

SPARQL_URL = "https://query.wikidata.org/sparql"

# Wikidata entity IDs per feature — supports multiple entities (joined in VALUES clause)
REAL_IDS: Dict[str, List[str]] = {
    "waterfall":  ["wd:Q355304", "wd:Q913785"],   # waterfall, swimming hole
    "peak":       ["wd:Q8502"],                    # mountain
    "park":       ["wd:Q46169", "wd:Q473972"],     # national park, protected area
    "cave":       ["wd:Q35509"],                   # cave
    "hot_spring": ["wd:Q177380"],                  # hot spring
    "viewpoint":  ["wd:Q578439"],                  # viewpoint
    "beach":      ["wd:Q40080"],                   # beach
    "lake":   ["wd:Q23397", "wd:Q4022"],       # lake, river
    "glacier":    ["wd:Q35666"],                   # glacier
    "volcano":    ["wd:Q8072"],                    # volcano
    "forest":     ["wd:Q4421"],                    # forest
    "camp":       ["wd:Q832778"],                  # campsite
    "mtb":        ["wd:Q1326645"],                 # mountain biking trail
    "hiking":     ["wd:Q2143825"],                 # hiking trail
}

FEATURE_LABELS = {
    "waterfall": "Waterfall", "peak": "Mountain Peak", "park": "National Park",
    "cave": "Cave", "hot_spring": "Hot Spring", "viewpoint": "Viewpoint",
    "beach": "Beach", "lake": "River / Lake", "glacier": "Glacier",
    "volcano": "Volcano", "forest": "Forest", "camp": "Campsite",
    "mtb": "MTB Trail", "hiking": "Hiking Trail",
}

# Per-entity label overrides (when a feature has multiple Wikidata classes)
_ENTITY_LABELS = {
    "wd:Q913785": "Swimming Hole",
    "wd:Q4022":   "River",
    "wd:Q23397":  "Lake",
}


async def fetch_wikidata(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 300,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch features from Wikidata SPARQL using wikibase:around SERVICE.
    Yields one result dict at a time.
    """
    for fid in feature_ids:
        wd_classes = REAL_IDS.get(fid)
        if not wd_classes:
            continue

        label = FEATURE_LABELS.get(fid, fid)
        effective_limit = min(limit, 500)
        values_clause = " ".join(wd_classes)

        sparql = f"""
SELECT DISTINCT ?item ?itemLabel ?type ?coord ?elev ?desc ?image ?article WHERE {{
  SERVICE wikibase:around {{
    ?item wdt:P625 ?coord.
    bd:serviceParam wikibase:center "Point({lng} {lat})"^^geo:wktLiteral.
    bd:serviceParam wikibase:radius "{radius_km}".
  }}
  VALUES ?type {{ {values_clause} }}
  ?item wdt:P31 ?type .
  # Require a Wikipedia article — filters out obscure/misclassified items.
  # If it's not notable enough for Wikipedia it belongs in OSM/GeoNames, not Wikidata.
  ?article schema:about ?item ;
           schema:isPartOf <https://en.wikipedia.org/> .
  OPTIONAL {{ ?item wdt:P2044 ?elev }}
  OPTIONAL {{ ?item schema:description ?desc . FILTER(LANG(?desc) = "en") }}
  OPTIONAL {{ ?item wdt:P18 ?image }}
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
                match = re.search(r"Point\(([^ ]+) ([^)]+)\)", coord_str)
                if not match:
                    continue

                item_lng = float(match.group(1))
                item_lat = float(match.group(2))
                name = b.get("itemLabel", {}).get("value", "")
                desc = b.get("desc", {}).get("value", "").lower()

                # Skip bare Wikidata IDs (Q123456)
                if name.startswith("Q") and name[1:].isdigit():
                    name = ""

                # Skip coordinate-style labels (e.g. "32.67°N 76.26°E")
                if re.match(r"^[\d.]+[°\s]*[NS]", name):
                    name = ""

                # For waterfall queries: skip rivers and streams misclassified in Wikidata
                if fid == "waterfall" and any(w in desc for w in (
                    "river", "stream", "nadi", "nāla", "nala", "khad", "nāl", "rivulet", "creek", "brook"
                )):
                    continue

                # Resolve per-entity label when feature has multiple Wikidata classes
                type_entity = b.get("type", {}).get("value", "")
                type_wd = "wd:" + type_entity.split("/")[-1] if type_entity else ""
                type_label = _ENTITY_LABELS.get(type_wd, label)

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
                    except (ValueError, TypeError):
                        elev = ""

                image = b.get("image", {}).get("value", "")
                if image and "Special:FilePath" not in image:
                    image = f"https://commons.wikimedia.org/wiki/Special:FilePath/{image.split('/')[-1]}?width=400"

                yield {
                    "name": name,
                    "type": type_label,
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

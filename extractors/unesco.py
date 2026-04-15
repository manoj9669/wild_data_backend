"""
UNESCO World Heritage Sites Extractor.

Primary source: Wikidata SPARQL (Q9259 = UNESCO World Heritage Site)
Fallback: OSM Overpass (heritage=1 / heritage:operator=UNESCO tags)
"""

import math
import httpx
from typing import Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def fetch_unesco_sites(
    lat: float,
    lng: float,
    radius_km: float,
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch UNESCO World Heritage Sites near coordinates.
    Primary: Wikidata SPARQL (Q9259). Fallback: OSM Overpass heritage tags.
    """
    seen_ids: set = set()

    # Bounding box for SPARQL filter (generous padding)
    pad = radius_km / 111.0
    lat_min, lat_max = lat - pad, lat + pad
    lon_min, lon_max = lng - pad, lng + pad

    async with httpx.AsyncClient(timeout=30) as client:

        # ── Source 1: Wikidata SPARQL ──────────────────────────────────────
        sparql = f"""
SELECT ?item ?itemLabel ?lat ?lon ?article ?image ?countryLabel WHERE {{
  ?item wdt:P1435 wd:Q9259 .
  ?item p:P625 ?coord .
  ?coord psv:P625 ?coordv .
  ?coordv wikibase:geoLatitude ?lat .
  ?coordv wikibase:geoLongitude ?lon .
  FILTER(?lat > {lat_min} && ?lat < {lat_max} && ?lon > {lon_min} && ?lon < {lon_max})
  OPTIONAL {{ ?item wdt:P18 ?image . }}
  OPTIONAL {{ ?item wdt:P17 ?country . }}
  OPTIONAL {{
    ?article schema:about ?item ;
             schema:inLanguage "en" ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}}
LIMIT {limit}
"""
        try:
            await rate_limiter.wait("query.wikidata.org", 1.0)
            resp = await client.get(
                WIKIDATA_SPARQL,
                params={"query": sparql, "format": "json"},
                headers={"Accept": "application/json", "User-Agent": "WildDataBot/1.0"},
            )
            if resp.status_code == 200:
                bindings = resp.json().get("results", {}).get("bindings", [])
                for row in bindings:
                    try:
                        site_lat = float(row["lat"]["value"])
                        site_lng = float(row["lon"]["value"])
                    except (KeyError, ValueError):
                        continue

                    if _haversine(lat, lng, site_lat, site_lng) > radius_km:
                        continue

                    qid = row["item"]["value"].split("/")[-1]
                    if qid in seen_ids:
                        continue
                    seen_ids.add(qid)

                    name = row.get("itemLabel", {}).get("value", "")
                    if not name or name == qid:
                        continue

                    wiki_url = row.get("article", {}).get("value", "")
                    if not wiki_url:
                        wiki_url = f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}"

                    image = row.get("image", {}).get("value", "")
                    country = row.get("countryLabel", {}).get("value", "")

                    yield {
                        "name": name,
                        "type": "UNESCO Heritage Site",
                        "type_id": "unesco",
                        "lat": site_lat,
                        "lng": site_lng,
                        "elevation": "",
                        "description": "UNESCO World Heritage Site",
                        "wikipedia": wiki_url,
                        "website": f"https://www.wikidata.org/wiki/{qid}",
                        "region": "",
                        "country": country,
                        "image": image,
                        "osm_id": "",
                        "source": "UNESCO (Wikidata)",
                        "confidence": "High",
                    }
            else:
                print(f"[UNESCO] Wikidata SPARQL HTTP {resp.status_code}")
        except Exception as e:
            print(f"[UNESCO] Wikidata SPARQL error: {e}")

        # ── Source 2: OSM Overpass fallback ───────────────────────────────
        overpass_query = f"""
[out:json][timeout:60];
(
  node["heritage"="1"](around:{int(radius_km * 1000)},{lat},{lng});
  way["heritage"="1"](around:{int(radius_km * 1000)},{lat},{lng});
  relation["heritage"="1"](around:{int(radius_km * 1000)},{lat},{lng});
  node["heritage:operator"~"UNESCO|whc",i](around:{int(radius_km * 1000)},{lat},{lng});
  way["heritage:operator"~"UNESCO|whc",i](around:{int(radius_km * 1000)},{lat},{lng});
  relation["heritage:operator"~"UNESCO|whc",i](around:{int(radius_km * 1000)},{lat},{lng});
);
out center;
"""
        try:
            await rate_limiter.wait("overpass-api.de", 1.5)
            resp = await client.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": overpass_query},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 200:
                for el in resp.json().get("elements", []):
                    tags = el.get("tags", {})
                    name = tags.get("name:en") or tags.get("name") or tags.get("int_name", "")
                    if not name:
                        wiki = tags.get("wikipedia", "")
                        if wiki and ":" in wiki:
                            name = wiki.split(":", 1)[1].replace("_", " ")
                    if not name:
                        continue

                    osm_id = str(el.get("id", ""))
                    if osm_id in seen_ids:
                        continue
                    seen_ids.add(osm_id)

                    el_lat = el.get("lat") or (el.get("center") or {}).get("lat")
                    el_lng = el.get("lon") or (el.get("center") or {}).get("lon")
                    if not el_lat or not el_lng:
                        continue
                    if _haversine(lat, lng, el_lat, el_lng) > radius_km:
                        continue

                    wiki_url = ""
                    wiki_tag = tags.get("wikipedia", "")
                    if wiki_tag and ":" in wiki_tag:
                        lang, page = wiki_tag.split(":", 1)
                        wiki_url = f"https://{lang}.wikipedia.org/wiki/{page.replace(' ', '_')}"

                    yield {
                        "name": name,
                        "type": "UNESCO Heritage Site",
                        "type_id": "unesco",
                        "lat": el_lat,
                        "lng": el_lng,
                        "elevation": "",
                        "description": "UNESCO World Heritage Site",
                        "wikipedia": wiki_url,
                        "website": tags.get("website") or tags.get("url") or "",
                        "region": tags.get("addr:state") or tags.get("is_in:state", ""),
                        "country": tags.get("addr:country") or tags.get("is_in:country", ""),
                        "image": tags.get("image") or "",
                        "osm_id": f"{el.get('type', 'node')}/{osm_id}",
                        "source": "UNESCO (OSM Heritage)",
                        "confidence": "High" if wiki_url else "Medium",
                    }
        except Exception as e:
            print(f"[UNESCO] Overpass error: {e}")

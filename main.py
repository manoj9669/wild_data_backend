import asyncio
import json
from typing import List, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from extractors.osm import fetch_osm
from extractors.opentripmap import fetch_opentripmap
from extractors.wikipedia import fetch_wikipedia_geo, enrich_wikipedia_descriptions
from extractors.geonames import fetch_geonames
from extractors.foursquare import fetch_foursquare
from extractors.geoapify import fetch_geoapify
from extractors.waymarked import fetch_waymarked
from extractors.protected_planet import fetch_protected_planet
from extractors.unesco import fetch_unesco_sites
from extractors.enrichment import enrich_geocoding, enrich_elevation, geocode_place
from extractors.countries import fetch_country_specific, COUNTRY_EXTRACTORS
from extractors.ai_enricher import enrich_with_ai
from extractors.here import fetch_here
from extractors.inaturalist import fetch_inaturalist
from extractors.refuges import fetch_refuges
from utils.deduplicator import deduplicate

app = FastAPI(
    title="WildData API",
    description="Outdoor feature extractor — OSM, Wikidata, Wikipedia, GeoNames, Waymarked Trails, Protected Planet, Government sources",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OSM_HIGH = {
    "FR","DE","GB","IT","ES","NL","BE","AT","CH","NO","SE","FI","DK","PL","CZ",
    "US","CA","AU","NZ","JP","PT","GR","HU","RO","SK","SI","HR","RS","BG","CY",
    "LV","LT","EE","LU","MT","IS","BR","ZA","AR","CL","CO","MX","KR","TW","SG",
}
OSM_LOW = {"IN","NP","PK","BD","MM","KH","LA","AF","IQ","SY","LY","SD","ET","SO","MG","CN","VN","KH"}

def osm_quality(cc: str) -> str:
    if cc in OSM_HIGH:
        return "high"
    if cc in OSM_LOW:
        return "low"
    return "med"


async def _collect(gen) -> List[dict]:
    """Drain an async generator into a list."""
    results = []
    async for item in gen:
        results.append(item)
    return results


async def _empty() -> List[dict]:
    """Return empty list — used as no-op placeholder in gather."""
    return []


@app.get("/search")
async def search_place(q: str = Query(..., min_length=2)):
    results = await geocode_place(q)
    return JSONResponse(content=results)


@app.get("/extract")
async def extract(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(25, ge=1, le=5000),
    features: str = Query("waterfall,peak,hiking"),
    limit: int = Query(300, ge=10, le=2000),
    country_code: str = Query(""),
    state_code: str = Query(""),
    district_code: str = Query(""),
    do_enrich_wiki: bool = Query(True),
    do_enrich_elevation: bool = Query(True),
    do_enrich_geocoding: bool = Query(True),
    do_enrich_ai: bool = Query(True),
    use_foursquare: bool = Query(False),
    use_geoapify: bool = Query(False),
    use_here: bool = Query(False),
    use_inaturalist: bool = Query(False),
    search_mode: str = Query(""),
    region_bbox: str = Query(""),
):
    feature_ids = [f.strip() for f in features.split(",") if f.strip()]
    cc = country_code.upper()
    quality = osm_quality(cc)

    bbox_tuple = None
    if region_bbox and search_mode == "region":
        try:
            parts = [float(x) for x in region_bbox.split(",")]
            if len(parts) == 4:
                bbox_tuple = (parts[0], parts[1], parts[2], parts[3])
        except (ValueError, AttributeError):
            pass

    osm_limit = limit if quality == "high" else min(limit, 150)
    trail_features = [f for f in feature_ids if f in ("hiking", "mtb")]

    async def generate():
        all_results = []

        try:
            yield json.dumps({"type": "progress", "stage": "fetching",
                              "message": "Fetching from all sources in parallel...", "count": 0}) + "\n"

            # ── Parallel fetch all data sources ───────────────────────────
            tasks = [
                _collect(fetch_osm(lat, lng, int(radius_km * 1000), feature_ids, osm_limit, bbox=bbox_tuple)),
                _collect(fetch_opentripmap(lat, lng, radius_km, feature_ids, limit=200, bbox=bbox_tuple)),
                _collect(fetch_wikipedia_geo(lat, lng, int(radius_km * 1000))),
                _collect(fetch_geonames(lat, lng, radius_km, feature_ids, country_code=cc, limit=100)),
                _collect(fetch_waymarked(lat, lng, radius_km, trail_features, limit=100)) if trail_features else _empty(),
                _collect(fetch_protected_planet(lat, lng, radius_km, feature_ids, country_code=cc)) if any(f in feature_ids for f in ("park", "forest")) else _empty(),
                _collect(fetch_country_specific(cc, lat, lng, radius_km, feature_ids)) if cc and cc in COUNTRY_EXTRACTORS else _empty(),
                _collect(fetch_unesco_sites(lat, lng, radius_km, limit=100)) if "unesco" in feature_ids else _empty(),
                _collect(fetch_refuges(lat, lng, radius_km, feature_ids)) if any(f in feature_ids for f in ("hut", "camp")) else _empty(),
                _collect(fetch_geoapify(lat, lng, radius_km, feature_ids, limit=limit)) if use_geoapify else _empty(),
                _collect(fetch_foursquare(lat, lng, radius_km, feature_ids, limit=limit)) if use_foursquare else _empty(),
                _collect(fetch_here(lat, lng, radius_km, feature_ids, limit=limit, bbox=bbox_tuple)) if use_here else _empty(),
                _collect(fetch_inaturalist(lat, lng, radius_km, feature_ids, limit=limit, bbox=bbox_tuple)) if use_inaturalist else _empty(),
            ]

            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            stage_names = ["OSM", "OpenTripMap", "Wikipedia", "GeoNames", "Waymarked",
                           "Protected Planet", "Country", "UNESCO", "Refuges",
                           "Geoapify", "Foursquare", "HERE", "iNaturalist"]

            for name, res in zip(stage_names, results_list):
                if isinstance(res, Exception):
                    print(f"[{name}] error: {res}")
                    continue
                if isinstance(res, list) and res:
                    all_results.extend(res)
                    yield json.dumps({"type": "results", "data": res}) + "\n"

            yield json.dumps({"type": "progress", "stage": "fetching",
                              "message": f"All sources done — {len(all_results)} raw features",
                              "count": len(all_results)}) + "\n"

            # ── Deduplicate ────────────────────────────────────────────────
            yield json.dumps({"type": "progress", "stage": "dedup",
                              "message": "Deduplicating results...", "count": len(all_results)}) + "\n"
            all_results = deduplicate(all_results)
            yield json.dumps({"type": "progress", "stage": "dedup",
                              "message": f"After dedup: {len(all_results)} unique features",
                              "count": len(all_results)}) + "\n"

            # ── Enrich Wikipedia ───────────────────────────────────────────
            if do_enrich_wiki:
                yield json.dumps({"type": "progress", "stage": "wiki_enrich",
                                  "message": "Fetching Wikipedia descriptions...",
                                  "count": len(all_results)}) + "\n"
                all_results = await enrich_wikipedia_descriptions(all_results, max_enrichments=80)
                yield json.dumps({"type": "progress", "stage": "wiki_enrich",
                                  "message": "Wikipedia enrichment done",
                                  "count": len(all_results)}) + "\n"

            # ── Reverse geocoding ──────────────────────────────────────────
            if do_enrich_geocoding:
                yield json.dumps({"type": "progress", "stage": "geocoding",
                                  "message": "Reverse geocoding...",
                                  "count": len(all_results)}) + "\n"
                all_results = await enrich_geocoding(all_results, max_calls=100)
                yield json.dumps({"type": "progress", "stage": "geocoding",
                                  "message": "Geocoding done", "count": len(all_results)}) + "\n"

            # ── Elevation ──────────────────────────────────────────────────
            if do_enrich_elevation:
                yield json.dumps({"type": "progress", "stage": "elevation",
                                  "message": "Fetching elevation data...",
                                  "count": len(all_results)}) + "\n"
                all_results = await enrich_elevation(all_results, max_points=150)
                yield json.dumps({"type": "progress", "stage": "elevation",
                                  "message": "Elevation done", "count": len(all_results)}) + "\n"

            # ── AI enrichment ──────────────────────────────────────────────
            if do_enrich_ai:
                yield json.dumps({"type": "progress", "stage": "ai_enrich",
                                  "message": "AI validation & description enrichment (Gemini)...",
                                  "count": len(all_results)}) + "\n"
                all_results = await enrich_with_ai(all_results, max_descriptions=50, max_validations=80)
                yield json.dumps({"type": "progress", "stage": "ai_enrich",
                                  "message": f"AI enrichment done — {len(all_results)} features",
                                  "count": len(all_results)}) + "\n"

            yield json.dumps({"type": "final", "data": all_results}) + "\n"
            yield json.dumps({"type": "done", "total": len(all_results)}) + "\n"

        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"},
    )


@app.get("/")
async def root():
    return {
        "service": "WildData API",
        "version": "2.0.0",
        "status": "running",
        "endpoints": {
            "search":  "/search?q=Kasol",
            "extract": "/extract?lat=32.01&lng=77.31&radius_km=25&features=waterfall,peak",
            "docs":    "/docs",
        },
        "supported_countries": list(COUNTRY_EXTRACTORS.keys()),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}

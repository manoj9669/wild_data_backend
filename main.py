import asyncio
import json
from typing import List, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from extractors.osm import fetch_osm
from extractors.wikipedia import fetch_wikipedia_geo, enrich_wikipedia_descriptions
from extractors.geonames import fetch_geonames
from extractors.waymarked import fetch_waymarked
from extractors.protected_planet import fetch_protected_planet
from extractors.enrichment import enrich_geocoding, enrich_elevation, geocode_place
from extractors.countries import fetch_country_specific, COUNTRY_EXTRACTORS
from extractors.ai_enricher import enrich_with_ai
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

# ── OSM quality map ───────────────────────────────────────────────────────────
OSM_HIGH = {
    "FR","DE","GB","IT","ES","NL","BE","AT","CH","NO","SE","FI","DK","PL","CZ",
    "US","CA","AU","NZ","JP","PT","GR","HU","RO","SK","SI","HR","RS","BG","CY",
    "LV","LT","EE","LU","MT","IS","BR","ZA","AR","CL","CO","MX","KR","TW","SG",
}
OSM_LOW  = {"IN","NP","PK","BD","MM","KH","LA","AF","IQ","SY","LY","SD","ET","SO","MG","CN","VN","KH"}

def osm_quality(cc: str) -> str:
    if cc in OSM_HIGH: return "high"
    if cc in OSM_LOW:  return "low"
    return "med"

# ── Geocode search endpoint ───────────────────────────────────────────────────

@app.get("/search")
async def search_place(q: str = Query(..., min_length=2)):
    """Autocomplete place search using Nominatim."""
    results = await geocode_place(q)
    return JSONResponse(content=results)

# ── Main extraction — streaming ───────────────────────────────────────────────

@app.get("/extract")
async def extract(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(25, ge=1, le=500),
    features: str = Query("waterfall,peak,hiking"),
    limit: int = Query(300, ge=10, le=2000),
    country_code: str = Query(""),
    do_enrich_wiki: bool = Query(True),
    do_enrich_elevation: bool = Query(True),
    do_enrich_geocoding: bool = Query(True),
    do_enrich_ai: bool = Query(True),
):
    """
    Main extraction endpoint — returns newline-delimited JSON (NDJSON) stream.

    Stream format:
    {"type": "progress", "stage": "osm", "message": "...", "count": 10}
    {"type": "results", "data": [...]}
    {"type": "done", "total": 150}
    {"type": "error", "message": "..."}
    """
    feature_ids = [f.strip() for f in features.split(",") if f.strip()]
    cc = country_code.upper()
    quality = osm_quality(cc)

    async def generate():
        all_results = []

        try:
            # ── Stage 1: OSM ───────────────────────────────────────────────
            yield json.dumps({"type": "progress", "stage": "osm", "message": f"Querying OpenStreetMap (quality: {quality})...", "count": len(all_results)}) + "\n"
            osm_limit = limit if quality == "high" else min(limit, 150)
            batch = []
            async for item in fetch_osm(lat, lng, int(radius_km * 1000), feature_ids, osm_limit):
                all_results.append(item)
                batch.append(item)
                if len(batch) >= 20:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                    batch = []
            if batch:
                yield json.dumps({"type": "results", "data": batch}) + "\n"
            yield json.dumps({"type": "progress", "stage": "osm", "message": f"OSM done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 3: Wikipedia GeoSearch ──────────────────────────────
            yield json.dumps({"type": "progress", "stage": "wikipedia", "message": "Wikipedia GeoSearch...", "count": len(all_results)}) + "\n"
            batch = []
            async for item in fetch_wikipedia_geo(lat, lng, int(radius_km * 1000)):
                all_results.append(item)
                batch.append(item)
                if len(batch) >= 20:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                    batch = []
            if batch:
                yield json.dumps({"type": "results", "data": batch}) + "\n"
            yield json.dumps({"type": "progress", "stage": "wikipedia", "message": f"Wikipedia done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 4: GeoNames (global) ─────────────────────────────────
            yield json.dumps({"type": "progress", "stage": "geonames", "message": "Querying GeoNames global database...", "count": len(all_results)}) + "\n"
            batch = []
            async for item in fetch_geonames(lat, lng, radius_km, feature_ids, country_code=cc, limit=100):
                all_results.append(item)
                batch.append(item)
                if len(batch) >= 20:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                    batch = []
            if batch:
                yield json.dumps({"type": "results", "data": batch}) + "\n"
            yield json.dumps({"type": "progress", "stage": "geonames", "message": f"GeoNames done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 5: Waymarked Trails (hiking / MTB / cycling) ─────────
            trail_features = [f for f in feature_ids if f in ("hiking", "mtb")]
            if trail_features:
                yield json.dumps({"type": "progress", "stage": "waymarked", "message": "Querying Waymarked Trails...", "count": len(all_results)}) + "\n"
                batch = []
                async for item in fetch_waymarked(lat, lng, radius_km, trail_features, limit=100):
                    all_results.append(item)
                    batch.append(item)
                    if len(batch) >= 20:
                        yield json.dumps({"type": "results", "data": batch}) + "\n"
                        batch = []
                if batch:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                yield json.dumps({"type": "progress", "stage": "waymarked", "message": f"Waymarked Trails done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 6: Protected Planet (global protected areas) ─────────
            if any(f in feature_ids for f in ("park", "forest")):
                yield json.dumps({"type": "progress", "stage": "protected_planet", "message": "Querying Protected Planet / WDPA...", "count": len(all_results)}) + "\n"
                batch = []
                async for item in fetch_protected_planet(lat, lng, radius_km, feature_ids, country_code=cc):
                    all_results.append(item)
                    batch.append(item)
                    if len(batch) >= 20:
                        yield json.dumps({"type": "results", "data": batch}) + "\n"
                        batch = []
                if batch:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                yield json.dumps({"type": "progress", "stage": "protected_planet", "message": f"Protected Planet done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 7: Country-specific sources ─────────────────────────
            if cc and cc in COUNTRY_EXTRACTORS:
                yield json.dumps({"type": "progress", "stage": "country", "message": f"Fetching {cc} government sources...", "count": len(all_results)}) + "\n"
                batch = []
                async for item in fetch_country_specific(cc, lat, lng, radius_km, feature_ids):
                    all_results.append(item)
                    batch.append(item)
                    if len(batch) >= 20:
                        yield json.dumps({"type": "results", "data": batch}) + "\n"
                        batch = []
                if batch:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                yield json.dumps({"type": "progress", "stage": "country", "message": f"Country sources done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 8: Deduplicate ───────────────────────────────────────
            yield json.dumps({"type": "progress", "stage": "dedup", "message": "Deduplicating results...", "count": len(all_results)}) + "\n"
            all_results = deduplicate(all_results)
            yield json.dumps({"type": "progress", "stage": "dedup", "message": f"After dedup: {len(all_results)} unique features", "count": len(all_results)}) + "\n"

            # ── Stage 9: Enrich Wikipedia descriptions ─────────────────────
            if do_enrich_wiki:
                yield json.dumps({"type": "progress", "stage": "wiki_enrich", "message": "Fetching Wikipedia descriptions...", "count": len(all_results)}) + "\n"
                all_results = await enrich_wikipedia_descriptions(all_results, max_enrichments=80)
                yield json.dumps({"type": "progress", "stage": "wiki_enrich", "message": "Wikipedia enrichment done", "count": len(all_results)}) + "\n"

            # ── Stage 10: Reverse geocoding ────────────────────────────────
            if do_enrich_geocoding:
                yield json.dumps({"type": "progress", "stage": "geocoding", "message": "Reverse geocoding (max 40)...", "count": len(all_results)}) + "\n"
                all_results = await enrich_geocoding(all_results, max_calls=40)
                yield json.dumps({"type": "progress", "stage": "geocoding", "message": "Geocoding done", "count": len(all_results)}) + "\n"

            # ── Stage 11: Elevation ────────────────────────────────────────
            if do_enrich_elevation:
                yield json.dumps({"type": "progress", "stage": "elevation", "message": "Fetching elevation data...", "count": len(all_results)}) + "\n"
                all_results = await enrich_elevation(all_results, max_points=150)
                yield json.dumps({"type": "progress", "stage": "elevation", "message": "Elevation done", "count": len(all_results)}) + "\n"

            # ── Stage 12: AI validation + description enrichment ───────────
            if do_enrich_ai:
                yield json.dumps({"type": "progress", "stage": "ai_enrich", "message": "AI validation & description enrichment (Gemini)...", "count": len(all_results)}) + "\n"
                all_results = await enrich_with_ai(all_results, max_descriptions=50, max_validations=80)
                yield json.dumps({"type": "progress", "stage": "ai_enrich", "message": f"AI enrichment done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Final ──────────────────────────────────────────────────────
            yield json.dumps({"type": "final", "data": all_results}) + "\n"
            yield json.dumps({"type": "done", "total": len(all_results)}) + "\n"

        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        },
    )

# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "WildData API",
        "version": "2.0.0",
        "status": "running",
        "data_sources": [
            "OpenStreetMap (Overpass)", "GeoNames.org (global)", "Wikipedia GeoSearch",
            "Waymarked Trails", "Protected Planet / WDPA",
            "NPS (USA)", "Parcs Nationaux (France)", "Ordnance Survey (UK)",
            "DOC (New Zealand)", "Parks Australia", "GSI (Japan)", "data.gov.in (India)",
            "Geodata.gov.gr (Greece)", "Kartverket SSR (Norway)", "Parks Canada",
            "IGN España (Spain)", "ICMBio (Brazil)", "SANParks (South Africa)",
        ],
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

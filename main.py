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
from extractors.waymarked import fetch_waymarked
from extractors.protected_planet import fetch_protected_planet
from extractors.enrichment import enrich_geocoding, enrich_elevation, geocode_place
from extractors.countries import fetch_country_specific, COUNTRY_EXTRACTORS
from extractors.ai_enricher import enrich_with_ai
from extractors.geoapify import fetch_geoapify
from extractors.foursquare import fetch_foursquare
from extractors.here import fetch_here
from extractors.inaturalist import fetch_inaturalist
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

def get_region_bounds(cc: str, state_code: str = "", district_code: str = ""):
    """Get exact region boundaries for country/state/district."""
    bounds_data = {
        # United States
        'US': (39.8283, -98.5795, 2500),
        'US-CA': (36.7783, -119.4179, 500),
        'US-CA-LA': (34.0522, -118.2437, 100),  # Los Angeles County
        'US-CA-LAX': (34.0522, -118.2437, 25),   # Los Angeles City
        'US-CA-SF': (37.7749, -122.4194, 25),    # San Francisco City
        'US-CA-SD': (32.7157, -117.1611, 25),    # San Diego City
        'US-CA-SJU': (37.3382, -121.8863, 25),   # San Jose City
        'US-CA-SAC': (38.5816, -121.4944, 25),   # Sacramento City
        'US-TX': (31.9686, -99.9018, 800),
        'US-TX-HOU': (29.7604, -95.3698, 50),   # Houston City
        'US-TX-DAL': (32.7767, -96.7970, 50),   # Dallas City
        'US-TX-SAT': (29.4241, -98.4936, 50),   # San Antonio City
        'US-TX-AUS': (30.2672, -97.7431, 25),   # Austin City
        'US-TX-ELP': (31.7619, -106.4850, 25),  # El Paso City
        'US-TX-FW': (32.7555, -97.3308, 25),   # Fort Worth City
        'US-NY': (43.0, -75.0, 500),
        'US-NY-NYC': (40.7128, -74.0060, 25),  # New York City
        'US-NY-BRO': (40.6782, -73.9442, 25),  # Brooklyn
        'US-NY-MAN': (40.7831, -73.9712, 25),  # Manhattan
        'US-NY-QUE': (40.7282, -73.7949, 25),  # Queens
        'US-NY-BRONX': (40.8448, -73.8648, 25), # Bronx
        'US-NY-BUFF': (42.8864, -78.8784, 25),  # Buffalo City
        'US-FL': (27.6648, -81.5158, 600),
        'US-FL-MIA': (25.7617, -80.1918, 25),   # Miami City
        'US-FL-FTL': (26.1224, -80.1373, 25),  # Fort Lauderdale City
        'US-FL-TPA': (27.9506, -82.4572, 25),  # Tampa City
        'US-FL-ORL': (28.5383, -81.3792, 25),  # Orlando City
        'US-FL-JAX': (30.3322, -81.6557, 25),  # Jacksonville City
        
        # India
        'IN': (20.5937, 78.9629, 1500),
        'IN-MH': (19.0760, 77.3797, 400),
        'IN-MH-MUM': (19.0760, 72.8777, 25),    # Mumbai City
        'IN-MH-MUMC': (19.0760, 72.8777, 15),   # Mumbai City Central
        'IN-MH-PUN': (18.5204, 73.8567, 25),    # Pune City
        'IN-MH-PUNC': (18.5204, 73.8567, 15),   # Pune City Central
        'IN-MH-NAG': (21.1458, 79.0882, 25),    # Nagpur City
        'IN-MH-NASC': (21.1458, 79.0882, 15),   # Nagpur City Central
        'IN-MH-THAC': (19.2183, 72.9781, 25),  # Thane City
        'IN-MH-AURC': (19.8762, 75.3433, 25),  # Aurangabad City
        'IN-KA': (15.3173, 75.7139, 300),
        'IN-KA-BLR': (12.9716, 77.5946, 25),   # Bengaluru City
        'IN-UP': (26.8467, 80.9462, 500),
        'IN-UP-LK': (26.8467, 80.9462, 25),    # Lucknow City
        'IN-UP-KN': (26.4499, 80.3319, 25),    # Kanpur City
        'IN-GJ': (22.2587, 71.1924, 400),
        'IN-GJ-AMD': (23.0225, 72.5714, 25),   # Ahmedabad City
        'IN-GJ-SR': (21.1702, 72.8311, 25),   # Surat City
        
        # United Kingdom
        'GB': (55.3781, -3.4360, 600),
        'GB-ENG': (52.3555, -1.1743, 400),
        'GB-ENG-LON': (51.5074, -0.1278, 25),  # London
        'GB-ENG-MAN': (53.4808, -2.2426, 25),  # Manchester
        'GB-ENG-BIR': (52.4862, -1.8904, 25),  # Birmingham
        'GB-ENG-LIV': (53.4084, -2.9916, 25),  # Liverpool
        'GB-ENG-LEE': (53.8008, -1.5491, 25),  # Leeds
        
        # France
        'FR': (46.2276, 2.2137, 600),
        'FR-IDF': (48.8566, 2.3522, 100),   # Île-de-France
        'FR-IDF-PAR': (48.8566, 2.3522, 25),   # Paris
        'FR-ARA': (45.7772, 3.0870, 200),    # Auvergne-Rhône-Alpes
        'FR-ARA-LY': (45.7640, 4.8357, 25),   # Lyon
        
        # Germany
        'DE': (51.1657, 10.4515, 400),
        'DE-BY': (48.7904, 11.4979, 200),    # Bavaria
        'DE-BY-MUC': (48.1351, 11.5820, 25),  # Munich
        'DE-BY-NUR': (49.4521, 11.0767, 25), # Nuremberg
        'DE-NW': (51.4332, 7.6616, 150),    # North Rhine-Westphalia
        'DE-NW-COL': (50.9375, 6.9603, 25),  # Cologne
        'DE-NW-DUS': (51.2277, 6.7735, 25),  # Düsseldorf
        'DE-NW-DOR': (51.5136, 7.4653, 25),  # Dortmund
        
        # Japan
        'JP': (36.2048, 138.2529, 400),
        'JP-13': (35.6762, 139.6503, 50),    # Tokyo
        'JP-13-TOK': (35.6762, 139.6503, 25),  # Tokyo Central
        'JP-27': (34.6937, 135.5023, 50),   # Osaka
        'JP-27-OSA': (34.6937, 135.5023, 25), # Osaka Central
        
        # Canada
        'CA': (56.1304, -106.3468, 2000),
        'CA-ON': (51.2538, -85.3232, 500),   # Ontario
        'CA-ON-TOR': (43.6532, -79.3832, 25), # Toronto
        'CA-ON-OTT': (45.4215, -75.6972, 25), # Ottawa
        'CA-QC': (52.7394, -73.2365, 400),   # Quebec
        'CA-QC-MON': (45.5017, -73.5673, 25), # Montreal
        'CA-QC-QUE': (46.8139, -71.2080, 25), # Quebec City
        
        # Australia
        'AU': (-25.2744, 133.7751, 2500),
        'AU-NSW': (-32.0, 147.0, 400),       # New South Wales
        'AU-NSW-SYD': (-33.8688, 151.2093, 25), # Sydney
        'AU-VIC': (-37.0, 144.0, 300),       # Victoria
        'AU-VIC-MEL': (-37.8136, 144.9631, 25), # Melbourne
        'AU-VIC-MELCITY': (-37.8136, 144.9631, 15), # Melbourne City
        'AU-VIC-GEEL': (-38.1499, 144.3617, 25), # Geelong
        'AU-VIC-BALL': (-37.5622, 143.8503, 25), # Ballarat
        'AU-VIC-BEND': (-36.7570, 144.2794, 25), # Bendigo
        
        # Singapore
        'SG': (1.3521, 103.8198, 50),
        'SG-01': (1.3521, 103.8198, 10),     # Central Region
        'SG-02': (1.3521, 103.8198, 10),     # East Region
        'SG-03': (1.3521, 103.8198, 10),     # North Region
        'SG-04': (1.3521, 103.8198, 10),     # North-East Region
        'SG-05': (1.3521, 103.8198, 10),     # West Region
        
        # Hong Kong
        'HK': (22.3193, 114.1694, 50),
        'HK-CW': (22.2811, 114.1556, 10),    # Central and Western
        'HK-E': (22.2811, 114.1556, 10),      # Eastern
        'HK-S': (22.2811, 114.1556, 10),      # Southern
        'HK-WC': (22.2811, 114.1556, 10),     # Wan Chai
        
        # Luxembourg
        'LU': (49.8153, 6.1296, 50),
        'LU-L': (49.6116, 6.1319, 10),       # Luxembourg District
        'LU-D': (49.7743, 6.1639, 10),       # Diekirch District
        'LU-G': (49.5116, 6.3531, 10),       # Grevenmacher District
    }
    
    # Build key in order of specificity: district > state > country
    key = None
    if district_code and state_code:
        key = f"{cc}-{state_code}-{district_code}"
    elif state_code:
        key = f"{cc}-{state_code}"
    else:
        key = cc
    
    return bounds_data.get(key, None)

# ── Main extraction — streaming ───────────────────────────────────────────────

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
    use_geoapify: bool = Query(False),    # Geoapify Places — 3,000 req/day free
    use_foursquare: bool = Query(False),  # Foursquare Places — 1,000 req/day free
    use_here: bool = Query(False),        # HERE Places — 250,000 req/month free
    use_inaturalist: bool = Query(True),  # iNaturalist — completely free, no key
    search_mode: str = Query("radius"),
    region_bbox: str = Query(""),  # "south,west,north,east"
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

    # Parse region_bbox if provided (south,west,north,east)
    bbox_tuple = None
    if region_bbox and search_mode == "region":
        try:
            parts = [float(x) for x in region_bbox.split(",")]
            if len(parts) == 4:
                bbox_tuple = (parts[0], parts[1], parts[2], parts[3])  # south,west,north,east
        except Exception:
            pass

    # Region-based bounds calculation - use exact boundaries instead of radius
    region_bounds = get_region_bounds(cc, state_code, district_code)
    if region_bounds:
        lat, lng, radius_km = region_bounds
        region_info = f"Using exact boundary: {cc}"
        if state_code: region_info += f"-{state_code}"
        if district_code: region_info += f"-{district_code}"
    else:
        # Fallback to coordinates if no region bounds found
        region_info = f"Using coordinates: {lat:.3f}, {lng:.3f}"
        # If we have region codes but no bounds, still log the region
        if state_code or district_code:
            if district_code:
                region_detail = f"District: {district_code}"
            elif state_code:
                region_detail = f"State: {state_code}"
            else:
                region_detail = f"Country: {cc}"
        else:
            region_detail = f"Country: {cc}"

    async def generate():
        all_results = []

        try:
            # Log region info at start
            yield json.dumps({"type": "progress", "stage": "region", "message": region_info, "count": 0}) + "\n"
            
            # Log the specific region being extracted
            if state_code or district_code:
                if district_code:
                    region_detail = f"District: {district_code}"
                elif state_code:
                    region_detail = f"State: {state_code}"
                else:
                    region_detail = f"Country: {cc}"
                yield json.dumps({"type": "progress", "stage": "region", "message": f"Extracting data from {region_detail}", "count": 0}) + "\n"
            else:
                yield json.dumps({"type": "progress", "stage": "region", "message": f"Extracting data from Country: {cc}", "count": 0}) + "\n"
            
            # ── Stage 1: OSM ───────────────────────────────────────────────
            yield json.dumps({"type": "progress", "stage": "osm", "message": f"Querying OpenStreetMap (quality: {quality})...", "count": len(all_results)}) + "\n"
            osm_limit = limit if quality == "high" else min(limit, 150)
            batch = []
            async for item in fetch_osm(lat, lng, int(radius_km * 1000), feature_ids, osm_limit, bbox=bbox_tuple):
                all_results.append(item)
                batch.append(item)
                if len(batch) >= 20:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                    batch = []
            if batch:
                yield json.dumps({"type": "results", "data": batch}) + "\n"
            yield json.dumps({"type": "progress", "stage": "osm", "message": f"OSM done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 2: OpenTripMap ───────────────────────────────────────
            yield json.dumps({"type": "progress", "stage": "opentripmap", "message": "Querying OpenTripMap tourism database...", "count": len(all_results)}) + "\n"
            batch = []
            async for item in fetch_opentripmap(lat, lng, radius_km, feature_ids, limit=200, bbox=bbox_tuple):
                all_results.append(item)
                batch.append(item)
                if len(batch) >= 20:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                    batch = []
            if batch:
                yield json.dumps({"type": "results", "data": batch}) + "\n"
            yield json.dumps({"type": "progress", "stage": "opentripmap", "message": f"OpenTripMap done — {len(all_results)} features", "count": len(all_results)}) + "\n"

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

            # ── Stage 8: Geoapify Places (optional, 3,000 req/day free) ──────
            if use_geoapify:
                yield json.dumps({"type": "progress", "stage": "geoapify", "message": "Querying Geoapify Places (free tier: 3k/day)...", "count": len(all_results)}) + "\n"
                batch = []
                async for item in fetch_geoapify(lat, lng, radius_km, feature_ids, limit=100, bbox=bbox_tuple):
                    all_results.append(item)
                    batch.append(item)
                    if len(batch) >= 20:
                        yield json.dumps({"type": "results", "data": batch}) + "\n"
                        batch = []
                if batch:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                yield json.dumps({"type": "progress", "stage": "geoapify", "message": f"Geoapify done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 9: Foursquare Places (optional, 1,000 req/day free) ────
            if use_foursquare:
                yield json.dumps({"type": "progress", "stage": "foursquare", "message": "Querying Foursquare Places (free tier: 1k/day)...", "count": len(all_results)}) + "\n"
                batch = []
                async for item in fetch_foursquare(lat, lng, radius_km, feature_ids, limit=50, bbox=bbox_tuple):
                    all_results.append(item)
                    batch.append(item)
                    if len(batch) >= 20:
                        yield json.dumps({"type": "results", "data": batch}) + "\n"
                        batch = []
                if batch:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                yield json.dumps({"type": "progress", "stage": "foursquare", "message": f"Foursquare done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 10: HERE Places (optional, 250k req/month free) ────────
            if use_here:
                yield json.dumps({"type": "progress", "stage": "here", "message": "Querying HERE Places (free tier: 250k/month)...", "count": len(all_results)}) + "\n"
                batch = []
                async for item in fetch_here(lat, lng, radius_km, feature_ids, limit=100, bbox=bbox_tuple):
                    all_results.append(item)
                    batch.append(item)
                    if len(batch) >= 20:
                        yield json.dumps({"type": "results", "data": batch}) + "\n"
                        batch = []
                if batch:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                yield json.dumps({"type": "progress", "stage": "here", "message": f"HERE Places done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 11: iNaturalist (always free, no key needed) ────────────
            if use_inaturalist:
                yield json.dumps({"type": "progress", "stage": "inaturalist", "message": "Querying iNaturalist nature areas & wildlife hotspots...", "count": len(all_results)}) + "\n"
                batch = []
                async for item in fetch_inaturalist(lat, lng, radius_km, feature_ids, limit=100, bbox=bbox_tuple):
                    all_results.append(item)
                    batch.append(item)
                    if len(batch) >= 20:
                        yield json.dumps({"type": "results", "data": batch}) + "\n"
                        batch = []
                if batch:
                    yield json.dumps({"type": "results", "data": batch}) + "\n"
                yield json.dumps({"type": "progress", "stage": "inaturalist", "message": f"iNaturalist done — {len(all_results)} features", "count": len(all_results)}) + "\n"

            # ── Stage 12: Deduplicate ──────────────────────────────────────
            yield json.dumps({"type": "progress", "stage": "dedup", "message": "Deduplicating results...", "count": len(all_results)}) + "\n"
            all_results = deduplicate(all_results)
            yield json.dumps({"type": "progress", "stage": "dedup", "message": f"After dedup: {len(all_results)} unique features", "count": len(all_results)}) + "\n"

            # ── Stage 13: Enrich Wikipedia descriptions ────────────────────
            if do_enrich_wiki:
                yield json.dumps({"type": "progress", "stage": "wiki_enrich", "message": "Fetching Wikipedia descriptions...", "count": len(all_results)}) + "\n"
                all_results = await enrich_wikipedia_descriptions(all_results, max_enrichments=80)
                yield json.dumps({"type": "progress", "stage": "wiki_enrich", "message": "Wikipedia enrichment done", "count": len(all_results)}) + "\n"

            # ── Stage 14: Reverse geocoding ────────────────────────────────
            if do_enrich_geocoding:
                yield json.dumps({"type": "progress", "stage": "geocoding", "message": "Reverse geocoding (max 40)...", "count": len(all_results)}) + "\n"
                all_results = await enrich_geocoding(all_results, max_calls=40)
                yield json.dumps({"type": "progress", "stage": "geocoding", "message": "Geocoding done", "count": len(all_results)}) + "\n"

            # ── Stage 15: Elevation ────────────────────────────────────────
            if do_enrich_elevation:
                yield json.dumps({"type": "progress", "stage": "elevation", "message": "Fetching elevation data...", "count": len(all_results)}) + "\n"
                all_results = await enrich_elevation(all_results, max_points=150)
                yield json.dumps({"type": "progress", "stage": "elevation", "message": "Elevation done", "count": len(all_results)}) + "\n"

            # ── Stage 16: AI validation + description enrichment ───────────
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

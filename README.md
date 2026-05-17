---
title: WildData API
emoji: 🌲
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8080
pinned: false
---

# WildData — Outdoor Feature Extractor

Extracts outdoor features (waterfalls, hiking routes, peaks, parks etc.) from:
- OpenStreetMap (Overpass API)
- Wikidata SPARQL
- Wikipedia GeoSearch + Summaries
- Country-specific government APIs (USA/NPS, France/data.gouv.fr, NZ/DOC, India/data.gov.in, Japan/GSI, UK, Australia)
- OpenTopoData (elevation)
- Nominatim (reverse geocoding)

## Project Structure

```
wilddata-backend/
├── main.py                  ← FastAPI app (entry point)
├── requirements.txt
├── render.yaml              ← Render.com deploy config
├── extractors/
│   ├── osm.py               ← OpenStreetMap Overpass
│   ├── wikidata.py          ← Wikidata SPARQL
│   ├── wikipedia.py         ← Wikipedia GeoSearch + summaries
│   ├── enrichment.py        ← Nominatim geocoding + elevation
│   └── countries.py         ← Country-specific sources
├── utils/
│   ├── rate_limiter.py      ← Async rate limiter
│   └── deduplicator.py      ← Result deduplication
└── frontend/
    └── index.html           ← Frontend (deploy to Netlify)
```

---

## Step 1 — Run locally first (test)

```bash
# Install Python 3.11+ if not installed
python3 --version

# Install dependencies
pip3 install -r requirements.txt

# Run the server
uvicorn main:app --reload --port 8000

# Test in browser
open http://localhost:8000
open http://localhost:8000/docs
```

Test API directly:
```
http://localhost:8000/search?q=Kasol
http://localhost:8000/extract?lat=32.01&lng=77.31&radius_km=25&features=waterfall,peak&limit=50
```

---

## Step 2 — Deploy backend to Render.com (free)

1. Push this folder to a GitHub repo:
```bash
cd wilddata-backend
git init
git add .
git commit -m "initial"
git remote add origin https://github.com/YOUR_USERNAME/wilddata-backend.git
git push -u origin main
```

2. Go to render.com → New → Web Service
3. Connect your GitHub repo
4. Settings:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Environment**: Python 3
5. Click Deploy
6. Your API URL will be: `https://wilddata-api.onrender.com`

---

## Step 3 — Deploy frontend to Netlify

1. Go to netlify.com/drop
2. Drag and drop `frontend/index.html`
3. Open the site
4. Enter your Render.com API URL in the top field
5. Click "Test Connection" — should show green dot
6. Search a place → Extract → Export CSV/XLSX

---

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | API info |
| `GET /health` | Health check |
| `GET /search?q=Kasol` | Geocode place name |
| `GET /extract?lat=&lng=&radius_km=&features=&limit=` | Main extraction (NDJSON stream) |
| `GET /docs` | Swagger UI |

### Extract parameters

| Param | Default | Description |
|---|---|---|
| lat | required | Latitude |
| lng | required | Longitude |
| radius_km | 25 | Search radius in km (1-500) |
| features | waterfall,peak,hiking | Comma-separated feature types |
| limit | 300 | Max results per feature type |
| country_code | "" | ISO2 country code (FR, IN, US etc.) |
| enrich_wiki | true | Fetch Wikipedia descriptions |
| enrich_elevation | true | Fetch elevation data |
| enrich_geocoding | true | Reverse geocode for region/country |
| use_geoapify | false | Add Geoapify Places spot lookups (requires API key) |
| use_foursquare | false | Add Foursquare Places spot lookups (requires API key) |

### Supported feature types
waterfall, pool, hiking, mtb, motorbiking, peak, park, viewpoint, historic, camp, hut, cave, hot_spring, lake, beach, gorge, meadow, glacier, volcano, unesco, forest_walk, monastery

---

## Notes

- **Nominatim**: max 1 request/second (geocoding capped at 40 calls per extraction)
- **Overpass**: 1.5 second gap between requests
- **Wikidata**: 0.5 second gap
- **Wikipedia**: 0.5 second gap
- **OpenTopoData**: batches 100 points per request, capped at 200 points
- Results are deduplicated by proximity (within 100m same type = merged)

## Optional API Keys (spot lookups)

Set these env vars to enable extra sources:

- `GEOAPIFY_API_KEY`
- `FOURSQUARE_API_KEY`

Free-tier safe mode (enabled by default):

- `FREE_TIER_SAFE_MODE=1` (set to 0 to disable caps)
- `FOURSQUARE_DAILY_CALL_LIMIT` (default 400)
- `FOURSQUARE_MONTHLY_CALL_LIMIT` (default 9000)
- `FOURSQUARE_MAX_PER_FEATURE` (default 20)
- `GEOAPIFY_DAILY_CREDITS_LIMIT` (default 2000)
- `GEOAPIFY_MAX_PER_FEATURE` (default 40)

Note: Geoapify is queried only for feature types that have precise categories
in the Places API (e.g., peaks, caves, hot springs, campsites, beaches, parks).
Other types (waterfalls, volcanoes, trails, lakes/rivers) are left to OSM/OTM/Waymarked.

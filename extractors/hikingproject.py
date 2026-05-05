"""
Hiking Project API (REI) — US trail database with difficulty ratings.
Free API key required. Register at: https://www.hikingproject.com/data
Set env var: HIKING_PROJECT_KEY

Covers: 40,000+ trails across the USA with difficulty, length, elevation gain.
"""

import os
import httpx
from typing import List, Dict, Any, AsyncGenerator
from utils.rate_limiter import rate_limiter

HIKING_PROJECT_KEY = os.getenv("HIKING_PROJECT_KEY", "")
HIKING_PROJECT_URL = "https://www.hikingproject.com/data/get-trails"

DIFFICULTY_MAP = {
    "green":       "Easy",
    "greenBlue":   "Easy-Moderate",
    "blue":        "Moderate",
    "blueBlack":   "Moderate-Hard",
    "black":       "Hard",
    "dblack":      "Very Hard",
}


async def fetch_hiking_project(
    lat: float,
    lng: float,
    radius_km: float,
    feature_ids: List[str],
    limit: int = 100,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Fetch trails from Hiking Project (REI) — US only.
    Only runs when 'hiking' or 'mtb' is in selected features.
    Requires HIKING_PROJECT_KEY env var.
    """
    if not any(f in feature_ids for f in ("hiking", "mtb")):
        return

    if not HIKING_PROJECT_KEY:
        print("[HikingProject] HIKING_PROJECT_KEY not set — skipping. Register free at hikingproject.com/data")
        return

    radius_miles = min(radius_km * 0.621371, 200)  # API max 200 miles

    try:
        await rate_limiter.wait("www.hikingproject.com", 0.5)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                HIKING_PROJECT_URL,
                params={
                    "lat":         lat,
                    "lon":         lng,
                    "maxDistance": round(radius_miles, 1),
                    "maxResults":  min(limit, 500),
                    "key":         HIKING_PROJECT_KEY,
                },
            )
            if resp.status_code == 200 and resp.json().get("success") == 0:
                print(f"[HikingProject] API error: {resp.json().get('message')}")
                return
            if resp.status_code != 200:
                print(f"[HikingProject] HTTP {resp.status_code}")
                return
            data = resp.json()

        for trail in data.get("trails", []):
            t_lat = trail.get("latitude")
            t_lng = trail.get("longitude")
            if not t_lat or not t_lng:
                continue

            name = trail.get("name", "")
            if not name:
                continue

            difficulty_raw = trail.get("difficulty", "")
            difficulty = DIFFICULTY_MAP.get(difficulty_raw, difficulty_raw.title())
            length_miles = trail.get("length", 0)
            length_km = round(length_miles * 1.609, 1) if length_miles else 0
            ascent = trail.get("ascent", "")
            stars = trail.get("stars", "")

            desc_parts = []
            if difficulty:
                desc_parts.append(f"Difficulty: {difficulty}")
            if length_km:
                desc_parts.append(f"{length_km} km")
            if ascent:
                desc_parts.append(f"Ascent: {ascent}m")
            if stars:
                desc_parts.append(f"Rating: {stars}/5")
            desc = " · ".join(desc_parts)

            fid = "mtb" if "mtb" in (trail.get("type", "").lower()) else "hiking"
            if fid not in feature_ids:
                fid = "hiking" if "hiking" in feature_ids else "mtb"

            yield {
                "name":        name,
                "type":        "Hiking Trail",
                "type_id":     fid,
                "lat":         float(t_lat),
                "lng":         float(t_lng),
                "elevation":   f"{trail.get('high', '')}m" if trail.get("high") else "",
                "description": desc,
                "wikipedia":   "",
                "website":     trail.get("url", ""),
                "region":      "",
                "country":     "United States",
                "image":       trail.get("imgMedium") or trail.get("imgSmall") or "",
                "osm_id":      "",
                "source":      "Hiking Project (REI)",
                "confidence":  "High" if stars and float(stars) >= 3.5 else "Medium",
            }

    except Exception as e:
        print(f"[HikingProject] error: {e}")

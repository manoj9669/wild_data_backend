"""
Wikimedia Commons image enrichment — CC-0 / Public Domain only.

Searches for geo-tagged photos near each place's coordinates and attaches
the best image URL to records that currently have no image.

Only CC-0 and Public Domain images are used — completely free for any
commercial use, no attribution required.

Two-step per place (batched):
  1. geosearch  → list of File: pages near coordinates
  2. imageinfo  → license + thumbnail URL for each file

API: https://commons.wikimedia.org/w/api.php
Rate: ≤1 req/sec (Wikimedia policy)
"""

import httpx
from typing import List, Dict, Any
from utils.rate_limiter import rate_limiter

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# License short-names considered public domain / CC-0
_FREE_LICENSES = {
    "CC0", "CC0 1.0", "Public Domain", "PD", "PD-self",
    "PD-old", "PD-old-70", "PD-old-70-1923", "PD-US",
}


def _is_free(license_str: str) -> bool:
    if not license_str:
        return False
    s = license_str.strip()
    return any(s.startswith(lic) for lic in _FREE_LICENSES)


async def _find_cc0_image(
    client: httpx.AsyncClient,
    lat: float,
    lng: float,
    radius_m: int,
) -> str:
    """
    Return the thumbnail URL of the nearest CC-0 Commons photo, or "".
    Two API calls: geo-search → imageinfo with license filter.
    """
    # ── Step 1: geo-search for File: pages near coordinates ───────────────
    try:
        await rate_limiter.wait("commons.wikimedia.org", 1.0)
        resp = await client.get(COMMONS_API, params={
            "action":      "query",
            "list":        "geosearch",
            "gscoord":     f"{lat}|{lng}",
            "gsradius":    min(radius_m, 10000),  # Commons caps at 10 km
            "gslimit":     "20",
            "gsnamespace": "6",                    # File namespace only
            "format":      "json",
            "origin":      "*",
        })
        if resp.status_code != 200:
            return ""
        hits = resp.json().get("query", {}).get("geosearch", [])
        if not hits:
            return ""
    except Exception:
        return ""

    titles = "|".join(h["title"] for h in hits[:20])

    # ── Step 2: imageinfo + license for all hits in one batch call ────────
    try:
        await rate_limiter.wait("commons.wikimedia.org", 1.0)
        resp = await client.get(COMMONS_API, params={
            "action":     "query",
            "titles":     titles,
            "prop":       "imageinfo",
            "iiprop":     "url|extmetadata|mediatype",
            "iiurlwidth": "800",
            "format":     "json",
            "origin":     "*",
        })
        if resp.status_code != 200:
            return ""
        pages = list(resp.json().get("query", {}).get("pages", {}).values())
    except Exception:
        return ""

    for page in pages:
        ii_list = page.get("imageinfo") or []
        if not ii_list:
            continue
        ii = ii_list[0]

        # Only photos / bitmaps — skip SVGs, audio, video
        if ii.get("mediatype", "").upper() not in ("BITMAP", "DRAWING", ""):
            continue

        meta = ii.get("extmetadata", {})
        license_short = meta.get("LicenseShortName", {}).get("value", "")
        if not _is_free(license_short):
            continue

        url = ii.get("thumburl") or ii.get("url", "")
        if url and url.startswith("http"):
            return url

    return ""


async def enrich_wikimedia_images(
    results: List[Dict[str, Any]],
    max_enrichments: int = 80,
    radius_m: int = 1500,
) -> List[Dict[str, Any]]:
    """
    Attach CC-0 Wikimedia Commons photos to results that currently have no image.

    Only processes named results — unnamed places are unlikely to have
    geo-tagged photos on Commons.

    Args:
        results:          List of place dicts (mutated in-place).
        max_enrichments:  Max number of API lookups per request.
        radius_m:         Search radius in metres around each place (≤10000).
    """
    candidates = [
        r for r in results
        if not r.get("image") and r.get("name") and r.get("lat") is not None
    ][:max_enrichments]

    if not candidates:
        return results

    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "WildDataExtractor/1.0 (gowild.co.in)"},
    ) as client:
        for r in candidates:
            url = await _find_cc0_image(client, r["lat"], r["lng"], radius_m)
            if url:
                r["image"] = url

    return results

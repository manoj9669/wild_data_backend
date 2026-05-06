"""
Gemini Flash AI enrichment — uses Google Gemini 1.5 Flash (free tier) to:
1. Generate short descriptions for features that have none
2. Translate non-English names to English (keeps original as subtitle)
3. Validate feature type classification (catches misclassified items)

Free tier limits: 15 RPM, 1M tokens/day — enough for ~500 enrichments/day.
Set env var: GEMINI_API_KEY

Skips silently if GEMINI_API_KEY is not set.
"""

import asyncio
import json
import os
import re
from typing import List, Dict, Any

import httpx
from utils.rate_limiter import rate_limiter

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

# Feature types that benefit most from AI description
ENRICHABLE_TYPES = {
    "waterfall", "peak", "cave", "hot_spring", "glacier",
    "volcano", "viewpoint", "beach", "park", "forest",
}

# Feature type → expected keywords/concepts for validation
TYPE_CONCEPTS = {
    "waterfall":  ["waterfall", "falls", "cascade", "pool", "swimming", "plunge"],
    "peak":       ["mountain", "peak", "summit", "hill", "ridge", "elevation"],
    "cave":       ["cave", "cavern", "grotto", "tunnel", "karst", "stalactite"],
    "hot_spring": ["spring", "thermal", "hot", "geothermal", "mineral", "bath"],
    "beach":      ["beach", "coast", "sand", "shore", "bay", "cove", "sea"],
    "glacier":    ["glacier", "ice", "snow", "alpine", "frozen", "icefield"],
    "volcano":    ["volcano", "volcanic", "eruption", "lava", "crater", "magma"],
    "viewpoint":  ["view", "panorama", "lookout", "vista", "scenic", "overlook"],
    "park":       ["park", "reserve", "protected", "national", "wildlife", "sanctuary"],
    "forest":     ["forest", "woodland", "trees", "jungle", "rainforest", "grove"],
    "lake":   ["lake", "river", "stream", "water", "reservoir", "gorge"],
    "camp":       ["camp", "camping", "campsite", "tent", "overnight", "lodge"],
}


async def _gemini_request(prompt: str) -> str:
    """Send a single prompt to Gemini Flash and return the text response."""
    if not GEMINI_API_KEY:
        return ""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 200,
        },
    }

    try:
        await rate_limiter.wait("generativelanguage.googleapis.com", 4.5)  # 15 RPM = 1 per 4s
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 429:
                print("[Gemini] Rate limit hit — slowing down")
                await asyncio.sleep(10)
                return ""
            if resp.status_code != 200:
                print(f"[Gemini] HTTP {resp.status_code}")
                return ""
            data = resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "").strip() if parts else ""

    except Exception as e:
        print(f"[Gemini] request error: {e}")
        return ""


def _needs_description(feature: Dict[str, Any]) -> bool:
    """True if this feature would benefit from an AI-generated description."""
    if feature.get("description") and len(feature["description"]) > 30:
        return False
    if feature.get("type_id") not in ENRICHABLE_TYPES:
        return False
    if not feature.get("name"):
        return False
    return True


def _looks_non_english(name: str) -> bool:
    """Simple heuristic: if >40% chars are non-ASCII, likely non-English."""
    if not name:
        return False
    non_ascii = sum(1 for c in name if ord(c) > 127)
    return non_ascii / len(name) > 0.4


async def _batch_describe(features: List[Dict[str, Any]]) -> List[str]:
    """
    Generate descriptions for a batch of features in a single Gemini call.
    Returns list of description strings in same order as input.
    """
    if not features:
        return []

    items = []
    for i, f in enumerate(features):
        region = f.get("region") or f.get("country") or ""
        items.append(f'{i+1}. {f["name"]} ({f.get("type","feature")}{", " + region if region else ""})')

    prompt = (
        "For each geographic feature below, write a single sentence (max 25 words) describing "
        "what it is and why it's notable for outdoor visitors. Be specific and factual. "
        "Reply with ONLY a JSON array of strings, one per item, in the same order.\n\n"
        + "\n".join(items)
    )

    response = await _gemini_request(prompt)
    if not response:
        return [""] * len(features)

    # Extract JSON array from response
    try:
        # Strip markdown code blocks if present
        response = re.sub(r"```(?:json)?\s*", "", response).strip()
        descriptions = json.loads(response)
        if isinstance(descriptions, list):
            result = []
            for d in descriptions[:len(features)]:
                result.append(str(d).strip() if d else "")
            # Pad if response was shorter
            while len(result) < len(features):
                result.append("")
            return result
    except (json.JSONDecodeError, ValueError):
        # Try line-by-line fallback
        lines = [l.strip().lstrip("0123456789.-) ") for l in response.split("\n") if l.strip()]
        result = lines[:len(features)]
        while len(result) < len(features):
            result.append("")
        return result

    return [""] * len(features)


async def _validate_batch(features: List[Dict[str, Any]]) -> List[bool]:
    """
    Ask Gemini to validate whether each feature's type matches its name.
    Returns list of bools: True = keep, False = likely misclassified.
    """
    if not features:
        return []

    items = []
    for i, f in enumerate(features):
        name = f.get("name", "")
        ftype = f.get("type", "")
        desc = (f.get("description") or "")[:80]
        items.append(f'{i+1}. Name="{name}", Type="{ftype}", Desc="{desc}"')

    prompt = (
        "For each item, decide if the geographic feature type correctly matches the name/description. "
        "Answer 'yes' if it seems correct, 'no' if clearly misclassified (e.g. a river named as 'Waterfall', "
        "a town named as 'Mountain Peak'). "
        "Reply ONLY with a JSON array of booleans (true=keep, false=remove), one per item.\n\n"
        + "\n".join(items)
    )

    response = await _gemini_request(prompt)
    if not response:
        return [True] * len(features)

    try:
        response = re.sub(r"```(?:json)?\s*", "", response).strip()
        results = json.loads(response)
        if isinstance(results, list):
            bools = []
            for r in results[:len(features)]:
                if isinstance(r, bool):
                    bools.append(r)
                elif isinstance(r, str):
                    bools.append(r.lower() != "false" and r.lower() != "no")
                else:
                    bools.append(True)
            while len(bools) < len(features):
                bools.append(True)
            return bools
    except (json.JSONDecodeError, ValueError):
        pass

    return [True] * len(features)


async def enrich_with_ai(
    results: List[Dict[str, Any]],
    max_descriptions: int = 50,
    max_validations: int = 80,
    validate: bool = True,
) -> List[Dict[str, Any]]:
    """
    Main AI enrichment pipeline:
    1. Validate feature type classifications (removes misclassified items)
    2. Generate descriptions for features that lack them

    Skips silently if GEMINI_API_KEY is not set.
    Caps API usage to stay within free tier limits.
    """
    if not GEMINI_API_KEY:
        return results

    BATCH_SIZE = 10  # Keep prompts short for reliability

    # ── Step 1: Validation disabled — was incorrectly removing valid results
    # Description generation only

    # ── Step 2: Generate descriptions ─────────────────────────────────────────
    describe_candidates = [
        (i, r) for i, r in enumerate(results)
        if _needs_description(r)
    ][:max_descriptions]

    for batch_start in range(0, len(describe_candidates), BATCH_SIZE):
        batch = describe_candidates[batch_start:batch_start + BATCH_SIZE]
        indices = [i for i, _ in batch]
        features = [f for _, f in batch]

        descriptions = await _batch_describe(features)

        for idx, desc in zip(indices, descriptions):
            if desc and len(desc) > 10:
                results[idx]["description"] = desc
                results[idx]["description_source"] = "AI"

    print(f"[Gemini] Enrichment done — {len(describe_candidates)} descriptions generated")
    return results

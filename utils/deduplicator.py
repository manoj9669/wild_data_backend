import math
import re
from typing import List, Dict, Any


# ── Distance ──────────────────────────────────────────────────────────────────

def haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ── Name normalisation ────────────────────────────────────────────────────────

# Common geographic suffix synonyms — all collapsed to a single canonical form
_SYNONYMS = [
    (r'\bwaterfalls?\b', ''),
    (r'\bfalls?\b', ''),
    (r'\bcascade\b', ''),
    (r'\bmountains?\b', ''),
    (r'\bmounts?\b', ''),
    (r'\bmt\.?\b', ''),
    (r'\bpeaks?\b', ''),
    (r'\bsummits?\b', ''),
    (r'\blakes?\b', ''),
    (r'\brivers?\b', ''),
    (r'\bstreams?\b', ''),
    (r'\bnational\b', ''),
    (r'\bpark\b', ''),
    (r'\breserve\b', ''),
    (r'\bforest\b', ''),
    (r'\bglacier\b', ''),
    (r'\bvolcano\b', ''),
    (r'\bbeach\b', ''),
    (r'\bcave\b', ''),
    (r'\bspring\b', ''),
    (r'\bpoint\b', ''),
    (r'\bpass\b', ''),
]

_STOP = {'a', 'an', 'the', 'of', 'in', 'at', 'on', 'near', 'and', 'de', 'la', 'le', ''}


def _normalise(name: str) -> str:
    """Lowercase, strip suffixes, remove punctuation, collapse whitespace."""
    s = name.lower()
    for pattern, repl in _SYNONYMS:
        s = re.sub(pattern, repl, s)
    s = re.sub(r"[^\w\s]", " ", s)   # punctuation → space
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokens(name: str) -> set:
    return {t for t in _normalise(name).split() if t not in _STOP and len(t) > 1}


def name_similarity(a: str, b: str) -> float:
    """
    Token-overlap similarity after synonym normalisation.
    Returns 0.0–1.0. 1.0 = identical tokens.
    """
    if not a or not b:
        return 0.0
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        # Both reduced to nothing — treat as identical (e.g. "Waterfall" vs "Falls")
        return 1.0 if _normalise(a) == _normalise(b) else 0.0
    overlap = ta & tb
    # Jaccard-like: intersection / smaller set  (biased toward shorter names matching)
    return len(overlap) / min(len(ta), len(tb))


# ── Type-aware proximity thresholds ──────────────────────────────────────────
# Small point features need tight matching; large area features can differ more.

_COORD_RADIUS: Dict[str, float] = {
    "waterfall":  0.30,   # 300 m — tight, it's a single point
    "hot_spring": 0.30,
    "cave":       0.30,
    "viewpoint":  0.30,
    "camp":       0.40,
    "beach":      0.60,
    "peak":       1.00,   # summit vs centroid can differ
    "glacier":    1.50,
    "volcano":    1.50,
    "waterway":   1.00,   # lake centroid shifts by source
    "park":       3.00,   # park centroid can be km away
    "forest":     3.00,
    "hiking":     1.00,
    "mtb":        1.00,
}
_DEFAULT_COORD_RADIUS = 0.50   # km — for unknown types

# When names are similar (>= threshold), allow a looser coordinate window
_NAME_RADIUS_MULTIPLIER = 3.0  # e.g. 0.3 km → 0.9 km if names match
_NAME_SIM_THRESHOLD     = 0.60  # 60 % token overlap → considered same place


# ── Merge helper ─────────────────────────────────────────────────────────────

_CONF_RANK = {"High": 3, "Medium": 2, "Low": 1}

_SOURCE_PRIORITY = [
    "OSM", "OpenTripMap", "GeoNames", "Wikipedia",
    "Protected Planet / WDPA", "Waymarked Trails",
]


def _better_source(a: str, b: str) -> str:
    """Return the more authoritative source label."""
    def rank(s):
        for i, src in enumerate(_SOURCE_PRIORITY):
            if src in s:
                return i
        return len(_SOURCE_PRIORITY)
    return a if rank(a) <= rank(b) else b


def _merge_into(keep: Dict, drop: Dict) -> None:
    """Merge fields from `drop` into `keep`, preferring richer data."""
    for field in ("name", "description", "wikipedia", "elevation",
                  "region", "country", "image", "website"):
        if not keep.get(field) and drop.get(field):
            keep[field] = drop[field]

    # Keep higher confidence
    if _CONF_RANK.get(drop.get("confidence", "Low"), 1) > _CONF_RANK.get(keep.get("confidence", "Low"), 1):
        keep["confidence"] = drop["confidence"]

    # Merge source labels (deduped)
    existing = {s.strip() for s in keep.get("source", "").split("+")}
    new_src  = drop.get("source", "").strip()
    if new_src and new_src not in existing:
        existing.add(new_src)
        # Keep most authoritative first
        ordered = [s for s in _SOURCE_PRIORITY if any(s in e for e in existing)]
        others  = [e for e in existing if not any(s in e for s in _SOURCE_PRIORITY)]
        keep["source"] = "+".join(ordered + others)

    # Prefer coordinates from most authoritative source
    better = _better_source(keep.get("source", ""), drop.get("source", ""))
    if drop.get("source", "") == better and drop.get("lat") and drop.get("lng"):
        keep["lat"] = drop["lat"]
        keep["lng"] = drop["lng"]


# ── Main deduplicator ─────────────────────────────────────────────────────────

def deduplicate(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge duplicate geographic features using two-stage matching:

    Stage 1 — coordinate proximity (type-aware radius):
        Same type_id + within threshold distance → duplicate.

    Stage 2 — fuzzy name similarity (within a looser radius):
        Same type_id + distance within 3× threshold + name similarity ≥ 60% → duplicate.
        Catches "Jogini Waterfall" vs "Jogini Falls" across OSM/OpenTripMap/GeoNames.

    Merges all fields from duplicates into the record with the most data,
    and concatenates source labels (e.g. "OSM+OpenTripMap").
    """
    merged: List[Dict[str, Any]] = []

    for r in results:
        type_id  = r.get("type_id", "")
        coord_r  = _COORD_RADIUS.get(type_id, _DEFAULT_COORD_RADIUS)
        name_r   = coord_r * _NAME_RADIUS_MULTIPLIER
        r_name   = r.get("name", "")
        r_lat    = r.get("lat", 0)
        r_lng    = r.get("lng", 0)

        matched = None
        for m in merged:
            if m.get("type_id") != type_id:
                continue

            dist = haversine_km(m["lat"], m["lng"], r_lat, r_lng)

            # Stage 1: close enough regardless of name
            if dist <= coord_r:
                matched = m
                break

            # Stage 2: further away but names are similar
            if dist <= name_r:
                sim = name_similarity(m.get("name", ""), r_name)
                if sim >= _NAME_SIM_THRESHOLD:
                    matched = m
                    break

        if matched is not None:
            _merge_into(matched, r)
        else:
            merged.append(dict(r))

    return merged

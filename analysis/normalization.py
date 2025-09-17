import re

from difflib import SequenceMatcher
from visor_scraper.constants import BASE_SUFFIXES

ENGINE_MARKERS = {"s", "turbo", "hybrid", "phev", "plug-in"}
DRIVETRAINS = {"4x4", "4wd", "2wd", "4xe", "awd", "rwd"}
BODY_STYLE_PATTERN = re.compile(
    r"\s+(Sedan|Coupe|Hatchback|Sport Utility|SUV|Crew Cab|Extended Cab|Van|Wagon|Pickup)"
    r"\s+4D(\s+(\d+(\s+\d+/\d+)?)(\s*ft))?$",
    re.IGNORECASE,
)
DISPLACEMENT_PAT = re.compile(r"^\d+(\.\d+)?[LS]?$", re.IGNORECASE)


def best_kbb_match(visor_trim: str, kbb_trims: list[str]) -> str | None:
    """
    Pick the best KBB trim match for a given Visor trim using fuzzy similarity.
    Falls back to the first item in case of tie (KBB orders trims by price).
    Returns None if no KBB trims are given.
    """
    if not kbb_trims:
        return None

    visor_norm = visor_trim.strip().lower()
    best_trim = None
    best_score = -1.0

    for kbb_trim in kbb_trims:
        score = SequenceMatcher(None, visor_norm, kbb_trim.lower()).ratio()
        if score > best_score:
            best_trim = kbb_trim
            best_score = score
        # if score == best_score, we *don’t* update best_trim
        # so the first occurrence stays selected (cheapest trim)

    return best_trim


def match_listing_to_kbb_trim(
    year, make, model, cased_trim, raw_trim, kbb_candidates
) -> str:
    # 1. Exact match on trim_version (raw_trim)
    for k in kbb_candidates:
        if k.endswith(raw_trim):
            return k

    # 2. Exact match on canonicalized trim
    for k in kbb_candidates:
        if k.endswith(cased_trim):
            return k

    # 3. Strip body style + try again
    for k in kbb_candidates:
        k_suffix = k.replace(f"{year} {make} {model}", "").strip()
        stripped = normalize_trim(k_suffix) or "Base"
        if stripped.lower() == cased_trim.lower():
            return k

    # 4. Fuzzy fallback
    suffixes = [k.replace(f"{year} {make} {model}", "").strip() for k in kbb_candidates]
    best_suffix = best_kbb_match(cased_trim, suffixes)
    if best_suffix:
        for k in kbb_candidates:
            if k.endswith(best_suffix):
                return k

    return ""


def normalize_trim(raw: str) -> str:
    """
    Simplified trim normalization:
    - Remove body style suffixes (e.g. "Sport Utility 4D")
    - Strip marketing fluff ("All-New", "The All New")
    - Collapse displacement-only trims (e.g. "2.5L") to empty
    Everything else is left intact for fuzzy matching.
    """
    name = raw.strip()

    # Remove body style suffixes
    name = BODY_STYLE_PATTERN.sub("", name).strip()
    tokens = name.split()

    # Remove "All-New" / "The All New"
    if len(tokens) >= 2 and tokens[0].lower() == "all" and tokens[1].lower() == "new":
        tokens = tokens[2:]
    elif tokens and tokens[0].lower() == "all-new":
        tokens = tokens[1:]
    elif (
        len(tokens) >= 3
        and tokens[0].lower() == "the"
        and tokens[1].lower() == "all"
        and tokens[2].lower() == "new"
    ):
        tokens = tokens[3:]

    # If the whole thing is just a displacement (e.g. "2.5L"), treat as empty
    if len(tokens) == 1 and DISPLACEMENT_PAT.match(tokens[0]):
        return ""

    return " ".join(tokens)


def resolve_cache_key(raw_title: str, cache_entries: dict[str, dict]) -> str:
    raw_title = raw_title.strip()
    year, make, model, *trim_parts = raw_title.split(maxsplit=3)
    raw_trim = trim_parts[0] if trim_parts else ""
    norm_trim = normalize_trim(raw_trim)

    # find any cache key with same year/make/model and normalized trim match
    ymm = f"{year} {make} {model}"
    related_keys = [k for k in cache_entries.keys() if k.startswith(ymm)]
    for k in related_keys:
        if k.startswith(ymm):
            cache_trim = k.replace(ymm, "").strip()
            if normalize_trim(cache_trim) == norm_trim:
                return k

    # fallback: case-insensitive whole string match
    for k in cache_entries.keys():
        if k.lower() == raw_title.lower():
            return k

    # if nothing matched, return the raw_title so the caller can handle KeyError
    return raw_title


def canonicalize_trim(raw_trim: str, model: str) -> str:
    """
    Normalize and apply consistent Title Case for trims across cache + listings.
    Removes body styles/displacements via normalize_trim first.
    """
    norm_trim = normalize_trim(raw_trim)
    if not norm_trim:
        return "Base"
        # if KBB trim is just the model name (e.g. "Forester"), map to Base
    if model and norm_trim.lower() == model.lower():
        return "Base"
    return " ".join(smart_title(p) for p in norm_trim.split())


def smart_title(token: str) -> str:
    # preserve all-caps for 3-letter codes or anything with a hyphen
    if len(token) <= 3 and token.isalpha():
        return token.upper()
    # Preserve casing for drivetrain / alphanumeric codes like 4x4, 4WD, etc.
    if token.lower() in DRIVETRAINS:
        return token
    if "-" in token:
        return token.upper()
    return token.title()

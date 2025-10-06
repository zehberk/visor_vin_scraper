import json
from playwright.async_api import Page

from analysis.cache import load_cache
from analysis.kbb_collector import get_missing_models
from analysis.normalization import best_kbb_model_match

from visor_scraper.constants import BAD_STRINGS, KBB_VARIANT_CACHE


def bool_from_url(val: str | None) -> bool:
    """True if a usable URL string appears present (not 'Unavailable'/empty/None)."""
    if not val:
        return False
    s = str(val).strip().lower()
    return s not in {"", "unavailable", "n/a", "none", "null"}


def percentile(values: list[int], p: float) -> float:
    """Inclusive-linear percentile; p in [0,1]."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    i = p * (len(s) - 1)
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    frac = i - lo
    return s[lo] * (1 - frac) + s[hi] * frac


# Price/mileage may be strings like "$32,500" or "52,025 mi"
def to_int(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    chars = "".join(ch for ch in str(val) if ch.isdigit())
    return int(chars) if chars else None


def is_trim_version_valid(trim_version: str) -> bool:
    if not trim_version or trim_version.strip().lower() in BAD_STRINGS:
        return False
    return any(c.isalnum() for c in trim_version)


async def get_variant_map(
    make: str, model: str, listings: list[dict]
) -> dict[str, list[dict]]:

    # Year, Make, list[Models/Variants]
    variant_cache: dict[str, dict[str, list[str]]] = load_cache(KBB_VARIANT_CACHE)
    candidate_map: dict[str, list[str]] = {}
    variant_map: dict[str, list[dict]] = {}

    stripped_model = model.replace("-", "")

    years = sorted(set({str(l["year"]) for l in listings}))
    prev_year = ""
    for year in years:
        cache_models = variant_cache.get(year, {}).get(make, [])
        # Get missing models if we don't find them
        if not cache_models:
            cache_models = await get_missing_models(year, make)

        models = [
            m
            for m in cache_models
            if model.lower() in m.lower()
            or m.lower() in model.lower()
            or stripped_model.lower() in m.lower()
            or m.lower() in stripped_model.lower()
        ]
        if not models:
            # print(
            #     f"No relevant models found, using previous year: {prev_year} {make} {model}."
            # )
            models = candidate_map.get(prev_year, [])
        candidate_map[year] = models
        prev_year = year

    no_match: list[dict] = []
    for l in listings:
        year = str(l["year"])

        if not candidate_map or not candidate_map[year]:
            no_match.append(l)
            continue
        elif len(candidate_map[year]) == 1:
            selected = candidate_map[year][0]
        else:
            selected = best_kbb_model_match(make, model, l, candidate_map[year])
            if selected is None:
                no_match.append(l)
                continue

        ymm = f"{year} {make} {selected}"
        variant_map.setdefault(ymm, []).append(l)

    # This is any entry in the variant map that has the most listings associated with it
    most_key = max(variant_map, key=lambda x: len(variant_map[x]))

    for l in no_match:
        year = str(l["year"])
        key_year = most_key[:4]
        variant = most_key.replace(key_year, "").replace(make, "").strip()
        if variant in candidate_map[year]:
            mod_key = most_key.replace(key_year, year)
        else:
            mod_key = year + " " + candidate_map[year][0]

        variant_map.setdefault(mod_key, []).append(l)

    return dict(sorted(variant_map.items()))


def find_variant_key(variant_map: dict[str, list[dict]], listing: dict) -> str | None:
    for key, listings in variant_map.items():
        if listing in listings:
            return key
    return None

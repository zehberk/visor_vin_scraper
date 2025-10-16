from collections import defaultdict

from analysis.cache import cache_covers_all, get_relevant_entries, load_cache
from analysis.kbb import get_trim_valuations_from_scrape
from analysis.kbb_collector import get_missing_models
from analysis.models import TrimValuation
from analysis.normalization import best_kbb_model_match, best_kbb_trim_match
from utils.constants import BAD_STRINGS, KBB_VARIANT_CACHE


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


def extract_years(slimmed: list[dict]) -> list[str]:
    """Extract unique 4-digit years from quicklist entries, sorted ascending."""
    years = {str(l["year"]) for l in slimmed if l.get("year")}
    return sorted(years)


def get_trim_valuations_from_cache(
    make: str, model: str, years: list[str], entries: dict
) -> list[TrimValuation]:
    trim_valuations = []
    for y in years:
        for entry in get_relevant_entries(entries, make, model, y).values():
            entry.setdefault("model", None)
            entry.setdefault("fmv", None)
            entry.setdefault("fmv_source", None)
            entry.setdefault("msrp", None)
            entry.setdefault("msrp_source", None)
            entry.setdefault("fpp", None)
            entry.setdefault("fpp_source", None)

            trim_valuations.append(TrimValuation.from_dict(entry))
    return trim_valuations


async def get_pricing_data(
    make: str, model: str, listings: list[dict], cache: dict
) -> list[TrimValuation]:
    cache_entries = cache.setdefault("entries", {})
    slugs = cache.setdefault("model_slugs", {})
    trim_options = cache.setdefault("trim_options", {})

    years = extract_years(listings)
    variant_map = await get_variant_map(make, model, listings)

    if cache_covers_all(make, list(variant_map.keys()), years, cache):
        return get_trim_valuations_from_cache(make, model, years, cache_entries)

    return await get_trim_valuations_from_scrape(
        make, model, slugs, listings, trim_options, cache_entries, cache
    )


def filter_valid_listings(
    make: str, model: str, listings: list[dict], cache_entries: dict, variant_map: dict
) -> tuple[list[dict], list[dict], defaultdict]:
    valid_entries: list[dict] = []
    skipped_listings: list[dict] = []
    skip_summary = defaultdict(lambda: defaultdict(int))

    for l in listings:
        year = str(l["year"])
        base_trim = (
            l["trim_version"] if is_trim_version_valid(l["trim_version"]) else l["trim"]
        )
        variant_model_key = find_variant_key(variant_map, l)
        variant_model = (
            variant_model_key.replace(year, "").replace(make, "").strip()
            if variant_model_key
            else model
        )
        entries = get_relevant_entries(cache_entries, make, variant_model, year)
        cache_key = best_kbb_trim_match(base_trim, list(entries.keys()))

        if (
            not cache_key
            or cache_key not in cache_entries
            or cache_entries[cache_key].get("skip_reason")
        ):
            skipped_listings.append(l)
            title = l.get("title", "Unknown")
            reason = cache_entries.get(cache_key, {}).get(
                "skip_reason", "Could not map KBB trim to Visor trim."
            )
            skip_summary[title][reason] += 1
            continue

        valid_entries.append(
            {
                "listing": l,
                "year": year,
                "base_trim": base_trim,
                "cache_key": cache_key,
            }
        )

    return valid_entries, skipped_listings, skip_summary

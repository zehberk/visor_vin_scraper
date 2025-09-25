from analysis.cache import load_cache

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


def get_variant_map(
    make: str, model: str, listings: list[dict]
) -> dict[str, list[dict]]:

    # Year, Make, list[Models/Variants]
    variant_cache: dict[str, dict[str, list[str]]] = load_cache(KBB_VARIANT_CACHE)

    mapped_by_title: dict[str, list[dict]] = {}
    for l in listings:
        year = l["year"]
        ymm = f"{year} {make} {model}"
        mapped_by_title.setdefault(ymm, []).append(l)
    sorted_mapping = dict(sorted(mapped_by_title.items()))

    variant_map: dict[str, list[dict]] = {}
    for ymm, listings in sorted_mapping.items():
        hybrid = f"{ymm} Hybrid"
        plugin = f"{ymm} Plug-in Hybrid"

        for l in listings:
            if l["is_plugin"] is True:
                variant_map.setdefault(plugin, []).append(l)
            elif l["is_hybrid"] is True:
                variant_map.setdefault(hybrid, []).append(l)
            else:
                variant_map.setdefault(ymm, []).append(l)

    return variant_map


def find_variant_key(variant_map: dict[str, list[dict]], listing: dict) -> str | None:
    for key, listings in variant_map.items():
        if listing in listings:
            return key
    return None

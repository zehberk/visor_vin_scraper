from visor_scraper.constants import BAD_STRINGS
from visor_scraper.utils import make_string_url_safe


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


def get_relevant_entries(
    entries: dict, make: str, model: str, year: str = ""
) -> dict[str, dict]:
    relevant_entries: dict = {}
    safe_make = make_string_url_safe(make)
    safe_model = make_string_url_safe(model)

    for key, entry in entries.items():
        url: str = entry.get("msrp_source", "").lower()
        if not url:
            continue

        path = url.replace("https://www.kbb.com/", "").replace("https://kbb.com/", "")
        parts = path.split("/")

        make_slug = parts[0] if len(parts) > 0 else ""
        model_slug = parts[1] if len(parts) > 1 else ""
        if safe_make == make_slug and safe_model in model_slug:
            if year:
                url_year = parts[2] if len(parts) > 2 else ""
                if year == url_year:
                    relevant_entries[key] = entry
                elif not url_year:
                    # Sometimes the source will not have a year because it is the current
                    # year, so we check the pricing timestamp as a precaution
                    timestamp: str = entry.get("pricing_timestamp", "")
                    if timestamp and timestamp.startswith(year):
                        relevant_entries[key] = entry
            else:
                relevant_entries[key] = entry

    return relevant_entries


def is_trim_version_valid(trim_version: str) -> bool:
    if not trim_version or trim_version.strip().lower() in BAD_STRINGS:
        return False
    return any(c.isalnum() for c in trim_version)


def get_variant_map(
    make: str, model: str, listings: list[dict]
) -> dict[str, list[dict]]:

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

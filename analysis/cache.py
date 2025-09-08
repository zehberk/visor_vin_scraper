import json

from datetime import datetime, timedelta
from pathlib import Path


CACHE_FILE = Path("output") / "level1_pricing_cache.json"
CACHE_TTL = timedelta(days=7)


def load_cache():
    if CACHE_FILE.exists():
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"entries": {}}


def save_cache(cache):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def prepare_cache():
    cache = load_cache()
    slugs = cache.setdefault("model_slugs", {})
    trim_options = cache.setdefault("trim_options", {})
    cache_entries = cache.setdefault("entries", {})
    return cache, slugs, trim_options, cache_entries


def is_fmv_fresh(entry):
    if "timestamp" not in entry:
        return False
    ts = datetime.fromisoformat(entry["timestamp"])
    return datetime.now() - ts < CACHE_TTL


def is_pricing_fresh(entry: dict) -> bool:
    ts = entry.get("pricing_timestamp")
    if not ts:
        return False
    saved = datetime.fromisoformat(ts)
    now = datetime.now()

    # Fresh if we're still in the same month & year
    return (saved.year == now.year) and (saved.month == now.month)


def cache_covers_all(
    make: str, model: str, years: list[str], trim_map: dict, cache: dict
) -> bool:
    slugs = cache.get("model_slugs", {})
    trim_options = cache.get("trim_options", {})
    cache_entries = cache.get("entries", {})

    make_model_key = f"{make} {model}"

    # Check model slug
    if make_model_key not in slugs:
        return False

    # Check trims
    if not (
        make_model_key in trim_options
        and all(y in trim_options[make_model_key] for y in years)
    ):
        return False

    # Check FMVs for every visor_trim
    for year, trims in trim_map.items():
        for trim in trims.keys():
            visor_trim = f"{year} {make} {model} {trim}"
            if visor_trim not in cache_entries or not is_fmv_fresh(
                cache_entries[visor_trim]
            ):
                return False

    return True

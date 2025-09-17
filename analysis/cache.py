import json

from datetime import datetime, timedelta
from pathlib import Path

from analysis.utils import get_relevant_entries


CACHE_FILE = Path("output") / "level1_pricing_cache.json"
CACHE_TTL = timedelta(days=7)


def load_cache() -> dict[str, dict]:
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
    slugs: dict[str, str] = cache.setdefault("model_slugs", {})
    trim_options: dict[str, dict[str, list[str]]] = cache.setdefault("trim_options", {})
    cache_entries: dict = cache.setdefault("entries", {})
    return cache, slugs, trim_options, cache_entries


def is_fmv_fresh(entry):
    if "timestamp" not in entry or not entry.get("timestamp"):
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


def cache_covers_all(make: str, model: str, years: list[str], cache: dict) -> bool:
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

    if len(cache_entries) == 0:
        return False

    for entry in get_relevant_entries(cache_entries, make, model):
        if not is_fmv_fresh(entry):
            return False

    return True

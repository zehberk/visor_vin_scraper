import json

from datetime import datetime
from pathlib import Path

from utils.common import make_string_url_safe
from utils.constants import *


def load_cache(cache_file: Path = PRICING_CACHE) -> dict[str, dict]:
    if cache_file.exists():
        with cache_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"entries": {}}


def save_cache(cache: dict, cache_file: Path = PRICING_CACHE):
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with cache_file.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def is_entry_fresh(entry: dict):
    if (
        "pricing_timestamp" not in entry
        or not entry.get("pricing_timestamp", "")
        or "timestamp" not in entry
        or not entry.get("timestamp", "")
    ):
        return False
    fpp_ts = datetime.fromisoformat(entry["pricing_timestamp"])
    is_fpp_fresh = datetime.now() - fpp_ts < CACHE_TTL
    fmv_ts = datetime.fromisoformat(entry["timestamp"])
    is_fmv_fresh = datetime.now() - fmv_ts < CACHE_TTL

    return is_fpp_fresh and is_fmv_fresh


def is_fpp_fresh(entry: dict) -> bool:
    if "pricing_timestamp" not in entry or not entry.get("pricing_timestamp", ""):
        return False
    fpp_ts = datetime.fromisoformat(entry["pricing_timestamp"])

    return datetime.now() - fpp_ts < CACHE_TTL


def is_fmv_fresh(entry: dict) -> bool:
    if "timestamp" not in entry or not entry.get("timestamp", ""):
        return False
    fmv_ts = datetime.fromisoformat(entry["timestamp"])

    return datetime.now() - fmv_ts < CACHE_TTL


def cache_covers_all(
    make: str, variants: list[str], years: list[str], cache: dict
) -> bool:
    cache_entries = cache.get("entries", {})
    slugs = cache.get("model_slugs", {})
    trim_options = cache.get("trim_options", {})

    if len(cache_entries) == 0:
        return False

    for ymm in variants:
        year: str = ymm[:4]
        make_model_key: str = ymm[5:].strip()
        model = make_model_key.replace(make, "")

        # Check model slug
        if ymm not in slugs:
            return False

        # Check trims
        if not (
            make_model_key in trim_options
            and all(y in trim_options[make_model_key] for y in years)
        ):
            return False

        relevant_entries = get_relevant_entries(cache_entries, make, model, year)
        for entry in relevant_entries.values():
            if is_entry_fresh(entry) is False:
                return False

    return True


def get_relevant_entries(
    entries: dict, make: str, model: str, year: str = ""
) -> dict[str, dict]:
    relevant_entries: dict = {}
    safe_make = make_string_url_safe(make)
    safe_model = make_string_url_safe(model)
    stripped_safe_model = safe_model.replace("-", "")

    for key, entry in entries.items():
        url: str = entry.get("msrp_source", "").lower()
        if not url:
            continue

        path = url.replace("https://www.kbb.com/", "").replace("https://kbb.com/", "")
        parts = path.split("/")

        make_slug = parts[0] if len(parts) > 0 else ""
        model_slug = parts[1] if len(parts) > 1 else ""
        if safe_make == make_slug and (
            safe_model in model_slug or stripped_safe_model in model_slug
        ):
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

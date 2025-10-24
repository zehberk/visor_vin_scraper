import json

from datetime import datetime
from pathlib import Path

from analysis.utils import get_relevant_entries
from utils.constants import *
from utils.models import *


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
        "natl_timestamp" not in entry
        or not entry.get("natl_timestamp", "")
        or "local_timestamp" not in entry
        or not entry.get("local_timestamp", "")
    ):
        return False

    return is_natl_fresh(entry) and is_local_fresh(entry)


def is_natl_fresh(entry: dict) -> bool:
    if "natl_timestamp" not in entry or not entry.get("natl_timestamp", ""):
        return False
    natl_ts = datetime.fromisoformat(entry["natl_timestamp"])

    return datetime.now() - natl_ts < CACHE_TTL


def is_local_fresh(entry: dict) -> bool:
    if "local_timestamp" not in entry or not entry.get("local_timestamp", ""):
        return False
    fmv_ts = datetime.fromisoformat(entry["local_timestamp"])

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
        if len(relevant_entries) == 0:
            return False

        for entry in relevant_entries.values():
            if is_entry_fresh(entry) is False:
                return False

    return True


def get_trim_valuations_from_cache(
    make: str, model: str, years: list[str], entries: dict
) -> list[TrimValuation]:
    trim_valuations = []
    for y in years:
        for entry in get_relevant_entries(entries, make, model, y).values():
            entry.setdefault("model", None)
            entry.setdefault("kbb_trim", None)
            entry.setdefault("msrp", None)
            entry.setdefault("fpp_natl", None)
            entry.setdefault("fmr_low", None)
            entry.setdefault("fmr_high", None)
            entry.setdefault("fpp_local", None)
            entry.setdefault("fmv", None)
            entry.setdefault("natl_source", None)
            entry.setdefault("local_source", None)

            trim_valuations.append(TrimValuation.from_dict(entry))
    return trim_valuations

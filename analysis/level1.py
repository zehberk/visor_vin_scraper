from __future__ import annotations

import re

from analysis.cache import *
from analysis.kbb import get_trim_valuations_from_cache, get_trim_valuations_from_scrape
from analysis.models import CarListing, DealBin, TrimValuation
from analysis.normalization import canonicalize_trim, resolve_cache_key
from analysis.outliers import summarize_outliers
from analysis.reporting import to_level1_json, render_pdf
from analysis.scoring import (
    build_bins_and_crosstab,
    compute_condition_distribution_total,
    deviation_pct,
    rate_deal,
    rate_risk,
    rate_uncertainty,
)
from analysis.utils import bool_from_url


def build_unique_trim_map(
    quicklist: list[str], make: str, model: str
) -> dict[str, dict[str, list[str]]]:
    trim_map: dict[str, dict[str, list[str]]] = {}
    for ymmt in quicklist:
        # Remove make/model, extract year
        year_trim = ymmt.replace(make, "").replace(model, "").strip()
        year = year_trim[:4]
        if year not in trim_map:
            trim_map[year] = {}

        # Raw trim without the year
        raw_trim = year_trim.replace(year, "").strip()
        # Normalize + canonicalize casing
        cased = canonicalize_trim(raw_trim, model)
        if cased not in trim_map[year]:
            trim_map[year][cased] = []
    return trim_map


def _price_history_lowest(price_history: list[dict] | None) -> bool:
    """True if any entry marks lowest=True."""
    if not price_history:
        return False
    for p in price_history:
        try:
            if bool(p.get("lowest")):
                return True
        except Exception:
            pass
    return False


def _days_on_market(listing: dict) -> int | None:
    """Pull DOM from common locations."""
    # Preferred: nested velocity block
    try:
        dom = listing.get("market_velocity", {}).get("this_vehicle_days")
        avg = listing.get("market_velocity", {}).get("avg_days_on_market")
        return int(dom) - int(avg) if dom is not None and avg is not None else None
    except Exception:
        pass
    return None


def extract_years(quicklist: list[str]) -> list[str]:
    """Extract unique 4-digit years from quicklist entries, sorted ascending."""
    years = {ymmt[:4] for ymmt in quicklist if ymmt[:4].isdigit()}
    return sorted(years)


def build_quicklist(slimmed: list[dict], make: str, model: str) -> list[str]:
    def _year_key(t: str) -> int:
        m = re.match(r"^\s*(\d{4})\b", t)
        return int(m.group(1)) if m else 9999

    titles = []
    for l in slimmed:
        raw_title = str(l.get("title", "")).strip()
        if not raw_title:
            continue
        year = raw_title[:4]
        raw_trim = (
            raw_title.replace(year, "").replace(make, "").replace(model, "").strip()
        )
        cased_trim = canonicalize_trim(raw_trim, model)
        titles.append(f"{year} {make} {model} {cased_trim}")

    unique = sorted(set(titles), key=lambda t: (_year_key(t), t.lower()))
    return unique


def slim(listing: dict) -> dict:
    """Convert a raw listing into the minimal Level-1 schema."""

    # Price/mileage may be strings like "$32,500" or "52,025 mi"
    def _to_int(val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return int(val)
        chars = "".join(ch for ch in str(val) if ch.isdigit())
        return int(chars) if chars else None

    addl = listing.get("additional_docs", {}) or {}
    carfax_present = bool_from_url(addl.get("carfax_url"))
    autocheck_present = bool_from_url(addl.get("autocheck_url"))
    sticker_present = bool_from_url(addl.get("window_sticker_url"))

    war = listing.get("warranty", {}) or {}
    # Treat "present" as: either a non-unknown overall_status or any coverages listed
    warranty_present = bool(war.get("coverages")) or (
        str(war.get("overall_status", "")).strip().lower()
        not in {"", "unknown", "n/a", "none"}
    )

    return {
        "id": listing.get("id"),
        "vin": listing.get("vin"),
        "title": listing.get("title"),
        "condition": listing.get("condition"),
        "price": _to_int(listing.get("price")),
        "mileage": _to_int(listing.get("mileage")),
        "days_on_market_delta": _days_on_market(listing),
        "price_history_lowest": _price_history_lowest(listing.get("price_history")),
        "report_present": carfax_present or autocheck_present,
        "window_sticker_present": sticker_present,
        "warranty_info_present": warranty_present,
    }


async def create_level1_file(listings: list[dict], metadata: dict):
    cache, slugs, trim_options, cache_entries = prepare_cache()
    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]
    quicklist = build_quicklist(listings, make, model)
    years = extract_years(quicklist)
    trim_map = build_unique_trim_map(quicklist, make, model)
    # Sort listings by mileage first to hopefully get valid VINs first (KBB may not have info for newer vehicles)
    vin_data = sorted(listings, key=lambda x: x["mileage"])
    vins = [entry["vin"] for entry in vin_data]

    trim_valuations: list[TrimValuation]
    if cache_covers_all(make, model, years, trim_map, cache):
        trim_valuations = get_trim_valuations_from_cache(
            make, model, trim_map, cache_entries
        )
    else:
        trim_valuations = await get_trim_valuations_from_scrape(
            make,
            model,
            years,
            vins,
            trim_map,
            slugs,
            trim_options,
            cache_entries,
            cache,
        )

    no_price_bin = DealBin(category="No Price", listings=[], count=0)
    all_listings: list[CarListing] = []
    seen_ids: set[str] = set()  # guard if input has dupes
    skipped_listings = []

    for listing in listings:
        raw_title = listing["title"].strip()
        year = raw_title[:4]
        raw_trim = (
            raw_title.replace(year, "").replace(make, "").replace(model, "").strip()
        )
        cased_trim = canonicalize_trim(raw_trim, model)
        prefix = f"{year} {make} {model}"
        listing_key = f"{prefix} {cased_trim}".strip()
        cache_key = resolve_cache_key(listing_key, cache_entries)
        # related_keys = [k for k in cache_entries.keys() if k.startswith(prefix)]
        # print("DEBUG listing_key:", listing_key)
        # print("DEBUG related cache keys:", related_keys)
        if cache_key not in cache_entries:
            # No valid mapping — skip this listing entirely
            skipped_listings.append(listing)
            print(f"⚠️ Skipping listing with unmapped trim: {listing_key}")
            continue
        fmv = cache_entries[cache_key].get("fmv", None)
        fpp = cache_entries[cache_key].get("fpp")
        if listing.get("price"):
            price = listing["price"]
            # New prices should be compared to fair purpose price, while Used and Certified
            # should use fair market value. If there is no FMV, then we default to FPP
            if listing["condition"] == "New" or fmv is None:
                delta = price - fpp
                compare_price = fpp
            else:
                delta = price - fmv
                compare_price = fmv
        else:
            # Listings with no price can't be compared
            price = 0
            delta = 0
            compare_price = 0

        deal = rate_deal(price, delta, compare_price)
        uncertainty = rate_uncertainty(listing)
        risk = rate_risk(listing, price, compare_price)

        year = cache_key[:4]
        trim = cased_trim

        car_listing = CarListing(
            id=listing["id"],
            vin=listing["vin"],
            year=int(year),
            make=make,
            model=model,
            trim=trim,
            title=cache_key,
            condition=listing["condition"],
            miles=listing["mileage"],
            price=price,
            price_delta=delta,
            uncertainty=uncertainty,
            risk=risk,
            deal_rating=deal,
            compare_price=compare_price,
            msrp=cache_entries[cache_key]["msrp"],
            fpp=fpp,
            fmv=fmv,
            deviation_pct=deviation_pct(price, fmv),
        )

        if deal == "No price":
            no_price_bin.listings.append(car_listing)
            no_price_bin.count += 1
            all_listings.append(car_listing)
            continue

        # single append, guarded
        if car_listing.id not in seen_ids:
            seen_ids.add(car_listing.id)
            all_listings.append(car_listing)

    deal_bins, crosstab = build_bins_and_crosstab(all_listings)
    bin_map = {b.category: b for b in deal_bins}
    great_bin = bin_map["Great"]
    good_bin = bin_map["Good"]
    fair_bin = bin_map["Fair"]
    poor_bin = bin_map["Poor"]
    bad_bin = bin_map["Bad"]

    cond_dist_total = compute_condition_distribution_total(all_listings)

    analysis_json = to_level1_json(
        make=make,
        model=model,
        sort=metadata["filters"]["sort"],  # already available in start_level1_analysis
        deal_bins=deal_bins,
        crosstab=crosstab,
        skipped_listings=skipped_listings,
    )
    analysis_json["condition_distribution"] = cond_dist_total

    outliers_json = summarize_outliers(all_listings)

    # Only pass along the entries from the quicklist, not the entire cache
    visible_entries = {k: cache_entries[k] for k in quicklist if k in cache_entries}

    await render_pdf(
        make,
        model,
        visible_entries,
        all_listings,
        trim_valuations,
        deal_bins,
        great_bin,
        good_bin,
        fair_bin,
        poor_bin,
        bad_bin,
        no_price_bin,
        analysis_json,
        outliers_json,
        crosstab,
        metadata,
    )


async def start_level1_analysis(
    listings: list[dict], metadata: dict, args, timestamp: str
):
    """
    Builds 'level1_input_<Make>_<Model>_<Timestamp>.jsonc' next to your outputs.
    Returns the file path.
    Call this AFTER you've saved listings.json and closed the browser.
    """
    if not listings:
        raise ValueError("No listings provided to create_level1_file().")

    # Slim all listings
    slimmed = [slim(l) for l in listings if l is not None]

    await create_level1_file(slimmed, metadata)

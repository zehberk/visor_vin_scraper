from __future__ import annotations

import asyncio, glob, json, os

from collections import defaultdict

from analysis.cache import get_relevant_entries, load_cache
from analysis.models import CarListing, DealBin, TrimValuation
from analysis.normalization import best_kbb_trim_match
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
from analysis.utils import (
    bool_from_url,
    get_pricing_data,
    filter_valid_listings,
    find_variant_key,
    get_variant_map,
    is_trim_version_valid,
    to_int,
)

from utils.constants import PRICING_CACHE


def slim(listing: dict) -> dict:
    """Convert a raw listing into the minimal Level-1 schema."""

    addl = listing.get("additional_docs", {}) or {}
    carfax_present: bool = bool_from_url(addl.get("carfax_url"))
    autocheck_present: bool = bool_from_url(addl.get("autocheck_url"))
    sticker_present: bool = bool_from_url(addl.get("window_sticker_url"))

    specs: dict = listing.get("specs", {})
    fuel_type: str = specs.get("Fuel Type", "").strip().lower()
    listing_url: str = listing.get("listing_url", "").lower()
    if not listing_url:
        print(f"Listing URL invalid for {listing.get("id")}")

    is_hybrid = False
    is_plugin = False
    if "hybrid" in fuel_type or "hybrid" in listing_url:
        is_hybrid = True
        if "plug" in fuel_type or "plug" in listing_url:
            is_plugin = True
    elif fuel_type == "" or fuel_type == "not specified":
        is_hybrid = None  # unknown
        is_plugin = None  # unknown

    war = listing.get("warranty", {}) or {}
    # Treat "present" as: either a non-unknown overall_status or any coverages listed
    warranty_present = bool(war.get("coverages")) or (
        str(war.get("overall_status", "")).strip().lower()
        not in {"", "unknown", "n/a", "none"}
    )

    tv = specs.get("Trim Version", "")
    valid_tv = tv if is_trim_version_valid(tv) else ""

    return {
        "id": listing.get("id"),
        "vin": listing.get("vin"),
        "title": listing.get("title"),
        "year": listing.get("year"),
        "trim": listing.get("trim"),
        "trim_version": valid_tv,
        "condition": listing.get("condition"),
        "price": to_int(listing.get("price")),
        "mileage": to_int(listing.get("mileage")),
        "is_hybrid": is_hybrid,
        "is_plugin": is_plugin,
        "report_present": carfax_present or autocheck_present,
        "window_sticker_present": sticker_present,
        "warranty_info_present": warranty_present,
    }


async def create_level1_file(listings: list[dict], metadata: dict):
    cache = load_cache(PRICING_CACHE)
    cache_entries: dict = cache.setdefault("entries", {})

    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]

    variant_map = await get_variant_map(make, model, listings)

    trim_valuations: list[TrimValuation] = await get_pricing_data(
        make, model, listings, cache
    )

    no_price_bin = DealBin(category="No Price", listings=[], count=0)
    all_listings: list[CarListing] = []
    seen_ids: set[str] = set()  # guard if input has dupes

    valid_data, skipped_listings, skip_summary = filter_valid_listings(
        make, model, listings, cache_entries, variant_map
    )

    for item in valid_data:
        listing = item["listing"]
        cache_key = item["cache_key"]
        year = item["year"]
        base_trim = item["base_trim"]

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

        car_listing = CarListing(
            id=listing["id"],
            vin=listing["vin"],
            year=int(year),
            make=make,
            model=model,
            trim=base_trim,
            trim_version=listing["trim_version"],
            title=listing["title"],
            cache_key=cache_key,
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
    quicklist = sorted(
        {l.cache_key for l in all_listings if l.cache_key in cache_entries}
    )
    visible_entries = {
        k: TrimValuation.from_dict({**cache_entries[k], "kbb_trim": k})
        for k in quicklist
    }

    # Output skipped listing reasons
    skip_messages: list[str] = []
    # print("The following models have been skipped for these reasons:")
    for title, reasons in sorted(skip_summary.items()):
        for reason, count in reasons.items():
            # print(f"  - {title}: {reason} ({count})")
            skip_messages.append(f"{title}: {reason} ({count})")

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
        skip_messages,
    )


async def start_level1_analysis(
    listings: list[dict], metadata: dict, args, timestamp: str
) -> None:
    if not listings:
        raise ValueError("No listings provided to create_level1_file().")

    # Slim all listings
    slimmed = [slim(l) for l in listings if l is not None]
    await create_level1_file(slimmed, metadata)


if __name__ == "__main__":
    json_files = glob.glob(os.path.join("output/raw", "*.json"))
    latest_json_file = max(json_files, key=os.path.getmtime)
    data: dict = {}
    with open(latest_json_file, "r") as file:
        data = json.load(file)
    metadata = data.get("metadata", {})
    listings = data.get("listings", {})
    if metadata and listings:
        asyncio.run(start_level1_analysis(listings, metadata, None, ""))

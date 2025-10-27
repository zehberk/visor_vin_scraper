from __future__ import annotations

import asyncio, glob, json, os

from analysis.cache import load_cache
from analysis.kbb import get_pricing_data
from analysis.normalization import (
    filter_valid_listings,
    get_variant_map,
    normalize_listing,
)
from analysis.outliers import summarize_outliers
from analysis.reporting import to_level1_json, render_pdf
from analysis.scoring import (
    build_bins_and_crosstab,
    classify_deal_rating,
    compute_condition_distribution_total,
    determine_best_price,
    deviation_pct,
    rate_risk_level1,
    rate_uncertainty,
)

from utils.constants import PRICING_CACHE
from utils.models import CarListing, DealBin, TrimValuation


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

        msrp = int(cache_entries[cache_key].get("msrp"))
        fpp_natl = int(cache_entries[cache_key].get("fpp_natl", 0))
        fpp_local = int(cache_entries[cache_key].get("fpp_local", 0))
        fmr_high = int(cache_entries[cache_key].get("fmr_high", 0))
        fmv = int(cache_entries[cache_key].get("fmv", 0))

        price = int(listing.get("price", 0))
        best_comparison = determine_best_price(price, fpp_local, fpp_natl, fmv, msrp)

        deal, midpoint, _ = classify_deal_rating(
            price, best_comparison, fmv, fpp_local, fmr_high
        )
        uncertainty = rate_uncertainty(listing)
        risk = rate_risk_level1(listing, price, midpoint)

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
            price_delta=price - midpoint if price else 0,
            uncertainty=uncertainty,
            risk=risk,
            deal_rating=deal,
            compare_price=midpoint,
            msrp=msrp,
            fpp_natl=fpp_natl,
            fpp_local=fpp_local,
            fmv=fmv,
            deviation_pct=deviation_pct(price, midpoint),
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

    # Normalize all listings
    normalized = [normalize_listing(l) for l in listings if l is not None]
    await create_level1_file(normalized, metadata)


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

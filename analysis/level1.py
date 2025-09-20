from __future__ import annotations
from collections import defaultdict

from analysis.cache import *
from analysis.kbb import get_trim_valuations_from_cache, get_trim_valuations_from_scrape
from analysis.models import CarListing, DealBin, TrimValuation
from analysis.normalization import (
    canonicalize_trim,
    match_listing_to_kbb_trim,
)
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
    get_relevant_entries,
    is_trim_version_valid,
    to_int,
)


def extract_years(slimmed: list[dict]) -> list[str]:
    """Extract unique 4-digit years from quicklist entries, sorted ascending."""
    years = {str(l["year"]) for l in slimmed if l.get("year")}
    return sorted(years)


def slim(listing: dict) -> dict:
    """Convert a raw listing into the minimal Level-1 schema."""

    addl = listing.get("additional_docs", {}) or {}
    carfax_present: bool = bool_from_url(addl.get("carfax_url"))
    autocheck_present: bool = bool_from_url(addl.get("autocheck_url"))
    sticker_present: bool = bool_from_url(addl.get("window_sticker_url"))

    fuel_type = listing["specs"].get("Fuel Type", "").strip().lower()
    if "hybrid" in fuel_type:
        is_hybrid = True
    elif fuel_type == "" or fuel_type == "not specified":
        is_hybrid = None  # unknown
    else:
        is_hybrid = False

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
        "year": listing.get("year"),
        "trim": listing.get("trim"),
        "trim_version": listing["specs"].get("Trim Version"),
        "condition": listing.get("condition"),
        "price": to_int(listing.get("price")),
        "mileage": to_int(listing.get("mileage")),
        "is_hybrid": is_hybrid,
        "report_present": carfax_present or autocheck_present,
        "window_sticker_present": sticker_present,
        "warranty_info_present": warranty_present,
    }


async def create_level1_file(listings: list[dict], metadata: dict):
    cache, slugs, trim_options, cache_entries = prepare_cache()
    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]
    years = extract_years(listings)

    trim_valuations: list[TrimValuation]
    if cache_covers_all(make, model, years, cache):
        trim_valuations = get_trim_valuations_from_cache(make, model, cache_entries)
    else:
        trim_valuations = await get_trim_valuations_from_scrape(
            make,
            model,
            slugs,
            listings,
            trim_options,
            cache_entries,
            cache,
        )

    no_price_bin = DealBin(category="No Price", listings=[], count=0)
    all_listings: list[CarListing] = []
    seen_ids: set[str] = set()  # guard if input has dupes
    skipped_listings = []
    skip_summary: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for listing in listings:
        year = listing["year"]
        base_trim = (
            listing["trim_version"]
            if is_trim_version_valid(listing["trim_version"])
            else listing["trim"]
        )
        cased_trim = canonicalize_trim(base_trim, model)
        entries = get_relevant_entries(cache_entries, make, model, year)
        keys = [v.get("kbb_trim_option", k) for k, v in entries.items()]
        cache_key = match_listing_to_kbb_trim(
            year, make, model, cased_trim, base_trim, keys
        )

        if not cache_key or (
            cache_key not in cache_entries
            or cache_entries[cache_key].get("skip_reason")
        ):
            skipped_listings.append(listing)
            title = listing["title"]
            reason = cache_entries.get(cache_key, {}).get(
                "skip_reason", "Could not map KBB trim to Visor trim."
            )
            skip_summary[title][reason] += 1
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

        car_listing = CarListing(
            id=listing["id"],
            vin=listing["vin"],
            year=int(year),
            make=make,
            model=model,
            trim=cased_trim,
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
    visible_entries = {
        k: TrimValuation.from_dict({**v, "kbb_trim": k})
        for k, v in get_relevant_entries(cache_entries, make, model).items()
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
):
    if not listings:
        raise ValueError("No listings provided to create_level1_file().")

    # Slim all listings
    slimmed = [slim(l) for l in listings if l is not None]
    await create_level1_file(slimmed, metadata)

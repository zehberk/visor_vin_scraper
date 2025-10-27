import asyncio, glob, json, os

from pathlib import Path

from analysis.cache import load_cache
from analysis.kbb import get_pricing_data
from analysis.normalization import (
    filter_valid_listings,
    get_variant_map,
    normalize_listing,
)
from analysis.scoring import (
    adjust_deal_for_risk,
    classify_deal_rating,
    determine_best_price,
    rate_risk_level2,
)

from utils.carfax_parser import get_carfax_data
from utils.constants import *
from utils.download import download_files, download_report_pdfs
from utils.models import CarfaxData, StructuralStatus, TrimValuation


def get_vehicle_dir(listing: dict) -> Path | None:
    title = listing.get("title")
    vin = listing.get("vin")
    if title is None or vin is None:
        return None
    path = Path(DOC_PATH) / title / vin
    return path if path.is_dir() else None


def get_report_dir(listing: dict) -> Path | None:
    dir = get_vehicle_dir(listing)
    # TODO: add auto-check
    return dir / "carfax.html" if dir else None


def check_missing_docs(listings: list[dict]):
    # Check to see if files exists
    missing_reports = []
    for l in listings:
        dir = get_vehicle_dir(l)
        if dir is None:
            continue
        html = dir / "carfax.html"
        pdf = dir / "carfax.pdf"
        unavail = dir / "carfax_unavailable.txt"

        carfax_url = l.get("additional_docs", {}).get("carfax_url", "Unavailable")
        if (
            not pdf.exists()
            and not unavail.exists()
            and not html.exists()
            and carfax_url != "Unavailable"
        ):
            missing_reports.append(l)

    if missing_reports:
        print(f"Downloading reports for {len(missing_reports)} listings...")
        download_report_pdfs(missing_reports)


async def start_level2_analysis(metadata: dict, listings: list[dict]):
    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]

    cache = load_cache(PRICING_CACHE)
    cache_entries: dict = cache.setdefault("entries", {})
    variant_map = await get_variant_map(make, model, listings)

    # Ensure all folders exist, and if not, save the documents
    if not all(get_vehicle_dir(l) for l in listings):
        await download_files(listings)

    # Check for missings documents (pdfs, html)
    check_missing_docs(listings)

    # Filter out only the listings that have a valid report
    filtered_listings = []
    for l in listings:
        report = get_report_dir(l)
        if report and report.exists():
            filtered_listings.append(normalize_listing(l))

    trim_valuations = await get_pricing_data(make, model, listings, cache)

    valid_listings, skipped_listings, skip_summary = filter_valid_listings(
        make, model, filtered_listings, cache_entries, variant_map
    )

    # Extract Carfax report
    for l in valid_listings:
        report = get_report_dir(l)
        if report is None or not report.exists():
            continue

        narrative: list[str] = []
        carfax: CarfaxData = get_carfax_data(report)
        risk = rate_risk_level2(carfax, l)

        listing = l["listing"]
        cache_key = l["cache_key"]
        year = l["year"]
        base_trim = l["base_trim"]

        msrp = int(cache_entries[cache_key].get("msrp"))
        fpp_natl = int(cache_entries[cache_key].get("fpp_natl", 0))
        fpp_local = int(cache_entries[cache_key].get("fpp_local", 0))
        fmr_high = int(cache_entries[cache_key].get("fmr_high", 0))
        fmv = int(cache_entries[cache_key].get("fmv", 0))

        price = int(listing.get("price", 0))
        best_comparison = determine_best_price(price, fpp_local, fpp_natl, fmv, msrp)

        deal, midpoint, increment = classify_deal_rating(
            price, best_comparison, fmv, fpp_local, fmr_high
        )

        if deal == "Great" and midpoint and price < midpoint - increment * 3:
            deal = "Suspicious"

        deal = adjust_deal_for_risk(deal, risk, narrative)

    if len(valid_listings) == 0:
        print("Unable to perform level2 analysis: no valid listings found")
    else:
        print(f"{len(valid_listings)} valid listings found.")


if __name__ == "__main__":
    json_files = glob.glob(os.path.join("output/raw", "*.json"))
    latest_json_file = max(json_files, key=os.path.getmtime)
    data: dict = {}
    with open(latest_json_file, "r") as file:
        data = json.load(file)
    metadata = data.get("metadata", {})
    listings = data.get("listings", {})
    if metadata and listings:
        asyncio.run(start_level2_analysis(metadata, listings))

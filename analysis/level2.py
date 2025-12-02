import asyncio, glob, json, os, time

from pathlib import Path

from utils.cache import load_cache
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
from analysis.reporting import render_level2_pdf

from utils.carfax_parser import get_carfax_data
from utils.common import stopwatch
from utils.constants import *
from utils.download import download_files, download_report_pdfs
from utils.models import CarfaxData


timing = {
    "total_listing": [],
    "deal_bins": [],
    "carfax": [],
    "risk": [],
    "narrative": [],
}


def report_stats(label: str, values: list[float]):
    if not values:
        return f"{label}: no data\n"
    avg = sum(values) / len(values)
    mn = min(values)
    mx = max(values)
    return (
        f"{label}:\n"
        f"  avg: {avg:.4f}s\n"
        f"  min: {mn:.4f}s\n"
        f"  max: {mx:.4f}s\n"
        f"  count: {len(values)}\n"
    )


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

        carfax_url = l.get("additional_docs", {}).get("carfax_url", "Unavailable")
        if carfax_url != "Unavailable" and not pdf.exists() and not html.exists():
            missing_reports.append(l)

    if missing_reports:
        print(f"Downloading reports for {len(missing_reports)} listings...")
        download_report_pdfs(missing_reports)


async def start_level2_analysis(metadata: dict, listings: list[dict], filename: str):
    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]

    cache = load_cache(PRICING_CACHE)
    cache_entries: dict = cache.setdefault("entries", {})
    variant_map = await get_variant_map(make, model, listings)

    # Ensure all folders exist, and if not, save the documents
    if not all(get_vehicle_dir(l) for l in listings):
        await download_files(listings, filename)

    # Check for missings documents (pdfs, html)
    check_missing_docs(listings)

    # Filter out only the listings that have a valid report
    filtered_listings = []
    for vl in listings:
        report = get_report_dir(vl)
        if report and report.exists():
            filtered_listings.append(normalize_listing(vl))

    await get_pricing_data(make, model, listings, cache)

    valid_listings, _, _ = filter_valid_listings(
        make, model, filtered_listings, cache_entries, variant_map
    )

    # listing, deal, risk, narrative
    ratings: list[tuple[dict, str, int, list[str]]] = []

    # Extract Carfax report
    for vl in sorted(valid_listings, key=lambda x: x["listing"]["id"]):
        listing: dict = vl["listing"]
        cache_key = vl["cache_key"]

        # Total listing timer
        t_total = time.perf_counter()

        full_listing = next(l for l in listings if l.get("id") == listing.get("id"))
        report = get_report_dir(full_listing)
        if report is None or not report.exists() or listing.get("price") is None:
            continue

        narrative: list[str] = []

        price = int(listing.get("price", 0))
        fpp_natl = int(cache_entries[cache_key].get("fpp_natl", 0))
        fpp_local = int(cache_entries[cache_key].get("fpp_local", 0))
        fmr_high = int(cache_entries[cache_key].get("fmr_high", 0))
        fmv = int(cache_entries[cache_key].get("fmv", 0))

        if not (fpp_natl and fpp_local and fmv):
            narrative.append(
                "Unable to provide ratings for this vehicle: no pricing data is available for this vehicle."
            )
            continue

        t_narr = time.perf_counter()
        narrative.append(f"This vehicle is being listed at ${price}.")

        t_bins = time.perf_counter()
        best_comparison = determine_best_price(
            price, fpp_local, fpp_natl, fmv, narrative
        )
        deal, midpoint, increment, percent = classify_deal_rating(
            price, best_comparison, fmv, fpp_local, fmr_high
        )
        deal_time = time.perf_counter() - t_bins
        narrative.append(
            f"Deal bins are set at ${increment * 2} ({percent * 200}%) in size, placing the Fair midpoint at ${midpoint}."
        )
        if deal == "Great" and midpoint and price < midpoint - increment * 3:
            deal = "Suspicious"

        t_carfax = time.perf_counter()
        carfax: CarfaxData = get_carfax_data(report)
        carfax_time = time.perf_counter() - t_carfax

        t_risk = time.perf_counter()
        risk = rate_risk_level2(carfax, listing, narrative)
        risk_time = time.perf_counter() - t_risk

        deal = adjust_deal_for_risk(deal, risk, narrative)
        narrative_time = time.perf_counter() - t_narr
        ratings.append((listing, deal, risk, narrative))

        timing["total_listing"].append(time.perf_counter() - t_total)
        timing["deal_bins"].append(deal_time)
        timing["carfax"].append(carfax_time)
        timing["risk"].append(risk_time)
        timing["narrative"].append(narrative_time)

    print("\n=== Level 2 Timing Summary ===")
    print(report_stats("Total listing runtime", timing["total_listing"]))
    print(report_stats("Deal-bin calculations", timing["deal_bins"]))
    print(report_stats("Carfax parsing", timing["carfax"]))
    print(report_stats("Risk scoring", timing["risk"]))
    print(report_stats("Narrative generation", timing["narrative"]))
    print("================================\n")

    await render_level2_pdf(
        make, model, len(listings), len(valid_listings), ratings, metadata
    )


if __name__ == "__main__":
    json_files = glob.glob(os.path.join("output/raw", "*.json"))
    latest_json_file = max(json_files, key=os.path.getmtime)
    data: dict = {}
    with open(latest_json_file, "r") as file:
        data = json.load(file)
    metadata = data.get("metadata", {})
    listings = data.get("listings", {})
    if metadata and listings:
        print(f"Loading {latest_json_file} - {len(listings)} found")
        asyncio.run(start_level2_analysis(metadata, listings, latest_json_file))

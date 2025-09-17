import sys

from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from playwright.async_api import async_playwright

from analysis.models import CarListing, DealBin, TrimValuation


def to_level1_json(
    make: str,
    model: str,
    sort: str,
    deal_bins: list[DealBin],
    crosstab: dict,
    skipped_listings: list,
) -> dict:

    all_listing_count = sum(b.count for b in deal_bins)
    if all_listing_count == 0:
        print(
            f"🚨 Unable to generate report: 0 listings have been ranked. {len(skipped_listings)} listings have been skipped."
        )
        sys.exit(0)
    gg_count = sum(b.count for b in deal_bins if b.category in ("Great", "Good"))
    f_count = sum(b.count for b in deal_bins if b.category == "Fair")
    pb_count = sum(b.count for b in deal_bins if b.category in ("Poor", "Bad"))

    return {
        "make": make,
        "model": model,
        "sort": sort,
        "deal_bins": [b.to_dict() for b in deal_bins],
        "deal_condition_matrix": crosstab,  # {bin:{condition:count}}
        "good_great_count": gg_count,
        "good_great_pct": gg_count / all_listing_count * 100,
        "fair_count": f_count,
        "fair_pct": f_count / all_listing_count * 100,
        "poor_bad_count": pb_count,
        "poor_bad_pct": pb_count / all_listing_count * 100,
        "skipped_listings": [l for l in skipped_listings],
        "skipped_count": len(skipped_listings),
    }


def create_report_parameter_summary(metadata: dict) -> str:
    """
    Creates a summary header for the level 1 analysis report that briefly goes over which parameters that were used in the search.
    This include condition, price filters, mileage filters, and the sort method
    """

    summary = "This report reflects{condition_summary}listings retrieved using the {sort_method} sort option"
    condition_summary = " "
    price_summary = ""
    miles_summary = ""
    filters = metadata["filters"]
    sort_method = filters["sort"]  # this will always exist
    condition: list[str] = filters.get("condition")
    min_price: int = filters.get("min_price")
    max_price: int = filters.get("max_price")
    min_miles: int = filters.get("min_miles")
    max_miles: int = filters.get("max_miles")

    if condition:
        if len(condition) == 1:
            condition_summary = f" {condition[0]} "
        elif len(condition) == 2:
            sort_cond = sorted(condition)
            condition_summary = f" {sort_cond[0]} and {sort_cond[1]} "
        else:
            condition_summary = " New, Used, and Certified "

    # Add clause for detecting filters
    if min_miles or max_miles or min_price or max_price:
        summary += ", filtered to vehicles "
    else:
        summary += " with no additional price or mileage filters applied."

    if min_price or max_price:
        if min_price and max_price:
            price_summary = f"priced between ${min_price:,} and ${max_price:,}"
        elif min_price:
            price_summary = f"priced over ${min_price:,}"
        elif max_price:
            price_summary = f"priced below ${max_price:,}"

    if min_miles or max_miles:
        if min_miles and max_miles:
            miles_summary = f"with between {min_miles:,} and {max_miles:,} miles"
        elif min_miles:
            miles_summary = f"with more than {min_miles:,} miles"
        elif max_miles:
            miles_summary = f"with fewer than {max_miles:, } miles"

    if price_summary and miles_summary:
        summary += price_summary + " and " + miles_summary + "."
    elif price_summary:
        summary += price_summary + "."
    elif miles_summary:
        summary += miles_summary + "."

    return summary.format(condition_summary=condition_summary, sort_method=sort_method)


async def render_pdf(
    make: str,
    model: str,
    cache_entries: dict[str, TrimValuation],
    all_listings: list[CarListing],
    trim_valuations: list[TrimValuation],
    deal_bins: list[DealBin],
    great_bin: DealBin,
    good_bin: DealBin,
    fair_bin: DealBin,
    poor_bin: DealBin,
    bad_bin: DealBin,
    no_price_bin: DealBin,
    analysis_json: dict,
    outliers_json: dict,
    crosstab: dict,
    metadata: dict,
    out_file=None,
):
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("level1.html")

    report_title = f"{make} {model} Market Overview — Level 1"
    generated_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

    summary = create_report_parameter_summary(metadata)

    html_out = template.render(
        report_title=report_title,
        generated_at=generated_at,
        summary=summary,
        cache_entries=cache_entries,
        all_listings=all_listings,
        trim_valuations=[e.to_dict() for e in trim_valuations],
        deal_bins=deal_bins,
        great_bin=great_bin,
        good_bin=good_bin,
        fair_bin=fair_bin,
        poor_bin=poor_bin,
        bad_bin=bad_bin,
        no_price_bin=no_price_bin,
        analysis=analysis_json,
        outliers=outliers_json,
        deal_condition_matrix=crosstab,
    )

    # Default save location
    if out_file is None:
        out_dir = Path("output") / "level1"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{make}_{model}_level1_analysis_report.pdf".replace(
            " ", "_"
        )

    # Render PDF with Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html_out, wait_until="load")
        await page.pdf(path=str(out_file), format="A4", print_background=True)
        await browser.close()

    print(f"✅ PDF created at: {out_file.resolve()}")

from __future__ import annotations

import json, re, time

from datetime import datetime, timedelta
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from playwright.async_api import async_playwright

from analysis.models import CarListing, DealBin, TrimValuation
from visor_scraper.utils import make_string_url_safe

CACHE_FILE = Path("output") / "level1_fmv_cache.json"
CACHE_TTL = timedelta(days=7)


async def render_pdf(
    make,
    model,
    cache_entries,
    trim_valuations: list[TrimValuation],
    great_bin: DealBin,
    good_bin: DealBin,
    fair_bin: DealBin,
    poor_bin: DealBin,
    bad_bin: DealBin,
    no_price_bin: DealBin,
    out_file=None,
):
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("level1.html")

    report_title = f"Level 1 Market Analysis Report – {make} {model}"  # utils.format_years(metadata["years"])
    generated_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

    # Build embedded JSON object
    embedded_data = {
        "make": make,
        "model": model,
        "generated_at": generated_at,
        "entries": cache_entries,
        "bins": {
            "great": great_bin.to_dict(),
            "good": good_bin.to_dict(),
            "fair": fair_bin.to_dict(),
            "poor": poor_bin.to_dict(),
            "bad": bad_bin.to_dict(),
            "no_price": no_price_bin.to_dict(),
        },
    }

    html_out = template.render(
        report_title=report_title,
        cache_entries=cache_entries,
        trim_valuations=[e.to_dict() for e in trim_valuations],
        great_bin=great_bin.to_dict(),
        good_bin=good_bin.to_dict(),
        fair_bin=fair_bin.to_dict(),
        poor_bin=poor_bin.to_dict(),
        bad_bin=bad_bin.to_dict(),
        no_price_bin=no_price_bin.to_dict(),
        embedded_data=embedded_data,
    )

    # Default save location
    if out_file is None:
        out_dir = Path("output") / "level1"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "level1_analysis_report.pdf"

    # Render PDF with Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html_out, wait_until="load")
        await page.pdf(path=str(out_file), format="A4", print_background=True)
        await browser.close()

    print(f"✅ PDF created at: {out_file.resolve()}")


def load_latest_level1():
    out_dir = Path("output") / "level1"
    # Find all files that look like level1_<make>_<model>_<timestamp>.json
    files = sorted(
        out_dir.glob("level1_*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not files:
        raise FileNotFoundError("No level1 JSON files found.")
    latest = files[0]
    with latest.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data["make"], data["model"], data["listings"]


# region Cache Logic


def load_cache():
    if CACHE_FILE.exists():
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {"entries": {}}


def save_cache(cache):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def is_fresh(entry):
    ts = datetime.fromisoformat(entry["timestamp"])
    return datetime.now() - ts < CACHE_TTL


def prepare_cache():
    cache = load_cache()
    slugs = cache.setdefault("model_slugs", {})
    trim_options = cache.setdefault("trim_options", {})
    cache_entries = cache.setdefault("entries", {})
    return cache, slugs, trim_options, cache_entries


def cache_covers_all(
    make: str, model: str, years: list[str], trim_map: dict, cache: dict
) -> bool:
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

    # Check FMVs for every visor_trim
    for year, trims in trim_map.items():
        for trim in trims.keys():
            visor_trim = f"{year} {make} {model} {trim}"
            if visor_trim not in cache_entries or not is_fresh(
                cache_entries[visor_trim]
            ):
                return False

    return True


# endregion

# region KBB model/trim workflow


async def get_model_slug_from_vin(page, vin: str) -> str:
    await page.goto("https://www.kbb.com/whats-my-car-worth")

    # Ensure VIN mode is selected
    await page.locator("input#vinButton").check()

    # Enter VIN
    await page.fill('input[data-lean-auto="vinInput"]', vin)
    await page.wait_for_timeout(500)
    await page.locator('button[data-lean-auto="vinSubmitBtn"]').click(force=True)

    # Wait for redirect
    await page.wait_for_url("**/vin/**", timeout=10000)

    # Extract canonical URL
    vin_url = page.url

    # Parse out the slug portion
    parts = vin_url.split("/")
    model_slug = parts[4]
    return make_string_url_safe(model_slug)


def build_unique_trim_map(
    quicklist: list[str], make: str, model: str
) -> dict[str, dict[str, list[str]]]:
    trim_map: dict[str, dict[str, list[str]]] = {}
    for ymmt in quicklist:
        # Replace the make and model in case they use multiple words multiple words (Aston Marton, Crown Victoria)
        year_trim = ymmt.replace(make, "").replace(model, "")
        # The year will always be the firt four digits
        year = year_trim[:4]
        if year not in trim_map:
            trim_map[year] = {}

        # Clean up extra spaces
        trim = year_trim.replace(year, "").strip()
        if trim not in trim_map[year]:
            trim_map[year][trim] = []

    return trim_map


async def get_trim_options_for_year(
    page, make, model_slug, year, trim_map, trim_options, make_model_key
):
    if make_model_key in trim_options and year in trim_options[make_model_key]:
        year_trims = trim_options[make_model_key][year]
        for kbb_trim in year_trims:
            for key in sorted(trim_map[year].keys(), key=len, reverse=True):
                if kbb_trim.startswith(key):
                    trim_map[year][key].append(kbb_trim)
                    break
        return

    url = f"https://kbb.com/{make_string_url_safe(make)}/{model_slug}/{year}/styles/?intent=trade-in-sell&mileage=1"
    await page.goto(url)
    raw = await page.inner_text("script#__NEXT_DATA__")
    data = json.loads(raw)
    apollo = data["props"]["apolloState"]["_INITIAL_QUERY"]
    key = next(k for k in apollo if k.startswith("stylesPageQuery"))
    body_styles = apollo[key]["result"]["ymm"]["bodyStyles"]

    keys = sorted(trim_map[year].keys(), key=len, reverse=True)
    year_trims = []
    for bs in body_styles:
        for t in bs["trims"]:
            kbb_trim = t["name"]
            year_trims.append(kbb_trim)
            for key in keys:
                if kbb_trim.startswith(key):
                    trim_map[year][key].append(kbb_trim)
                    break

    trim_options.setdefault(make_model_key, {})[year] = year_trims


async def get_or_fetch_fmv(
    page,
    year: str,
    make: str,
    model: str,
    model_slug: str,
    trim: str,
    style: str,
    cache_entries,
):
    visor_trim = f"{year} {make} {model} {trim}"
    kbb_trim = f"{year} {make} {model} {style}"

    # Check cache first
    if visor_trim in cache_entries and is_fresh(cache_entries[visor_trim]):
        cached = cache_entries[visor_trim]
        return TrimValuation(
            visor_trim=visor_trim,
            kbb_trim=kbb_trim,
            fmv=cached["fmv"],
            source=cached["source"],
        )

    fmv_url = f"https://kbb.com/{make_string_url_safe(make)}/{model_slug}/{year}/{make_string_url_safe(style)}/"
    await page.goto(fmv_url)
    div_text = await page.inner_text("div.css-fbyg3h")

    match = re.search(r"current resale value of \$([\d,]+)", div_text)
    if match:
        resale_value = int(match.group(1).replace(",", ""))
        car_entry = TrimValuation(
            visor_trim=visor_trim,
            kbb_trim=kbb_trim,
            fmv=resale_value,
            source=fmv_url,
        )

        # Save into cache
        cache_entries[visor_trim] = {
            "kbb_trim": kbb_trim,
            "fmv": resale_value,
            "source": fmv_url,
            "timestamp": datetime.now().isoformat(),
        }

        return car_entry


def get_trim_valuations_from_cache(
    make, model, trim_map, cache_entries
) -> list[TrimValuation]:
    trim_valuations = []
    for year, trims in trim_map.items():
        for trim in trims.keys():
            visor_trim = f"{year} {make} {model} {trim}"
            cached = cache_entries[visor_trim]
            trim_valuations.append(
                TrimValuation(
                    visor_trim=visor_trim,
                    kbb_trim=cached["kbb_trim"],
                    fmv=cached["fmv"],
                    source=cached["source"],
                )
            )
    return trim_valuations


async def get_trim_valuations_from_scrape(
    make, model, years, vin, trim_map, slugs, trim_options, cache_entries, cache
) -> list[TrimValuation]:
    trim_valuations = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
            ],
        )
        context = await browser.new_context()
        page = await context.new_page()

        try:
            make_model_key = f"{make} {model}"
            model_slug = slugs.get(make_model_key)
            if not model_slug:
                model_slug = await get_model_slug_from_vin(page, vin)
                slugs[make_model_key] = model_slug

            # Fill trim_map with styles
            for year in years:
                await get_trim_options_for_year(
                    page, make, model_slug, year, trim_map, trim_options, make_model_key
                )

            # Fetch FMVs
            for year, trims in trim_map.items():
                for trim, styles in trims.items():
                    for style in styles:
                        entry = await get_or_fetch_fmv(
                            page,
                            year,
                            make,
                            model,
                            model_slug,
                            trim,
                            style,
                            cache_entries,
                        )
                        if entry:
                            trim_valuations.append(entry)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
            save_cache(cache)

    return trim_valuations


# endregion

# region Scoring / Binning


def rate_uncertainty(listing) -> str:
    report_present = listing["report_present"]
    window_sticker_present = listing["window_sticker_present"]
    warranty_info_present = listing["warranty_info_present"]

    if not report_present and not window_sticker_present and not warranty_info_present:
        return "High"
    elif not report_present and window_sticker_present and warranty_info_present:
        return "Some"
    else:
        return "Low"


def rate_deal(price, delta, fmv) -> str:
    if price == 0:
        return "No price"

    if delta <= -2000 or price <= fmv * 0.93:
        return "Great"
    elif (-2000 < delta <= -1000) or (fmv * 0.93 < price <= fmv * 0.97):
        return "Good"
    elif (-999 <= delta <= 999) or (fmv * 0.97 < price < fmv * 1.03):
        return "Fair"
    elif (2000 > delta >= 1000) or (fmv * 1.03 <= price < fmv * 1.07):
        return "Poor"
    else:
        return "Bad"


def rate_risk(listing, price, fmv) -> str:
    year = int(listing["title"][:4])
    avg_miles_per_day = 13500 / 365
    est_days_since_manufacture = (datetime.now() - datetime(year, 1, 1)).days
    expected_miles = est_days_since_manufacture * avg_miles_per_day
    mileage = int(listing["mileage"])
    if price == 0:
        return "Unknown"
    if (mileage >= expected_miles * 1.35) or (
        mileage >= expected_miles * 1.2 and price >= fmv * 1.1
    ):
        return "High"
    elif (mileage >= expected_miles * 1.2) or (price >= fmv * 1.1):
        return "Some"
    else:
        return "Low"


# endregion


def _bool_from_url(val: str | None) -> bool:
    """True iff a usable URL string appears present (not 'Unavailable'/empty/None)."""
    if not val:
        return False
    s = str(val).strip().lower()
    return s not in {"", "unavailable", "n/a", "none", "null"}


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


def build_quicklist(slimmed: list[dict]) -> list[str]:
    def _year_key(t: str) -> int:
        m = re.match(r"^\s*(\d{4})\b", t)
        return int(m.group(1)) if m else 9999

    titles = [str(l.get("title", "")) for l in slimmed if l.get("title")]
    unique = sorted(set(titles), key=lambda t: (_year_key(t), t.lower()))
    return unique


def _slim(listing: dict) -> dict:
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
    carfax_present = _bool_from_url(addl.get("carfax_url"))
    autocheck_present = _bool_from_url(addl.get("autocheck_url"))
    sticker_present = _bool_from_url(addl.get("window_sticker_url"))

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
        "price": _to_int(listing.get("price")),
        "mileage": _to_int(listing.get("mileage")),
        "days_on_market_delta": _days_on_market(listing),
        "price_history_lowest": _price_history_lowest(listing.get("price_history")),
        "report_present": carfax_present or autocheck_present,
        "window_sticker_present": sticker_present,
        "warranty_info_present": warranty_present,
    }


async def create_level1_file(metadata: dict):
    cache, slugs, trim_options, cache_entries = prepare_cache()
    make, model, listings = load_latest_level1()
    vin = listings[0]["vin"]
    quicklist = build_quicklist(listings)
    years = extract_years(quicklist)
    trim_map = build_unique_trim_map(quicklist, make, model)

    trim_valuations: list[TrimValuation]
    if cache_covers_all(make, model, years, trim_map, cache):
        trim_valuations = get_trim_valuations_from_cache(
            make, model, trim_map, cache_entries
        )
    else:
        trim_valuations = await get_trim_valuations_from_scrape(
            make, model, years, vin, trim_map, slugs, trim_options, cache_entries, cache
        )

    great_bin = DealBin(category="Great", listings=[], count=0)
    good_bin = DealBin(category="Good", listings=[], count=0)
    fair_bin = DealBin(category="Fair", listings=[], count=0)
    poor_bin = DealBin(category="Poor", listings=[], count=0)
    bad_bin = DealBin(category="Bad", listings=[], count=0)
    no_price_bin = DealBin(category="No Price", listings=[], count=0)

    for listing in listings:
        fmv = cache_entries[listing["title"]]["fmv"]
        if listing["price"] is not None:
            price = listing["price"]
            delta = price - fmv
        else:
            price = 0
            delta = 0

        uncertainty = rate_uncertainty(listing)
        risk = rate_risk(listing, price, fmv)

        car_listing = CarListing(
            id=listing["id"],
            vin=listing["vin"],
            title=listing["title"],
            miles=listing["mileage"],
            price=price,
            price_delta=delta,
            uncertainty=uncertainty,
            risk=risk,
        )

        deal = rate_deal(price, delta, fmv)
        if deal == "Great":
            great_bin.listings.append(car_listing)
            great_bin.count += 1
        elif deal == "Good":
            good_bin.listings.append(car_listing)
            good_bin.count += 1
        elif deal == "Fair":
            fair_bin.listings.append(car_listing)
            fair_bin.count += 1
        elif deal == "Poor":
            poor_bin.listings.append(car_listing)
            poor_bin.count += 1
        elif deal == "Bad":
            bad_bin.listings.append(car_listing)
            bad_bin.count += 1
        else:
            no_price_bin.listings.append(car_listing)
            no_price_bin.count += 1

    await render_pdf(
        make,
        model,
        cache_entries,
        trim_valuations,
        great_bin,
        good_bin,
        fair_bin,
        poor_bin,
        bad_bin,
        no_price_bin,
    )


async def start_level1_analysis(
    listings: list[dict], metadata: dict, args, timestamp: str
) -> Path:
    """
    Builds 'level1_input_<Make>_<Model>_<Timestamp>.jsonc' next to your outputs.
    Returns the file path.
    Call this AFTER you've saved listings.json and closed the browser.
    """
    if not listings:
        raise ValueError("No listings provided to create_level1_file().")

    # Derive make/model from args when available; else from first listing/title.
    make = getattr(args, "make", None) or listings[0].get("make") or "Unknown"
    model = getattr(args, "model", None) or listings[0].get("model") or "Unknown"

    # Slim all listings
    slimmed = [_slim(l) for l in listings if l is not None]
    payload = {"make": make, "model": model, "listings": slimmed}

    # Output pathing
    out_dir = Path("output") / "level1"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"level1_{make}_{model}_{timestamp}.json"

    # Write header (comments) + JSON data
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    await create_level1_file(metadata)

    return out_path

import json, re, sys

from collections import defaultdict
from datetime import datetime
from playwright.async_api import async_playwright, Page, TimeoutError

from analysis.cache import is_fmv_fresh, is_pricing_fresh, save_cache
from analysis.models import TrimValuation
from analysis.normalization import best_kbb_match, normalize_trim
from analysis.utils import get_relevant_entries, to_int
from visor_scraper.utils import make_string_url_safe


async def get_model_slug_from_vins(page, vins: list[str]) -> str:
    await page.goto("https://www.kbb.com/whats-my-car-worth")

    # Ensure VIN mode is selected
    await page.locator("input#vinButton").check()

    # Enter VIN
    for vin in vins:
        try:
            await page.fill('input[data-lean-auto="vinInput"]', vin)
            await page.wait_for_timeout(500)
            await page.locator('button[data-lean-auto="vinSubmitBtn"]').click(
                force=True
            )

            # Wait for redirect
            await page.wait_for_url("**/vin/**", timeout=5000)

            # Extract canonical URL
            vin_url = page.url

            # Parse out the slug portion
            parts = vin_url.split("/")
            model_slug = parts[4]
            return make_string_url_safe(model_slug)
        except TimeoutError:
            print(f"Could not model slug from VIN: {vin}")

    print(
        "No models could be mapped from the provided listings. This vehicle's information does not appear on KBB."
    )
    sys.exit(1)


async def get_trim_options_for_year(
    page,
    make: str,
    model_slug: str,
    year: str,
    trim_options: dict[str, dict[str, list[str]]],
    make_model_key: str,
) -> None:
    """
    Fetch available trims for a given year directly from KBB.
    Returns a list of raw KBB trim names (no visor mapping).
    """
    # If we already have cached trims for this year, we don't need another lookup
    if make_model_key in trim_options and year in trim_options[make_model_key]:
        return

    url = f"https://kbb.com/{make_string_url_safe(make)}/{model_slug}/{year}/styles/?intent=trade-in-sell&mileage=1"
    await page.goto(url)
    raw = await page.inner_text("script#__NEXT_DATA__")
    data = json.loads(raw)
    apollo = data.get("props", {}).get("apolloState", {})

    styles = find_styles_data(apollo)
    if not styles:
        print(f"⚠️  No styles query found for {make_model_key} {year}")
        trim_options.setdefault(make_model_key, {})[year] = []
        return

    body_styles = styles["result"]["ymm"]["bodyStyles"]

    # Collect raw KBB trims (e.g., "Premium Sport Utility 4D")
    year_trims = []
    for bs in body_styles:
        for t in bs["trims"]:
            kbb_trim = t["name"].strip()
            if kbb_trim not in year_trims:
                year_trims.append(kbb_trim)

    trim_options.setdefault(make_model_key, {})[year] = year_trims


async def get_or_fetch_new_pricing_for_year(
    page: Page,
    make: str,
    model: str,
    model_slug: str,
    year: str,
    cache_entries: dict,
    expected_trims: set[str],
) -> None:
    if expected_trims:
        # Check cache before hitting the page
        relevant_entries = get_relevant_entries(cache_entries, make, model, year)

        expected_norm = {normalize_trim(t) for t in expected_trims}
        have_norm = {
            normalize_trim(
                v.get("kbb_trim", "").split(f"{year} {make} {model}", 1)[-1].strip()
            )
            for v in relevant_entries.values()
            if "kbb_trim" in v
        }

        all_fresh = expected_norm.issubset(have_norm) and all(
            is_pricing_fresh(e) for e in relevant_entries.values()
        )

        if all_fresh:
            print(
                f"Cache for {year} {make} {model} is complete and fresh, skipping fetch"
            )
            return

    url = f"https://kbb.com/{make_string_url_safe(make)}/{model_slug}/{year}"
    await page.goto(url)
    await page.wait_for_selector("table.css-lb65co tbody tr >> nth=0", timeout=5000)
    rows = await page.query_selector_all("table.css-lb65co tbody tr")

    # Collect the pricing data before attempting to get FMV, otherwise page context gets overwritten and Playwright will throw an error
    pricing_data = []
    for row in rows:
        divs = await row.query_selector_all("div")
        if len(divs) < 3:
            continue

        table_trim = (await divs[0].inner_text()).strip()
        msrp = (await divs[1].inner_text()).strip()
        fpp = (await divs[2].inner_text()).strip()
        pricing_data.append((table_trim, msrp, fpp))

    for table_trim, msrp, fpp in pricing_data:
        prefix = f"{year} {make} {model}"
        kbb_trim = f"{prefix} {table_trim}"

        fmv = None
        fmv_source = None
        timestamp = ""

        if expected_trims:
            # try to match against expected trims
            norm_table = normalize_trim(table_trim)
            norm_expected = {t: normalize_trim(t) for t in expected_trims}

            match_trim = next(
                (t for t, norm in norm_expected.items() if norm == norm_table), None
            )

            if not match_trim:
                match_trim = best_kbb_match(norm_table, list(norm_expected.values()))
                if match_trim:
                    for orig, norm in norm_expected.items():
                        if norm.lower() == match_trim.lower():
                            match_trim = orig
                            break

            if not match_trim:
                print(
                    f"⚠️ Could not map pricing trim '{table_trim}' to any expected trim"
                )
                continue

            kbb_trim_option = f"{prefix} {match_trim}"

            # ✅ only here do we call FMV
            fmv, fmv_source, timestamp = await get_or_fetch_fmv(
                page, year, make, model_slug, match_trim, kbb_trim_option, cache_entries
            )

        else:
            # no expected trims → just use pricing table trim as key
            print(f"ℹ️ No FMV data for {kbb_trim}; saving MSRP/FPP only")
            kbb_trim_option = kbb_trim

        entry = cache_entries.setdefault(kbb_trim_option, {})

        fpp_val = None
        if fpp and fpp.upper() != "TBD":
            fpp_val = to_int(fpp)
        msrp_val = to_int(msrp)

        entry["kbb_trim"] = kbb_trim
        entry["fmv"] = fmv
        entry["fmv_source"] = fmv_source
        entry["timestamp"] = timestamp
        entry["msrp"] = msrp_val
        entry["msrp_source"] = url
        entry["fpp"] = fpp_val
        entry["fpp_source"] = url

        if fpp_val is None:
            entry["skip_reason"] = f"There is currently no pricing data for this trim."

        entry["pricing_timestamp"] = datetime.now().isoformat()


async def get_or_fetch_fmv(
    page: Page,
    year: str,
    make: str,
    model_slug: str,
    style: str,
    kbb_trim: str,
    cache_entries: dict[str, dict],
):
    entry = cache_entries.setdefault(kbb_trim, {})

    # Check cache first
    if is_fmv_fresh(entry):
        return entry.get("fmv"), entry.get("fmv_source"), entry.get("timestamp")

    fmv_url = f"https://kbb.com/{make_string_url_safe(make)}/{model_slug}/{year}/{make_string_url_safe(style)}/"
    await page.goto(fmv_url)
    try:
        div_text = await page.inner_text("div.css-fbyg3h", timeout=10000)
    except TimeoutError as t:
        print("Timeout: ", fmv_url)
        print(t.message)
        return None, None, ""

    match = re.search(r"current resale value of \$([\d,]+)", div_text)
    if match:
        resale_value = int(match.group(1).replace(",", ""))
        return resale_value, fmv_url, datetime.now().isoformat()
    else:
        # ✅ fallback when FMV is missing
        return None, None, ""


def get_trim_valuations_from_cache(make, model, cache_entries) -> list[TrimValuation]:
    trim_valuations = []
    for cached in get_relevant_entries(cache_entries, make, model).values():
        # ensure keys always exist
        cached.setdefault("fmv", None)
        cached.setdefault("fmv_source", None)
        cached.setdefault("msrp", None)
        cached.setdefault("msrp_source", None)
        cached.setdefault("fpp", None)
        cached.setdefault("fpp_source", None)

        trim_valuations.append(TrimValuation.from_dict(cached))
    return trim_valuations


async def get_trim_valuations_from_scrape(
    make: str,
    model: str,
    years: list[str],
    vins: list[str],
    slugs: dict[str, str],
    trim_options: dict[str, dict[str, list[str]]],
    cache_entries: dict[str, dict],
    cache: dict,
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
                model_slug = await get_model_slug_from_vins(page, vins)
                slugs[make_model_key] = model_slug

            # Fill trim_map with styles
            for year in years:
                await get_trim_options_for_year(
                    page,
                    make,
                    model_slug,
                    year,
                    trim_options,
                    make_model_key,
                )
                expected_trims = set(trim_options.get(make_model_key, {}).get(year, []))
                await get_or_fetch_new_pricing_for_year(
                    page, make, model, model_slug, year, cache_entries, expected_trims
                )
        finally:
            try:
                await browser.close()
            except Exception:
                pass
            save_cache(cache)
    for entry in get_relevant_entries(cache_entries, make, model).values():
        trim_valuations.append(TrimValuation.from_dict(entry))

    return trim_valuations


def find_styles_data(apollo: dict) -> dict | None:
    """
    Recursively search for a value containing 'result.ymm.bodyStyles'.
    Returns the full object if found, else None.
    """
    if isinstance(apollo, dict):
        for k, v in apollo.items():
            if isinstance(k, str) and (
                k.startswith("stylesPageQuery") or k.startswith("stylesQuery")
            ):
                return v  # return the nested value, not the key
            found = find_styles_data(v)
            if found:
                return found
    elif isinstance(apollo, list):
        for item in apollo:
            found = find_styles_data(item)
            if found:
                return found
    return None

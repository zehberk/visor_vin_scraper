import json, re

from datetime import datetime
from playwright.async_api import (
    APIRequestContext,
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError,
)

from analysis.cache import (
    cache_covers_all,
    get_relevant_entries,
    get_trim_valuations_from_cache,
    is_entry_fresh,
    is_natl_fresh,
    save_cache,
)
from analysis.normalization import best_kbb_trim_match, get_variant_map
from analysis.utils import extract_years, to_int
from utils.common import make_string_url_safe
from utils.constants import *
from utils.models import TrimValuation


async def get_model_slug_map(
    page: Page,
    request: APIRequestContext,
    slugs: dict[str, str],
    slimmed: list[dict],
    make: str,
    model: str,
) -> dict[str, str]:
    relevant_slugs: dict[str, str] = {}

    # Sort listings by mileage first to hopefully get valid VINs first (KBB may not have info for newer vehicles)
    def _sort_key(listing: dict):
        condition_rank = 0 if listing["condition"].lower() == "used" else 1
        return (condition_rank, listing["mileage"])

    def _get_vins(listings: list) -> list[str]:
        return [listing["vin"] for listing in listings[: min(10, len(listings))]]

    variant_map = await get_variant_map(make, model, slimmed)

    for model_key, listings in variant_map.items():
        if slugs and model_key in slugs:
            relevant_slugs[model_key] = slugs[model_key]
            continue
        year = model_key[:4]
        safe_make = make_string_url_safe(make)
        model = model_key.replace(year, "").replace(make, "").strip()
        model_slug = make_string_url_safe(model)

        url = KBB_LOOKUP_BASE_URL.format(make=safe_make, model=model_slug, year=year)
        try:
            resp = await request.get(url, max_redirects=0)
            if resp.status in [200, 301]:
                slugs[model_key] = model_slug
                relevant_slugs[model_key] = model_slug
            else:
                vin_slug = await get_model_slug_from_vins(
                    page, model_key, _get_vins(sorted(listings, key=_sort_key))
                )
                if vin_slug:
                    slugs[model_key] = vin_slug
                    relevant_slugs[model_key] = vin_slug
        except TimeoutError as t:
            print("Timed out: ", t)
        except Exception as e:
            print("Error: ", e)

    await request.dispose()

    return relevant_slugs


async def get_model_slug_from_vins(page: Page, model_key: str, vins: list[str]) -> str:
    await page.goto(KBB_WHATS_MY_CAR_WORTH_URL)

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
        except TimeoutError as t:
            # print(f"Could not model slug from VINs: {model_key}", t)
            mesg = f"Could not model slug from VINs: {model_key}"

    print(
        f"No models could be mapped for {model_key}. This vehicle's information does not appear on KBB."
    )
    return ""


async def get_trim_options_for_year(
    page: Page,
    make: str,
    model_slug: str,
    year: str,
    trim_options: dict[str, dict[str, list[str]]],
    make_model_key: str,
) -> list[str]:
    """
    Fetch available trims for a given year directly from KBB.
    Returns a list of raw KBB trim names (no visor mapping).
    """
    # If we already have cached trims for this year, we don't need another lookup
    if make_model_key in trim_options and year in trim_options[make_model_key]:
        return trim_options[make_model_key][year]
    safe_make = make_string_url_safe(make)

    url = KBB_LOOKUP_STYLES_URL.format(make=safe_make, model=model_slug, year=year)
    await page.goto(url)
    raw = await page.inner_text("script#__NEXT_DATA__")
    data = json.loads(raw)
    apollo = data.get("props", {}).get("apolloState", {})

    styles = find_styles_data(apollo)
    if not styles:
        trim_options.setdefault(make_model_key, {})[year] = []
        return []

    body_styles = styles["result"]["ymm"]["bodyStyles"]

    # Collect raw KBB trims (e.g., "Premium Sport Utility 4D")
    year_trims = []
    for bs in body_styles:
        for t in bs["trims"]:
            kbb_trim = t["name"].strip()
            if kbb_trim not in year_trims:
                year_trims.append(kbb_trim)

    trim_options.setdefault(make_model_key, {})[year] = year_trims
    return year_trims


async def get_or_fetch_national_pricing(
    page: Page,
    make: str,
    model: str,
    model_slug: str,
    year: str,
    cache_entries: dict,
    expected_trims: list[str],
) -> list[tuple[str, str, str, str, str]]:
    pricing_data = []
    relevant_entries = get_relevant_entries(cache_entries, make, model, year)

    all_fresh = bool(relevant_entries) and all(
        is_natl_fresh(e) for e in relevant_entries.values()
    )
    if expected_trims:
        all_fresh = all_fresh and all(
            f"{year} {make} {model} {t}" in relevant_entries for t in expected_trims
        )

    if all_fresh:
        for e in relevant_entries.values():
            pricing_data.append(
                (
                    e["kbb_trim"],
                    e["msrp"],
                    e["natl_fpp"],
                    e["natl_source"],
                    e["natl_timestamp"],
                )
            )
    else:
        safe_make = make_string_url_safe(make)
        url = KBB_LOOKUP_BASE_URL.format(make=safe_make, model=model_slug, year=year)
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(
                "table.css-lb65co tbody tr >> nth=0", timeout=5000
            )
            rows = await page.query_selector_all("table.css-lb65co tbody tr")
        except TimeoutError as t1:
            try:
                await page.wait_for_selector("div.css-127mtog table", timeout=5000)
                rows = await page.query_selector_all("div.css-127mtog table tbody tr")
            except TimeoutError as t2:
                print(f"Unable to find table for pricing: {url}")
                return pricing_data

        # Collect the pricing data before attempting to get FMV, otherwise page context gets
        # overwritten and Playwright will throw an error
        for row in rows:
            divs = await row.query_selector_all("div")
            if divs:
                if len(divs) < 3:
                    continue

                table_trim = (await divs[0].inner_text()).strip()
                msrp = (await divs[1].inner_text()).strip()
                natl_fpp = (await divs[2].inner_text()).strip()
                pricing_data.append(
                    (table_trim, msrp, natl_fpp, url, datetime.now().isoformat())
                )
            else:
                tds = await row.query_selector_all("td")
                if len(tds) < 2:
                    continue
                table_trim = (await tds[0].inner_text()).strip()
                msrp = (await tds[1].inner_text()).strip()
                natl_fpp = None
                pricing_data.append(
                    (table_trim, msrp, natl_fpp, url, datetime.now().isoformat())
                )

    return pricing_data


async def populate_pricing_for_year(
    page: Page,
    make: str,
    model: str,
    model_slug: str,
    year: str,
    cache_entries: dict,
    expected_trims: list[str],
) -> None:

    # Get MSRP/National FPP first, will return only entries that need an FMV
    natl_data = await get_or_fetch_national_pricing(
        page, make, model, model_slug, year, cache_entries, expected_trims
    )

    if not natl_data:
        return

    for table_trim, msrp, natl_fpp, natl_source, natl_ts in natl_data:
        prefix = f"{year} {make} {model}"
        # If the pricing data is from the cache, strip the prefix
        if prefix in table_trim:
            table_trim = table_trim.replace(prefix, "").strip()

        kbb_trim = f"{prefix} {table_trim}"

        fmr_low: int | None = None
        fmr_high: int | None = None
        fpp_local: int | None = None
        fmv: int | None = None
        local_source: str | None = None
        local_ts = datetime.now().isoformat()

        if expected_trims:
            match_trim = best_kbb_trim_match(table_trim, expected_trims)

            if not match_trim:
                print(
                    f"⚠️ Could not map pricing trim '{table_trim}' to any expected trim"
                )
                continue

            kbb_trim_option = f"{prefix} {match_trim}"

            # only here do we call FMV
            fmr_low, fmr_high, fpp_local, fmv, local_source = (
                await get_or_fetch_local_pricing(
                    page,
                    year,
                    make,
                    model_slug,
                    match_trim,
                    kbb_trim_option,
                    cache_entries,
                )
            )

        else:
            kbb_trim_option = kbb_trim
            if natl_fpp and natl_fpp != "TBD":
                print(f"ℹ️  No local data for {kbb_trim}; saving MSRP/National FPP only")
            else:
                print(f"ℹ️  No national pricing data for {kbb_trim}; saving MSRP only")

        entry = cache_entries.setdefault(kbb_trim_option, {})

        natl_val = None
        if natl_fpp and natl_fpp.upper() != "TBD":
            natl_val = to_int(natl_fpp)

        entry["model"] = model
        entry["kbb_trim"] = kbb_trim

        entry["msrp"] = to_int(msrp)
        entry["fpp_natl"] = natl_val

        entry["fmr_low"] = fmr_low
        entry["fmr_high"] = fmr_high
        entry["fpp_local"] = fpp_local
        entry["fmv"] = fmv
        entry["natl_source"] = natl_source
        entry["local_source"] = local_source

        if natl_val is None:
            entry["skip_reason"] = f"There is currently no pricing data for this trim."

        entry["natl_timestamp"] = natl_ts
        entry["local_timestamp"] = local_ts


async def get_or_fetch_local_pricing(
    page: Page,
    year: str,
    make: str,
    model_slug: str,
    trim: str,
    kbb_trim: str,
    cache_entries: dict[str, dict],
):
    entry = cache_entries.setdefault(kbb_trim, {})

    # Check cache first
    if is_entry_fresh(entry):
        return (
            entry.get("fmr_low"),
            entry.get("fmr_high"),
            entry.get("fpp_local"),
            entry.get("fmv"),
            entry.get("local_source"),
        )

    safe_make = make_string_url_safe(make)
    safe_trim = make_string_url_safe(trim)
    local_url = KBB_LOOKUP_TRIM_URL.format(
        make=safe_make, model=model_slug, year=year, trim=safe_trim
    )

    fmr_low: int | None = None
    fmr_high: int | None = None
    fpp_local: int | None = None
    fmv: int | None = None
    depreciation_text: str = ""
    try:
        await page.goto(local_url, wait_until="domcontentloaded")

        fmr_low, fmr_high, fpp_local = await get_price_advisor_values(page)

        nav_tabs = await page.query_selector_all(
            "div.styled-nav-tabs.css-16wc4jq.empazup2 button"
        )

        depreciation_exists = False
        for button in nav_tabs:
            aria_label = await button.get_attribute("aria-label")
            if aria_label == "Depreciation":
                depreciation_exists = True
                break

        if depreciation_exists:
            depreciation_text = await page.inner_text("div.css-fbyg3h", timeout=10000)
    except TimeoutError as t:
        print("Timeout: ", local_url)
        print(t.message)

    match = re.search(r"current resale value of \$([\d,]+)", depreciation_text)
    if match:
        fmv = int(match.group(1).replace(",", ""))

    return fmr_low, fmr_high, fpp_local, fmv, local_url


async def get_price_advisor_values(
    page: Page,
) -> tuple[int | None, int | None, int | None]:
    """Loads the DOM of the internal object in order to retrieve the fair market range
    and local fair purchase price"""

    fmr_low: int | None = None
    fmr_high: int | None = None
    fpp_local: int | None = None
    price_values: list[str] = []

    try:
        await page.wait_for_selector("object#priceAdvisor")
        data_url = await page.get_attribute("object#priceAdvisor", "data")

        if data_url:
            # Now navigate directly to that URL to parse it
            svg_page = await page.context.new_page()
            await svg_page.goto(data_url)
            await svg_page.wait_for_selector("text")
            price_values = await svg_page.eval_on_selector_all(
                "g#RangeBox > text",
                """
                nodes => nodes
                    .map(n => n.textContent.trim())
                    .filter(t => t.includes('$'))
                """,
            )

            await svg_page.close()
    except TimeoutError as t:
        print("Timeout waiting for FMR an local FPP")
        print(t.message)

    if price_values:
        fmr_text, fpp_text = price_values
        low, high = fmr_text.split("-")
        fmr_low = to_int(low)
        fmr_high = to_int(high)
        fpp_local = to_int(fpp_text)

    return fmr_low, fmr_high, fpp_local


async def create_kbb_browser() -> (
    tuple[APIRequestContext, Browser, BrowserContext, Page]
):
    p = await async_playwright().start()
    request: APIRequestContext = await p.request.new_context()
    browser: Browser = await p.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
        ],
    )
    context: BrowserContext = await browser.new_context()
    await context.route(
        "**/*",
        lambda route: (
            route.abort()
            if route.request.resource_type in ["image", "media", "font"]
            else route.continue_()
        ),
    )
    page: Page = await context.new_page()
    return request, browser, context, page


async def get_trim_valuations_from_scrape(
    make: str,
    model: str,
    slugs: dict[str, str],
    listings: list[dict],
    trim_options: dict[str, dict[str, list[str]]],
    cache_entries: dict[str, dict],
    cache: dict,
) -> list[TrimValuation]:
    trim_valuations = []

    relevant_slugs: dict[str, str] = {}

    request, browser, context, page = await create_kbb_browser()

    try:
        relevant_slugs = await get_model_slug_map(
            page, request, slugs, listings, make, model
        )

        for ymm, slug in relevant_slugs.items():
            if slug:
                year = ymm[:4]
                make_model = ymm.replace(year, "").strip()
                model_name = make_model.replace(make, "").strip()
                options = await get_trim_options_for_year(
                    page, make, slug, year, trim_options, make_model
                )
                await populate_pricing_for_year(
                    page,
                    make,
                    model_name,
                    slug,
                    year,
                    cache_entries,
                    options,
                )

    finally:
        try:
            await page.close()
            await context.close()
            await browser.close()
        except Exception:
            pass
        save_cache(cache)

    for ymm in relevant_slugs.keys():
        year = ymm[:4]
        new_model = ymm.replace(year, "").replace(make, "").lower().strip()
        entries = get_relevant_entries(cache_entries, make, new_model, year)
        for entry in entries.values():
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


async def get_pricing_data(
    make: str, model: str, listings: list[dict], cache: dict
) -> list[TrimValuation]:
    cache_entries = cache.setdefault("entries", {})
    slugs = cache.setdefault("model_slugs", {})
    trim_options = cache.setdefault("trim_options", {})

    years = extract_years(listings)
    variant_map = await get_variant_map(make, model, listings)

    if cache_covers_all(make, list(variant_map.keys()), years, cache):
        return get_trim_valuations_from_cache(make, model, years, cache_entries)

    return await get_trim_valuations_from_scrape(
        make, model, slugs, listings, trim_options, cache_entries, cache
    )

import json, re, sys

from collections import defaultdict
from datetime import datetime
from playwright.async_api import APIRequestContext, async_playwright, Page, TimeoutError

from analysis.cache import is_fmv_fresh, is_pricing_fresh, save_cache
from analysis.models import TrimValuation
from analysis.normalization import best_kbb_match, normalize_trim
from analysis.utils import get_relevant_entries, to_int
from visor_scraper.utils import make_string_url_safe


def get_hybrid_map(sorted_mapping: dict[str, list[dict]]) -> dict[str, list[dict]]:
    map_with_hybrids: dict[str, list[dict]] = {}
    for ymm, listings in sorted_mapping.items():
        all_none = all(l["is_hybrid"] is None for l in listings)
        all_hybrid = all(
            l["is_hybrid"] is True or l["is_hybrid"] is None for l in listings
        )
        all_non_hybrid = all(
            l["is_hybrid"] is False or l["is_hybrid"] is None for l in listings
        )
        if all_none:
            map_with_hybrids.setdefault(ymm, []).extend(listings)
        elif not all_hybrid and not all_non_hybrid:
            map_with_hybrids.setdefault(ymm, []).extend(
                [l for l in listings if l["is_hybrid"] is False]
            )
            map_with_hybrids.setdefault(f"{ymm} Hybrid", []).extend(
                [l for l in listings if l["is_hybrid"] is True]
            )
        elif all_non_hybrid:
            map_with_hybrids.setdefault(ymm, []).extend(listings)
        elif all_hybrid:
            map_with_hybrids.setdefault(ymm, []).extend(listings)
            map_with_hybrids.setdefault(f"{ymm} Hybrid", []).extend(listings)
    return map_with_hybrids


async def get_model_slug_map(
    page: Page,
    request: APIRequestContext,
    slugs: dict[str, str],
    slimmed: list[dict],
    make: str,
    model: str,
) -> dict[str, str]:
    mapped_by_title: dict[str, list[dict]] = {}
    map_with_hybrids: dict[str, list[dict]] = {}
    relevant_slugs: dict[str, str] = {}

    # Sort listings by mileage first to hopefully get valid VINs first (KBB may not have info for newer vehicles)
    def _sort_key(listing: dict):
        condition_rank = 0 if listing["condition"].lower() == "used" else 1
        return (condition_rank, listing["mileage"])

    def _get_vins(listings: list) -> list[str]:
        return [listing["vin"] for listing in listings[: min(10, len(listings))]]

    for l in slimmed:
        year = l["year"]
        ymm = f"{year} {make} {model}"
        mapped_by_title.setdefault(ymm, []).append(l)

    map_with_hybrids = get_hybrid_map(dict(sorted(mapped_by_title.items())))

    for model_key, listings in map_with_hybrids.items():
        if slugs and model_key in slugs:
            relevant_slugs[model_key] = slugs[model_key]
            continue
        year = model_key[:4]
        safe_make = make_string_url_safe(make)
        trim = model_key.replace(year, "").replace(make, "").strip()
        model_slug = make_string_url_safe(trim)
        url = f"https://www.kbb.com/{safe_make}/{model_slug}/{year}/"
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
        except TimeoutError as t:
            # print(f"Could not model slug from VINs: {model_key}", t)
            mesg = f"Could not model slug from VINs: {model_key}"

    print(
        f"No models could be mapped for {model_key}. This vehicle's information does not appear on KBB."
    )
    return ""


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

    relevant_entries = get_relevant_entries(cache_entries, make, model, year)
    have_norm = {
        normalize_trim(
            v.get("kbb_trim", "").split(f"{year} {make} {model}", 1)[-1].strip()
        )
        for v in relevant_entries.values()
        if "kbb_trim" in v
    }

    if expected_trims:
        expected_norm: set[str] = {normalize_trim(t) for t in expected_trims}
        all_fresh = expected_norm.issubset(have_norm) and all(
            is_pricing_fresh(e) for e in relevant_entries.values()
        )
    else:
        all_fresh = bool(relevant_entries) and all(
            is_pricing_fresh(e) for e in relevant_entries.values()
        )

    if all_fresh:
        # print(f"Cache for {year} {make} {model} is complete and fresh, skipping fetch")
        return

    url = f"https://kbb.com/{make_string_url_safe(make)}/{model_slug}/{year}"
    await page.goto(url)
    try:
        await page.wait_for_selector("table.css-lb65co tbody tr >> nth=0", timeout=5000)
        rows = await page.query_selector_all("table.css-lb65co tbody tr")
    except TimeoutError as t:
        await page.wait_for_selector("div.css-127mtog table", timeout=5000)
        rows = await page.query_selector_all("div.css-127mtog table tbody tr")

    # Collect the pricing data before attempting to get FMV, otherwise page context gets
    # overwritten and Playwright will throw an error
    pricing_data = []
    for row in rows:
        divs = await row.query_selector_all("div")
        if divs:
            if len(divs) < 3:
                continue

            table_trim = (await divs[0].inner_text()).strip()
            msrp = (await divs[1].inner_text()).strip()
            fpp = (await divs[2].inner_text()).strip()
            pricing_data.append((table_trim, msrp, fpp))
        else:
            tds = await row.query_selector_all("td")
            if len(tds) < 2:
                continue
            table_trim = (await tds[0].inner_text()).strip()
            msrp = (await tds[1].inner_text()).strip()
            fpp = None
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

            # only here do we call FMV
            fmv, fmv_source, timestamp = await get_or_fetch_fmv(
                page, year, make, model_slug, match_trim, kbb_trim_option, cache_entries
            )

        else:
            if fpp:
                print(f"ℹ️  No FMV data for {kbb_trim}; saving MSRP/FPP only")
            else:
                print(f"ℹ️  No pricing data for {kbb_trim}; saving MSRP only")
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


def get_trim_valuations_from_cache(
    make: str, model: str, years: list[str], entries: dict
) -> list[TrimValuation]:
    trim_valuations = []
    for y in years:
        for entry in get_relevant_entries(entries, make, model, y).values():
            entry.setdefault("model", None)
            entry.setdefault("fmv", None)
            entry.setdefault("fmv_source", None)
            entry.setdefault("msrp", None)
            entry.setdefault("msrp_source", None)
            entry.setdefault("fpp", None)
            entry.setdefault("fpp_source", None)

            trim_valuations.append(TrimValuation.from_dict(entry))
    return trim_valuations


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
    async with async_playwright() as p:
        request = await p.request.new_context()
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
            relevant_slugs = await get_model_slug_map(
                page, request, slugs, listings, make, model
            )

            for ymm, slug in relevant_slugs.items():
                if slug:
                    year = ymm[:4]
                    make_model = ymm.replace(year, "").strip()
                    model_name = make_model.replace(make, "").strip()
                    await get_trim_options_for_year(
                        page, make, slug, year, trim_options, make_model
                    )
                    expected_trims = set(trim_options.get(make_model, {}).get(year, []))
                    await get_or_fetch_new_pricing_for_year(
                        page,
                        make,
                        model_name,
                        slug,
                        year,
                        cache_entries,
                        expected_trims,
                    )

        finally:
            try:
                await browser.close()
            except Exception:
                pass
            save_cache(cache)

    for ymm in relevant_slugs.keys():
        year = ymm[:4]
        new_model = ymm.replace(year, "").replace(make, "").lower().strip()
        for entry in get_relevant_entries(
            cache_entries, make, new_model, year
        ).values():
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

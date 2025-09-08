import json, re

from collections import Counter, defaultdict
from datetime import datetime
from playwright.async_api import async_playwright, Page, TimeoutError

from analysis.cache import is_fmv_fresh, is_pricing_fresh, save_cache
from analysis.models import TrimValuation
from analysis.normalization import find_visor_key, normalize_trim
from analysis.utils import money_to_int
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
    raise Exception(f"No model slugs could be retrieved from provided VINs: {vins}")


async def get_trim_options_for_year(
    page, make, model_slug, year, trim_map, trim_options, make_model_key
):
    if make_model_key in trim_options and year in trim_options[make_model_key]:
        year_trims = trim_options[make_model_key][year]
        check_trim_collisions(year, year_trims, list(trim_map[year].keys()))

        for kbb_trim in year_trims:
            norm_trim = normalize_trim(kbb_trim)
            visor_key = find_visor_key(norm_trim, list(trim_map[year].keys()))
            if visor_key:
                trim_map[year][visor_key].append(kbb_trim)
            else:
                continue

        # Add 'Base' fallback even in cached path
        base_key = next((k for k in trim_map[year] if k.lower() == "base"), None)
        if base_key and not trim_map[year][base_key]:
            # Synthesize a single "body_style" from cached names so the picker works
            bs = [{"trims": [{"name": n} for n in year_trims]}]
            picked = pick_base_by_common_tokens(bs)
            if picked:
                trim_map[year][base_key].append(picked)
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

            norm_trim = normalize_trim(kbb_trim)
            visor_key = find_visor_key(norm_trim, list(trim_map[year].keys()))

            if visor_key:
                trim_map[year][visor_key].append(kbb_trim)
            else:
                continue

    base_key = next((k for k in trim_map[year] if k.lower() == "base"), None)
    if base_key and not trim_map[year][base_key]:
        picked = pick_base_by_common_tokens(body_styles)
        if picked:
            trim_map[year][base_key].append(picked)
            # optional: logger.info("Base fallback mapped to %s", picked)

    trim_options.setdefault(make_model_key, {})[year] = year_trims


async def get_or_fetch_new_pricing_for_year(
    page: Page,
    make: str,
    model: str,
    model_slug: str,
    year: str,
    trim_map_for_year: dict[str, list[str]],
    cache_entries,
) -> None:
    # pre-check before hitting the page
    all_fresh = True
    for visor_key in trim_map_for_year.keys():
        visor_trim = f"{year} {make} {model} {visor_key}"
        entry = cache_entries.get(visor_trim, {})
        if not is_pricing_fresh(entry):
            all_fresh = False
            break

    if all_fresh:
        return  # nothing to do this year, skip webcall

    url = f"https://kbb.com/{make_string_url_safe(make)}/{model_slug}/{year}"
    await page.goto(url)
    rows = await page.query_selector_all("table.css-lb65co tbody tr")
    visor_keys = list(trim_map_for_year.keys())

    for row in rows:
        divs = await row.query_selector_all("div")
        if len(divs) < 3:
            continue

        table_trim = (await divs[0].inner_text()).strip()
        msrp = (await divs[1].inner_text()).strip()
        fpp = (await divs[2].inner_text()).strip()

        # Normalize KBB label and map to visor key
        norm_trim = normalize_trim(table_trim)
        visor_key = find_visor_key(norm_trim, visor_keys)

        if not visor_key:
            # print(
            #     f"{year}: Unable to map KBB trim '{table_trim}' (normalized '{norm_trim}') to visor keys: {visor_keys}"
            # )
            continue

        visor_trim = f"{year} {make} {model} {visor_key}"
        entry = cache_entries.setdefault(visor_trim, {})

        msrp_val = money_to_int(msrp)
        fpp_val = money_to_int(fpp)

        entry["visor_trim"] = visor_trim
        if msrp_val is not None:
            entry["msrp"] = msrp_val
            entry["msrp_source"] = url
        if fpp_val is not None:
            entry["fpp"] = fpp_val
            entry["fpp_source"] = url

        entry["pricing_timestamp"] = datetime.now().isoformat()


async def get_or_fetch_fmv(
    page: Page,
    year: str,
    make: str,
    model: str,
    model_slug: str,
    trim: str,
    style: str,
    cache_entries: dict[str, dict],
):
    # Always normalize the KBB style and map it back to visor trim key
    norm_style = normalize_trim(style)
    visor_key = find_visor_key(norm_style, [trim])
    if not visor_key:
        return

    visor_trim = f"{year} {make} {model} {visor_key}"
    kbb_trim = f"{year} {make} {model} {style}"

    entry = cache_entries.setdefault(visor_trim, {})

    # Check cache first
    if is_fmv_fresh(entry):
        return TrimValuation.from_dict(
            {
                **entry,
                "visor_trim": visor_trim,
                "kbb_trim": entry.get("kbb_trim", kbb_trim),
            }
        )

    fmv_url = f"https://kbb.com/{make_string_url_safe(make)}/{model_slug}/{year}/{make_string_url_safe(style)}/"
    await page.goto(fmv_url)
    div_text = await page.inner_text("div.css-fbyg3h")

    match = re.search(r"current resale value of \$([\d,]+)", div_text)
    if match:
        resale_value = int(match.group(1).replace(",", ""))
        entry.update(
            {
                "visor_trim": visor_trim,
                "kbb_trim": kbb_trim,
                "fmv": resale_value,
                "fmv_source": fmv_url,
                "timestamp": datetime.now().isoformat(),
            }
        )
        return TrimValuation.from_dict(entry)
    else:
        # ✅ fallback when FMV is missing
        entry.update(
            {
                "visor_trim": visor_trim,
                "kbb_trim": kbb_trim,
                "fmv": None,
                "fmv_source": None,
                "timestamp": datetime.now().isoformat(),
            }
        )
        return TrimValuation.from_dict(entry)


def get_trim_valuations_from_cache(
    make, model, trim_map, cache_entries
) -> list[TrimValuation]:
    trim_valuations = []
    for year, trims in trim_map.items():
        for trim in trims.keys():
            visor_trim = f"{year} {make} {model} {trim}"
            cached = cache_entries[visor_trim]

            # ensure keys always exist
            cached.setdefault("fmv", None)
            cached.setdefault("fmv_source", None)
            cached.setdefault("msrp", None)
            cached.setdefault("msrp_source", None)
            cached.setdefault("fpp", None)
            cached.setdefault("fpp_source", None)

            trim_valuations.append(
                TrimValuation(
                    visor_trim=visor_trim,
                    kbb_trim=cached["kbb_trim"],
                    fmv=cached["fmv"],
                    fmv_source=cached["fmv_source"],
                    msrp=cached["msrp"],
                    msrp_source=cached["msrp_source"],
                    fpp=cached["fpp"],
                    fpp_source=cached["fpp_source"],
                )
            )
    return trim_valuations


async def get_trim_valuations_from_scrape(
    make: str,
    model: str,
    years: list[str],
    vins: list[str],
    trim_map,
    slugs,
    trim_options,
    cache_entries,
    cache,
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
                    page, make, model_slug, year, trim_map, trim_options, make_model_key
                )
                await get_or_fetch_new_pricing_for_year(
                    page, make, model, model_slug, year, trim_map[year], cache_entries
                )

                # 🔧 Patch: ensure all trims from pricing table are in trim_map
                for visor_trim, entry in cache_entries.items():
                    if not visor_trim.startswith(f"{year} {make} {model}"):
                        continue
                    kbb_trim = entry.get("kbb_trim")
                    if not kbb_trim:
                        continue
                    # visor_key is just the last piece (e.g., "Premium")
                    raw_key = visor_trim.replace(f"{year} {make} {model}", "").strip()
                    visor_key = raw_key if raw_key else "Base"
                    # make sure trim_map has this key and style
                    if visor_key not in trim_map[year]:
                        trim_map[year][visor_key] = []
                    if kbb_trim not in trim_map[year][visor_key]:
                        trim_map[year][visor_key].append(kbb_trim)

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


def pick_base_by_common_tokens(body_styles: list[dict]) -> str | None:
    # Collect KBB trim names per body style; usually you’ll hit the right one (e.g., “Wagon 4D”)
    for bs in body_styles:
        names = [t["name"] for t in bs.get("trims", [])]
        if not names:
            continue

        # Normalize/tokenize
        def toks(s: str) -> list[str]:
            return [w.lower() for w in re.findall(r"[A-Za-z0-9]+", s)]

        token_lists = [toks(n) for n in names]
        common = set(token_lists[0])
        for tl in token_lists[1:]:
            common &= set(tl)
        if not common:
            continue  # no obvious common suffix/prefix; try next body style

        # Trim with zero residual tokens after removing common = “Base”
        best = None
        best_res = []
        for n, tl in zip(names, token_lists):
            res = [w for w in tl if w not in common]
            if not res:
                return n  # perfect match: only common words (e.g., "Wagon 4D")
            if (
                best is None
                or len(res) < len(best_res)
                or (len(res) == len(best_res) and len(n) < len(best))
            ):
                best, best_res = n, res

        # Fallback: shortest residual wins
        if best:
            return best
    return None


def check_trim_collisions(year: str, kbb_trims: list[str], visor_keys: list[str]):
    grouped = defaultdict(list)
    for raw in kbb_trims:
        norm = normalize_trim(raw)
        match = find_visor_key(norm, visor_keys)  # use the new staged logic
        if match:
            grouped[match].append(raw)

    collisions = {k: v for k, v in grouped.items() if len(v) > 1}
    if collisions:
        msg_lines = [f"⚠️ Trim mapping collision for {year}:"]
        for visor_key, raws in collisions.items():
            msg_lines.append(f"  Visor key '{visor_key}' maps to: {', '.join(raws)}")
        # print warning instead of raising
        print("\n".join(msg_lines))

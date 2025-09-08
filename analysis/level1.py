from __future__ import annotations

import json, re

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from playwright.async_api import async_playwright, Page
from typing import Optional

from analysis.models import CarListing, DealBin, TrimValuation
from visor_scraper.utils import make_string_url_safe

CACHE_FILE = Path("output") / "level1_pricing_cache.json"
CACHE_TTL = timedelta(days=7)

DEAL_ORDER = ["Great", "Good", "Fair", "Poor", "Bad"]
COND_ORDER = ["New", "Certified", "Used"]

UNDER = -10.0  # ≤ -10% = strong underpriced
OVER = 10.0  # ≥ +10% = strong overpriced
DROP = 2000  # ≥ $2,000 price whiplash
LOW_PCTL = 0.15
HIGH_PCTL = 0.85
EXAMPLE_LIMIT = 3


# def build_trim_tables(listings: list[CarListing]) -> dict[str, dict]:
#     tables = defaultdict(lambda: {"total": 0, "rows": []})

#     # group by year/make/model (string key for Jinja)
#     for l in listings:
#         key = f"{l.year} {l.make} {l.model}"  # e.g., "2022 Subaru Outback"
#         tables[key]["total"] += 1
#         tables[key].setdefault("counts", Counter())
#         tables[key]["counts"][
#             l.title.split()[2]
#         ] += 1  # crude trim pick; replace with your own

#     # build rows with share + thin data
#     for key, info in tables.items():
#         total = info["total"]
#         rows = []
#         for trim, count in info["counts"].most_common():
#             share = round(count / total * 100)
#             thin = count < 3
#             rows.append(
#                 {
#                     "trim": trim,
#                     "count": count,
#                     "share": f"{share}%",
#                     "thin": thin,
#                 }
#             )
#         info["rows"] = rows
#         del info["counts"]

#     return tables


def _deviation_pct(
    price: int | float, compare_price: int | float | None
) -> Optional[float]:
    if compare_price and compare_price > 0 and isinstance(price, (int, float)):
        return (price - compare_price) / compare_price
    return None


def build_bins_and_crosstab(listings: list[CarListing]) -> tuple[list[DealBin], dict]:
    """
    Returns (deal_bins:list[DealBin], crosstab:dict)
    - deal_bins includes avg_deviation_pct, condition_counts, percent_of_total
    - crosstab is a nested dict: {bin: {condition: count}}
    """
    # totals
    total = 0
    for row in listings:
        if row.deviation_pct is None:
            row.deviation_pct = _deviation_pct(row.price, row.fmv)
        total += 1

    # group by bin
    by_bin: dict[str, list[CarListing]] = {k: [] for k in DEAL_ORDER}
    for row in listings:
        if row.deal_rating in by_bin:
            by_bin[row.deal_rating].append(row)

    # cross-tab counts
    crosstab: dict[str, dict[str, int]] = {
        b: {c: 0 for c in COND_ORDER} for b in DEAL_ORDER
    }
    for row in listings:
        if row.deal_rating in DEAL_ORDER and row.condition in COND_ORDER:
            crosstab[row.deal_rating][row.condition] += 1

    # build DealBin objects with summaries
    deal_bins: list[DealBin] = []
    for b in DEAL_ORDER:
        items = by_bin[b]
        count = len(items)

        # avg deviation (only valid numbers)
        sum_dev = 0.0
        n_dev = 0
        for r in items:
            if isinstance(r.deviation_pct, (int, float)):
                sum_dev += r.deviation_pct
                n_dev += 1
        avg_dev = (sum_dev / n_dev) if n_dev else None

        # condition breakdown for this bin
        cond_counts = {c: crosstab[b][c] for c in COND_ORDER}

        deal_bins.append(
            DealBin(
                category=b,
                listings=items,
                count=count,
                avg_deviation_pct=avg_dev,
                condition_counts=cond_counts,
                percent_of_total=(count / total * 100.0) if total else 0.0,
            )
        )

    return deal_bins, crosstab


def compute_condition_distribution_total(
    all_listings: list[CarListing],
    no_price_bin: DealBin | None = None,
) -> dict[str, int]:
    counts = {c: 0 for c in COND_ORDER}

    def bump(c: str | None):
        c = c if c in counts else "Used"  # keep matrix tidy
        counts[c] += 1

    for r in all_listings:
        bump(getattr(r, "condition", None))

    if no_price_bin:
        for r in no_price_bin.listings:
            bump(getattr(r, "condition", None))

    return counts


def _percentile(values: list[int], p: float) -> float:
    """Inclusive-linear percentile; p in [0,1]."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    i = p * (len(s) - 1)
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    frac = i - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _base_label(l: CarListing) -> str:
    last5 = (l.vin or "")[-5:]
    title = (l.title or "").strip()
    return f"{l.id} · {last5}" + (f" · {title}" if title else "")


_EXTRA_RULES = {
    "strong_underpriced": lambda l: (
        [f"{l.deviation_pct:+.1f}%"] if l.deviation_pct is not None else []
    ),
    "strong_overpriced": lambda l: (
        [f"{l.deviation_pct:+.1f}%"] if l.deviation_pct is not None else []
    ),
    "cond_price_mismatch": lambda l: [l.condition]
    + ([f"{l.deviation_pct:+.1f}%"] if l.deviation_pct is not None else []),
    "miles_price_tension": lambda l: (
        [f"{l.miles:,} mi"] if l.miles is not None else []
    ),
    "price_whiplash": lambda l: (
        [f"Δ${int(l.price_delta):,}"] if l.price_delta is not None else []
    ),
    "highrisk_bargains": lambda l: (
        ["High risk" if l.risk == "High" else "High uncertainty"]
        if (l.risk == "High" or l.uncertainty == "High")
        else [] + ([f"{l.deviation_pct:+.1f}%"] if l.deviation_pct is not None else [])
    ),
}


def fmt_example(l, kind: str) -> str:
    base = _base_label(l)
    extras = _EXTRA_RULES.get(kind, lambda _l: [])(l)
    return base if not extras else f"{base} — " + " — ".join(extras)


def mileage_price_tension(listings: list) -> list:
    miles_list = [l.miles for l in listings if l.miles is not None]
    if not miles_list:
        return []
    low_cut = _percentile(miles_list, LOW_PCTL)
    high_cut = _percentile(miles_list, HIGH_PCTL)

    def is_tension(l) -> bool:
        if l.miles is None or l.deviation_pct is None:
            return False
        # low miles but at/below FMV → underpriced despite low miles
        if l.miles <= low_cut and l.deviation_pct <= 0:
            return True
        # high miles but at/above FMV → overpriced despite high miles
        if l.miles >= high_cut and l.deviation_pct >= 0:
            return True
        return False

    return [l for l in listings if is_tension(l)]


def summarize_outliers(listings: list):
    # Strong under/over
    strong_under = [
        l for l in listings if l.deviation_pct is not None and l.deviation_pct <= UNDER
    ]
    strong_over = [
        l for l in listings if l.deviation_pct is not None and l.deviation_pct >= OVER
    ]

    # Condition/Price mismatch
    cond_price = [
        l
        for l in listings
        if (
            (l.condition == "Certified" and (l.deviation_pct or 0) <= -7.0)
            or (
                l.condition == "New"
                and l.fpp
                and l.price is not None
                and l.price < 0.95 * l.fpp
            )
            or (l.deal_rating == "Bad" and l.condition == "Certified")
        )
    ]

    # Mileage/Price tension (uses percentile logic)
    tension = mileage_price_tension(listings)

    # Price whiplash (big recent change)
    whiplash = [l for l in listings if abs(l.price_delta or 0) >= DROP]

    # High-risk bargains (cheap but risky)
    highrisk_barg = [
        l
        for l in listings
        if (l.risk == "High" or l.uncertainty == "High")
        and ((l.deal_rating in ("Great", "Good")) or ((l.deviation_pct or 0) <= -7.0))
    ]

    # Sort for nicest examples
    strong_under.sort(key=lambda l: l.deviation_pct or 0)  # most negative first
    strong_over.sort(key=lambda l: -(l.deviation_pct or 0))  # most positive first
    cond_price.sort(key=lambda l: (l.deviation_pct or 0))  # certified underpriced first
    tension.sort(
        key=lambda l: abs(l.deviation_pct or 0), reverse=True
    )  # biggest mismatch first
    whiplash.sort(key=lambda l: abs(l.price_delta or 0), reverse=True)
    highrisk_barg.sort(key=lambda l: (l.deviation_pct or 0))  # cheapest first

    def examples(ls, kind):
        return [fmt_example(x, kind) for x in ls[:EXAMPLE_LIMIT]]

    return {
        "thresholds": {"under_pct": UNDER, "over_pct": OVER, "drop_usd": DROP},
        "strong_underpriced": {
            "count": len(strong_under),
            "examples": examples(strong_under, "strong_underpriced"),
        },
        "strong_overpriced": {
            "count": len(strong_over),
            "examples": examples(strong_over, "strong_overpriced"),
        },
        "cond_price_mismatch": {
            "count": len(cond_price),
            "examples": examples(cond_price, "cond_price_mismatch"),
        },
        "miles_price_tension": {
            "count": len(tension),
            "examples": examples(tension, "miles_price_tension"),
        },
        "price_whiplash": {
            "count": len(whiplash),
            "examples": examples(whiplash, "price_whiplash"),
        },
        "highrisk_bargains": {
            "count": len(highrisk_barg),
            "examples": examples(highrisk_barg, "highrisk_bargains"),
        },
    }


def to_level1_json(
    make: str, model: str, sort: str, deal_bins: list[DealBin], crosstab: dict
) -> dict:

    all_listing_count = sum(b.count for b in deal_bins)
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
    }


def create_report_parameter_summary(metadata: dict) -> str:
    """
    Creates a summary header for the level 1 analysis report that briefly goes over which parameters that were used in the search.
    This include condition, price filters, mileage filters, and the sort method
    """

    summary = "This report reflects{condition_summary}listings retrieved using the {sort_method} sort option"
    condition_summary = ""
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
    make,
    model,
    cache_entries,
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

    report_title = f"Level 1 Market Analysis Report – {make} {model}"  # utils.format_years(metadata["years"])
    generated_at = datetime.now().strftime("%B %d, %Y %I:%M %p")

    summary = create_report_parameter_summary(metadata)

    # Build embedded JSON object
    embedded_data = {
        "make": make,
        "model": model,
        "generated_at": generated_at,
        "summary": summary,
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


def is_fmv_fresh(entry):
    if "timestamp" not in entry:
        return False
    ts = datetime.fromisoformat(entry["timestamp"])
    return datetime.now() - ts < CACHE_TTL


def is_pricing_fresh(entry: dict) -> bool:
    ts = entry.get("pricing_timestamp")
    if not ts:
        return False
    saved = datetime.fromisoformat(ts)
    now = datetime.now()

    # Fresh if we're still in the same month & year
    return (saved.year == now.year) and (saved.month == now.month)


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
            if visor_trim not in cache_entries or not is_fmv_fresh(
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
        # Replace the make and model in case they use multiple words (Aston Marton, Crown Victoria)
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


def match_visor_key(kbb_name: str, visor_keys: list[str]) -> str | None:
    k = kbb_name.strip()
    for key in sorted(visor_keys, key=len, reverse=True):
        if k.startswith(key):
            return key
    return None


def money_to_int(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip()
    if "—" in s or "N/A" in s or s == "":
        return None
    num = "".join(ch for ch in s if ch.isdigit())
    return int(num) if num else None


def pick_base_by_common_tokens(body_styles: list[dict]) -> str | None:
    # Collect KBB trim names per body style; usually you’ll hit the right one (e.g., “Wagon 4D”)
    for bs in body_styles:
        names = [t["name"] for t in bs.get("trims", [])]
        print(names)
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


async def get_trim_options_for_year(
    page, make, model_slug, year, trim_map, trim_options, make_model_key
):
    if make_model_key in trim_options and year in trim_options[make_model_key]:
        year_trims = trim_options[make_model_key][year]
        for kbb_trim in year_trims:
            match = match_visor_key(kbb_trim, list(trim_map[year].keys()))
            if match:
                trim_map[year][match].append(kbb_trim)

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
            for key in keys:
                if kbb_trim.startswith(key):
                    trim_map[year][key].append(kbb_trim)
                    break

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

        # Map KBB table label -> Visor trim key
        visor_key = match_visor_key(table_trim, visor_keys)
        if not visor_key:
            # If "Base" exists and our earlier styles-mapping attached a style (e.g., "Wagon 4D"),
            # honor that here when the table label equals that style.
            if "Base" in trim_map_for_year and table_trim in set(
                trim_map_for_year["Base"]
            ):
                visor_key = "Base"
        if not visor_key:
            print("Unable to find a matching trim: ", visor_key)
            continue  # couldn't map; log if you want

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
    visor_trim = f"{year} {make} {model} {trim}"
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
                "kbb_trim": kbb_trim,
                "fmv": resale_value,
                "fmv_source": fmv_url,
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
                await get_or_fetch_new_pricing_for_year(
                    page, make, model, model_slug, year, trim_map[year], cache_entries
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


def rate_deal(price, delta, compare_price) -> str:
    if price == 0:
        return "No price"

    if delta <= -2000 or price <= compare_price * 0.93:
        return "Great"
    elif (-2000 < delta <= -1000) or (
        compare_price * 0.93 < price <= compare_price * 0.97
    ):
        return "Good"
    elif (-999 <= delta <= 999) or (
        compare_price * 0.97 < price < compare_price * 1.03
    ):
        return "Fair"
    elif (2000 > delta >= 1000) or (
        compare_price * 1.03 <= price < compare_price * 1.07
    ):
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
        "condition": listing.get("condition"),
        "price": _to_int(listing.get("price")),
        "mileage": _to_int(listing.get("mileage")),
        "days_on_market_delta": _days_on_market(listing),
        "price_history_lowest": _price_history_lowest(listing.get("price_history")),
        "report_present": carfax_present or autocheck_present,
        "window_sticker_present": sticker_present,
        "warranty_info_present": warranty_present,
    }


async def create_level1_file(listings: list[dict], metadata: dict):
    cache, slugs, trim_options, cache_entries = prepare_cache()
    make = metadata["vehicle"]["make"]
    model = metadata["vehicle"]["model"]
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

    no_price_bin = DealBin(category="No Price", listings=[], count=0)
    all_listings: list[CarListing] = []
    seen_ids: set[str] = set()  # guard if input has dupes

    for listing in listings:
        listing_key = listing["title"]
        fmv = cache_entries[listing_key]["fmv"]
        fpp = cache_entries[listing_key]["fpp"]
        if listing.get("price"):
            price = listing["price"]
            # New prices should be compared to fair purpose price, while Used and Certified
            # should use fair market value
            if listing["condition"] == "New":
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
        risk = rate_risk(listing, price, fmv)

        year = listing_key[:4]
        trim = (
            listing_key.replace(year, "").replace(make, "").replace(model, "").strip()
        )

        car_listing = CarListing(
            id=listing["id"],
            vin=listing["vin"],
            year=int(year),
            make=make,
            model=model,
            trim=trim,
            condition=listing["condition"],
            miles=listing["mileage"],
            price=price,
            price_delta=delta,
            uncertainty=uncertainty,
            risk=risk,
            deal_rating=deal,
            compare_price=compare_price,
            msrp=cache_entries[listing_key]["msrp"],
            fpp=fpp,
            fmv=fmv,
            deviation_pct=_deviation_pct(price, fmv),
        )

        if deal == "No price":
            no_price_bin.listings.append(car_listing)
            no_price_bin.count += 1
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

    cond_dist_total = compute_condition_distribution_total(all_listings, no_price_bin)

    analysis_json = to_level1_json(
        make=make,
        model=model,
        sort=metadata["filters"]["sort"],  # already available in start_level1_analysis
        deal_bins=deal_bins,
        crosstab=crosstab,
    )
    analysis_json["condition_distribution"] = cond_dist_total

    outliers_json = summarize_outliers(all_listings)

    # Only pass along the entries from the quicklist, not the entire cache
    visible_entries = {k: cache_entries[k] for k in quicklist if k in cache_entries}

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
    )


async def start_level1_analysis(
    listings: list[dict], metadata: dict, args, timestamp: str
):
    """
    Builds 'level1_input_<Make>_<Model>_<Timestamp>.jsonc' next to your outputs.
    Returns the file path.
    Call this AFTER you've saved listings.json and closed the browser.
    """
    if not listings:
        raise ValueError("No listings provided to create_level1_file().")

    # Slim all listings
    slimmed = [_slim(l) for l in listings if l is not None]

    await create_level1_file(slimmed, metadata)

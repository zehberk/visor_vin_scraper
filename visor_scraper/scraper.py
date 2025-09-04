import argparse, asyncio, json, logging, os, sys
from tqdm import tqdm
from urllib.parse import urlencode
from playwright.async_api import async_playwright, TimeoutError
from visor_scraper.constants import *
from visor_scraper.download import download_files
from visor_scraper.utils import *
from analysis.level1 import start_level1_analysis

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


async def fetch_page(page, url):
    try:
        await page.goto(url, timeout=60000)
        await page.wait_for_function(
            "(args) => !!(document.querySelector(args.sel) || document.body.innerText.includes(args.empty))",
            arg={"sel": LISTING_CARD_SELECTOR, "empty": NO_LISTINGS_FOUND_TEXT},
            timeout=10000,
        )

        # Branch based on what appeared.
        if await page.locator(LISTING_CARD_SELECTOR).count() > 0:
            return True
        logging.info("No listings found.")
        return True  # still 'success' — downstream will find 0 cards
    except TimeoutError as e:
        logging.error(f"Page did not reach results or empty state: {e}")
        return False
    except Exception as e:
        logging.error(f"Page load failed: {e}")
        return False


async def extract_numbers_from_sidebar(page, metadata):
    sidebar = await page.query_selector("text=/\\d+ for sale nationwide/")
    if sidebar:
        text = await sidebar.inner_text()
        match = TOTAL_NATIONWIDE_REGEX.search(text)
        if match:
            metadata["site_info"]["total_for_sale"] = int(
                match.group(1).replace(",", "")
            )
            logging.info(
                f"Total for sale nationwide: {metadata["site_info"]['total_for_sale']}"
            )


async def parse_warranty_coverage(coverage, index, metadata):
    entry = {}

    entry["type"] = await safe_text(
        coverage, COVERAGE_TYPE_ELEMENT, f"Coverage type from #{index}", metadata
    )
    entry["status"] = await safe_text(
        coverage,
        COVERAGE_STATUS_ELEMENT,
        f"Coverage status for {entry["type"]} from #{index}",
        metadata,
    )

    try:
        limits = await coverage.query_selector_all(COVERAGE_LIMIT_ELEMENTS)
        if len(limits) >= 2:
            # Time values
            time_values = await limits[0].query_selector_all(
                COVERAGE_LIMIT_VALUES_ELEMENTS
            )
            time_left = (
                await safe_inner_text(time_values[0], "Time Left", index, metadata)
                if len(time_values) > 0
                else None
            )
            time_total = (
                await safe_inner_text(time_values[1], "Time Total", index, metadata)
                if len(time_values) > 1
                else None
            )

            # Miles values
            miles_values = await limits[1].query_selector_all(
                COVERAGE_LIMIT_VALUES_ELEMENTS
            )
            miles_left = (
                await safe_inner_text(miles_values[0], "Miles Left", index, metadata)
                if len(miles_values) > 0
                else None
            )
            miles_total = (
                await safe_inner_text(miles_values[1], "Miles Total", index, metadata)
                if len(miles_values) > 1
                else None
            )

            entry["time_left"] = time_left
            entry["time_total"] = time_total
            entry["miles_left"] = miles_left
            entry["miles_total"] = miles_total
    except Exception as e:
        metadata["warnings"].append(e)

    return entry


async def extract_warranty_info(page, listing, index, metadata):
    listing.setdefault("warranty", {}).setdefault("coverages", [])
    listing["warranty"]["overall_status"] = await safe_text(
        page,
        WARRANTY_STATUS_TEXT_ELEMENT,
        f"Warranty Status for #{index}",
        metadata,
        "No warranty info found",
    )

    # page is the details page. since we don't have cards to enumerate on
    # we can use page instead of card for calling safe_text or other functions
    coverages = await page.query_selector_all(COVERAGE_ELEMENTS)
    for coverage in coverages:
        entry = await parse_warranty_coverage(coverage, index, metadata)
        listing["warranty"]["coverages"].append(entry)


async def extract_additional_documents(page, listing, index, metadata):
    listing.setdefault("additional_docs", {})
    listing["additional_docs"]["autocheck_url"] = await get_url(
        page, AUTOCHECK_URL_ELEMENT, index, metadata
    )
    listing["additional_docs"]["carfax_url"] = await get_url(
        page, CARFAX_URL_ELEMENT, index, metadata
    )
    listing["additional_docs"]["window_sticker_url"] = await get_url(
        page, WINDOW_STICKER_URL_ELEMENT, index, metadata
    )


async def extract_seller_info(page, listing, index, metadata):
    listing.setdefault("seller", {})

    seller_div = await page.query_selector(SELLER_BLOCK_ELEMENT)
    if not seller_div:
        listing["seller"]["name"] = "N/A"
        listing["seller"]["location"] = "N/A"
        listing["seller"]["map_url"] = "N/A"
        listing["seller"]["stock_number"] = "N/A"
        listing["seller"]["phone"] = "N/A"
        return

    seller_info = await safe_text(
        seller_div, SELLER_NAME_ELEMENT, f"Seller Info #{index}", metadata
    )
    if " in " in seller_info:
        name, location = seller_info.split(" in ", 1)
        listing["seller"]["name"] = name
        listing["seller"]["location"] = location
    else:
        metadata["warnings"].append(
            f"Failed to read seller name/location in listing {index}"
        )

    try:
        await page.wait_for_selector(GOOGLE_MAP_ELEMENT, timeout=2000)
        seller_map_url = await page.get_attribute(GOOGLE_MAP_ELEMENT, "href")
        listing["seller"]["map_url"] = seller_map_url
    except TimeoutError:
        metadata["warnings"].append(
            f"Google Maps link not found for seller in listing #{index}"
        )
        listing["seller"]["map_url"] = "N/A"

    button_elements = await seller_div.query_selector_all(BUTTON_ELEMENTS)
    stock_num = phone_num = "N/A"

    if len(button_elements) >= 1:
        stock_num = await safe_text(
            button_elements[0], STOCK_NUM_ELEMENT, f"Stock Num #{index}", metadata
        )

    if len(button_elements) >= 2:
        phone_num = await safe_text(
            button_elements[1], PHONE_NUM_ELEMENT, f"Phone Num #{index}", metadata
        )

    listing["seller"]["stock_number"] = stock_num
    listing["seller"]["phone"] = phone_num


async def extract_market_velocity(page, listing, index, metadata):
    try:
        await page.wait_for_selector(VELOCITY_ELEMENTS, timeout=5000)
        sections = await page.query_selector_all(VELOCITY_SECTION_ELEMENTS)

        market_velocity = {}

        if len(sections) >= 1:
            sold_el = await sections[0].query_selector(VEHICLE_SOLD_ELEMENT)
            if sold_el:
                text = await sold_el.inner_text()
                market_velocity["vehicles_sold_14d"] = int(text.strip())

        if len(sections) >= 2:
            labels = await sections[1].query_selector_all(DAYS_ON_MARKET_ELEMENT)
            if len(labels) >= 1:
                days = await labels[0].inner_text()
                market_velocity["avg_days_on_market"] = int(
                    days.strip().replace(" days", "").replace(" day", "")
                )
            if len(labels) >= 2:
                days = await labels[1].inner_text()
                market_velocity["this_vehicle_days"] = int(
                    days.strip().replace(" days", "").replace(" day", "")
                )

        if len(sections) >= 3:
            demand_el = await sections[2].query_selector(DEMAND_ELEMENT)
            if demand_el:
                text = await demand_el.inner_text()
                percent = int(text.strip().replace("% chance", ""))
                market_velocity["sell_chance_7d"] = round(percent / 100, 2)

        if market_velocity:
            listing["market_velocity"] = market_velocity

    except Exception as e:
        msg = f"Failed to extract market velocity for listing {index}: {e}"
        metadata["warnings"].append(msg)


async def extract_install_options(page, listing, index, metadata):
    listing["installed_addons"] = {"items": [], "total": 0}

    try:
        no_opts = await page.query_selector("text=No options found")
        if no_opts:
            return

        await page.wait_for_selector(ADDON_LI_ELEMENTS, timeout=2000)
        addon_elements = page.query_selector_all(ADDON_LI_ELEMENTS)

        addons = []
        total = 0
        for idx, addon in enumerate(await addon_elements):
            text = await addon.inner_text()
            if text.startswith("Total options:"):
                match = PRICE_MATCH_REGEX.search(text)
                if match:
                    total = int(match.group(1).replace(",", ""))
            else:
                match = ADDON_REGEX.search(text)
                if match:
                    name = match.group(1).strip()
                    price = int(match.group(2).replace(",", ""))
                else:
                    name = text.strip()
                    price = 0

                addons.append({"name": name, "price": price})

        listing["installed_addons"] = {"items": addons, "total": total}
    except TimeoutError as t:
        metadata["warnings"].append(
            f"Timeout when extracting car add-ons for listing #{index}."
        )
    except Exception as e:
        metadata["warnings"].append(
            f"Could not extract install options for listing #{index}: {e}"
        )


async def extract_spec_details(page, listing, index, metadata):
    listing.setdefault("specs", {})
    specs = {}
    SKIP_LABELS = {
        "VIN",
        "Warranty Status",
    }  # These are already being handled in other parts of the code

    try:
        await page.wait_for_selector(SPEC_TABLE_ELEMENT, timeout=2000)
        rows = await page.query_selector_all(SPEC_ROW_ELEMENTS)

        for row in rows:
            cells = await row.query_selector_all("td")
            if not cells:
                continue

            # 4-column row: two spec pairs
            if len(cells) == 4:
                for i in (0, 2):
                    label = (
                        await safe_inner_text(cells[i], "Spec", index, metadata) or ""
                    ).rstrip(":")
                    if not label or label in SKIP_LABELS:
                        continue
                    specs[label] = await safe_inner_text(
                        cells[i + 1], f"{label} value", index, metadata
                    )

            # 2-column row: special handling
            elif len(cells) == 2:
                label = (await cells[0].inner_text()).strip().rstrip(":")
                if label == "Installed Options":
                    await extract_install_options(page, listing, index, metadata)
                elif label == "Additional Documentation":
                    await extract_additional_documents(page, listing, index, metadata)
                elif label == "Seller":
                    await extract_seller_info(page, listing, index, metadata)
        # Store specs in listing after loop
        if specs:
            listing["specs"] = specs
    except TimeoutError as t:
        metadata["warnings"].append(
            f"Could not extract spec details for listing #{index}: {t}"
        )
    except Exception as e:
        metadata["warnings"].append(
            f"Could not extract spec details for listing #{index}: {e}"
        )


async def extract_price_history(page, listing, index, metadata):
    listing.setdefault("price_history", {})
    price_history = []
    await page.wait_for_selector(PRICE_HISTORY_ELEMENT, timeout=2000)
    price_changes = await page.query_selector_all(PRICE_CHANGE_ELEMENTS)

    for change in price_changes:
        entry = {
            "date": None,
            "price": None,
            "price_change": None,
            "mileage": None,
            "lowest": False,
        }

        blocks = await change.query_selector_all("div.space-y-1")
        if len(blocks) != 2:
            continue

        # Left Block
        left_divs = await blocks[0].query_selector_all("div")
        if len(left_divs) >= 1:
            entry["date"] = (await left_divs[0].inner_text()).strip()
        if len(left_divs) >= 2:
            price_change_text = await left_divs[1].inner_text()
            match = PRICE_CHANGE_REGEX.search(price_change_text)
            if match:
                entry["price_change"] = int(
                    match.group(1).replace("$", "").replace(",", "")
                )

        # Right Block
        right_divs = await blocks[1].query_selector_all("div")
        if len(right_divs) >= 1:
            price_text = await right_divs[0].inner_text()
            entry["lowest"] = "Lowest" in price_text
            if "$" in price_text:
                price_match = PRICE_CHANGE_REGEX.search(price_text)
                if price_match:
                    entry["price"] = int(
                        price_match.group(1).replace(",", "").replace("$", "")
                    )

        for div in right_divs:
            miles_text = await div.inner_text()
            if miles_text.strip().endswith("mi"):
                miles_match = MILES_MATCH_REGEX.search(miles_text)
                if miles_match:
                    entry["mileage"] = int(miles_match.group(1).replace(",", ""))
                break

        price_history.append(entry)
    listing["price_history"] = price_history


async def extract_full_listing_details(browser, listing, index, metadata):
    context = await browser.new_context()
    await context.add_cookies(
        convert_browser_cookies_to_playwright(".session/cookies.json")
    )
    detail_page = await context.new_page()
    try:
        vin = listing.get("vin")
        url = VIN_DETAILS_URL.format(vin=vin)
        await detail_page.goto(url, timeout=60000)
        await detail_page.wait_for_selector(DETAIL_PAGE_ELEMENT, timeout=20000)

        try:
            link = await detail_page.query_selector(LISTING_URL_ELEMENT)
            listing_url = await link.get_attribute("href") if link else None
        except TimeoutError:
            metadata["warnings"].append(f"Failed to get listing URL for #{index}")
            listing_url = "None"
        listing["listing_url"] = listing_url
        await extract_spec_details(detail_page, listing, index, metadata)
        await extract_warranty_info(detail_page, listing, index, metadata)
        await extract_market_velocity(detail_page, listing, index, metadata)
        await extract_price_history(detail_page, listing, index, metadata)
    except Exception as e:
        listing["error"] = f"Failed to fetch full details: {e}"
    finally:
        await detail_page.close()


async def extract_listings(browser, page, metadata, max_listings=50):
    listings = []
    cards = await page.query_selector_all(LISTING_CARD_SELECTOR)

    # Even though this is already an int, the runtime environment
    # may pass it as a string, so we ensure it's an int
    max_listings = int(max_listings)

    if len(cards) > max_listings:
        logging.info(
            f"Found {len(cards)} listings, but limiting to {max_listings} as per --max_listings."
        )
        cards = cards[:max_listings]

    for idx, card in enumerate(tqdm(cards, desc="Extracting listings", unit="car")):
        index = idx + 1
        try:
            title = await safe_text(card, TITLE_ELEMENT, f"title #{index}", metadata)
            price = await safe_text(card, PRICE_ELEMENT, f"price #{index}", metadata)
            mileage = await safe_text(
                card, MILEAGE_ELEMENT, f"mileage ${index}", metadata
            )
            vin = await safe_vin(card, index, metadata)

            listing = {
                "id": index,
                "title": title,
                "price": price,
                "mileage": mileage,
                "vin": vin,
            }

            try:
                await extract_full_listing_details(
                    browser, listing, index, metadata
                )  # Fetch full details in background
            except:
                msg = f"Failed to extract the full details on listing #{index}"
                metadata["warnings"].append(msg)
                logging.error(msg)
            listings.append(listing)

        except Exception as e:  # pragma: no cover
            metadata["warnings"].append(f"Skipping listing #{index}: {e}")
    return listings


async def safe_vin(card, index, metadata):
    try:
        href = await card.get_attribute("href")
        return href.split("/")[-1].split("?")[0] if href else None
    except Exception as e:
        msg = f"Listing #{index}: Failed to extract VIN: {e}"
        logging.warning(msg)
        metadata["warnings"].append(msg)
        return None


def save_results(listings, metadata, args, output_dir="output"):
    ts = current_timestamp()
    filename = f"{args.make}_{args.model}_listings_{ts}.json".replace(" ", "_")
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"metadata": metadata, "listings": listings},
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"Saved {len(listings)} listings to {path}")
    return ts


async def auto_scroll_to_load_all(page, metadata, max_listings, delay_ms=250):
    previous_count = 0
    i = 0
    print(f"Starting auto-scroll to load up to {max_listings} listings...")

    while True:
        cards = await page.query_selector_all(LISTING_CARD_SELECTOR)
        current_count = len(cards)

        print(f"\tFound {current_count} listings...")

        if current_count >= int(max_listings):
            print(f"\tStopping at {max_listings} (cap reached).")
            break

        if current_count == previous_count:
            print(f"\tScroll ended at {current_count} listings (no more found).")
            break

        previous_count = current_count
        i += 1

        await page.evaluate(
            f"""
			const container = document.querySelector('{SCROLL_CONTAINER_SELECTOR}');
			if (container) container.scrollTop = container.scrollHeight;
		"""
        )

        try:
            await page.wait_for_selector(
                f"{LISTING_CARD_SELECTOR} >> nth={previous_count}", timeout=5000
            )
        except:
            logging.info("No new listings detected after scroll wait.")
            break

        await page.wait_for_timeout(
            delay_ms
        )  # Optional: wait a little extra for UI to settle

    metadata["runtime"]["scrolls"] = i


async def scrape(args):
    metadata = build_metadata(args)
    query_params = build_query_params(args, metadata)
    url = f"{BASE_URL}?{urlencode(query_params)}"
    metadata["runtime"]["url"] = url
    listings = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        if not await fetch_page(page, url):
            await browser.close()
            return
        await extract_numbers_from_sidebar(page, metadata)
        if args.max_listings > 50:
            await auto_scroll_to_load_all(page, metadata, args.max_listings)
        else:
            metadata["runtime"]["scrolls"] = 0
        listings = await extract_listings(
            browser, page, metadata, args.max_listings
        )  # pragma: no cover
        if len(listings) == 0:
            metadata["warnings"].append(
                "No listings found. Please check your input and try again"
            )
        timestamp = save_results(listings, metadata, args)  # pragma: no cover
        await browser.close()  # pragma: no cover
    if args.save_docs:
        await download_files(listings)
    if listings:
        level1_path = await start_level1_analysis(listings, metadata, args, timestamp)


def save_preset_if_requested(args):
    if not args.save_preset:
        return

    while True:
        preset_name = input("Enter a name for this preset: ").strip()
        if not preset_name:
            logging.error("Preset name cannot be empty.")
            exit(1)

        # Exclude non-search-related flags
        exclude = {"preset", "save_preset"}
        preset_data = {
            k: v for k, v in vars(args).items() if k not in exclude and v is not None
        }

        # Load existing presets
        if PRESET_PATH.exists():
            with open(PRESET_PATH) as f:
                presets = json.load(f)
        else:
            presets = {}

        if preset_name in presets:
            print(preset_name)
            accept = input(
                f"{preset_name} already exists. Would you like to overwrite it (y/n)?"
            ).strip()
            print(accept)
            if accept.lower() in {"y", "yes"}:
                presets[preset_name] = preset_data
                break
            else:
                continue  # pragma: no cover
        else:
            presets[preset_name] = preset_data
            break

    with open(PRESET_PATH, "w") as f:
        json.dump(presets, f, indent=2)
    logging.info(f"Preset '{preset_name}' saved.")


def resolve_args(args):
    if args.preset and args.save_preset:
        logging.error("You cannot use --preset and --save-preset together.")
        exit(1)

    if args.preset:
        if not PRESET_PATH.exists():
            logging.error(f"Preset file not found: {PRESET_PATH}")
            exit(1)

        with open(PRESET_PATH) as f:
            presets = json.load(f)

        preset_data = presets.get(args.preset)
        if not preset_data:
            logging.error(f"Profile '{args.preset}' not found.")
            exit(1)

        explicit_flags = {
            arg.lstrip("-") for arg in sys.argv[1:] if arg.startswith("-")
        }
        for k, v in preset_data.items():
            if k not in explicit_flags:
                setattr(args, k, v)
    elif args.save_preset:
        save_preset_if_requested(args)

    if not args.make or not args.model:
        logging.error("You must provide either a --preset OR both --make and --model.")
        exit(1)

    return args


# Entry point
def main():  # pragma: no cover

    parser = argparse.ArgumentParser(
        description="Scrape vehicle listings from visor.vin.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    presets = parser.add_argument_group("Preset profiles")
    docs = parser.add_argument_group("Documents")
    required = parser.add_argument_group("Required arguments")
    behavior = parser.add_argument_group("Scraper behavior")
    filters = parser.add_argument_group("Search filters")
    sorting = parser.add_argument_group("Sorting options")

    presets.add_argument(
        "--preset", type=str, help="Optional preset name from presets.json"
    )
    presets.add_argument(
        "--save-preset", action="store_true", help="Save this search as a preset"
    )

    docs.add_argument(
        "--save_docs",
        action="store_true",
        help="Save the documents retrieved from the listings",
    )

    required.add_argument("--make", type=str, help="Vehicle make (e.g., Jeep)")
    required.add_argument("--model", type=str, help="Vehicle model (e.g., Wrangler)")

    behavior.add_argument(
        "--max_listings",
        type=capped_max_listings,
        default=50,
        help="Maximum number of listings to retrieve (default: 50, max: 500)",
    )

    filters.add_argument(
        "--trim",
        nargs="+",
        type=str,
        help="One or more trim names (quoted if multi-word)",
    )
    filters.add_argument(
        "--year", nargs="+", help="Model years or ranges (e.g., 2021 2022-2024 20-22)"
    )
    filters.add_argument("--min_miles", type=int, help="Minimum mileage")
    filters.add_argument("--max_miles", type=int, help="Maximum mileage")
    filters.add_argument(
        "--miles", type=str, help="Mileage range (e.g., 10000-60000). Overrides min/max"
    )
    filters.add_argument("--min_price", type=int, help="Minimum price")
    filters.add_argument("--max_price", type=int, help="Maximum price")
    filters.add_argument(
        "--price", type=str, help="Price range (e.g., 10000-60000). Overrides min/max"
    )
    filters.add_argument(
        "--condition",
        choices=CONDITIONS,
        nargs="+",
        help="Condition(s) to filter (New, Used, Certified)",
    )

    sorting.add_argument(
        "--sort",
        choices=SORT_OPTIONS.keys(),
        default="Newest",
        help="Sort order for results",
    )

    args = resolve_args(parser.parse_args())
    asyncio.run(scrape(args))


if __name__ == "__main__":  # pragma: no cover
    main()

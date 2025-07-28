import argparse
import asyncio
import json
import logging
import os
import re
import sys
from tqdm import tqdm
from urllib.parse import urlencode
from playwright.async_api import async_playwright, TimeoutError
from scraper.constants import *
from scraper.utils import *

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

async def fetch_page(page, url):
	try:
		await page.goto(url, timeout=60000)
		await page.wait_for_selector(LISTING_CARD_SELCTOR, timeout=20000)
	except Exception as e:
		logging.error(f"Page load or selector wait failed: {e}")
		return False
	return True

async def extract_numbers_from_sidebar(page, metadata):
	sidebar = await page.query_selector("text=/\\d+ for sale nationwide/")
	if sidebar:
		text = await sidebar.inner_text()
		match = re.search(r"(\d[\d,]*) for sale nationwide", text)
		if match:
			metadata["site_info"]["total_for_sale"] = int(match.group(1).replace(",", ""))
			logging.info(f"Total for sale nationwide: {metadata["site_info"]['total_for_sale']}")

async def parse_warranty_coverage(coverage, index, metadata):
	entry = {}
	
	entry["type"] = await safe_text(coverage, COVERAGE_TYPE_ELEMENT, f"Coverage type from #{index}", metadata)
	entry["status"] = await safe_text(coverage, COVERAGE_STATUS_ELEMENT, f"Coverage status for {entry["type"]} from #{index}", metadata)

	limits = await coverage.query_selector_all(COVERAGE_LIMIT_ELEMENTS)
	if len(limits) >= 6:
		entry["time_left"] = (await limits[1].inner_text()).strip()
		entry["time_total"] = (await limits[2].inner_text()).strip()
		entry["miles_left"] = (await limits[4].inner_text()).strip()
		entry["miles_total"] = (await limits[5].inner_text()).strip()

	return entry

async def extract_warranty_info(page, listing, index, metadata):
	listing.setdefault("warranty", {}).setdefault("coverages", [])
	listing["warranty"]["overall_status"] = await safe_text(
		page, 
		WARRANTY_STATUS_TEXT_ELEMENT, 
		f"Warranty Status for #{index}", 
		metadata, 
		"No warranty info found")
	
	# page is the details page. since we don't have cards to enumerate on
	# we can use page instead of card for calling safe_text or other functions
	coverages = await page.query_selector_all(COVERAGE_ELEMENTS)
	for coverage in coverages:
		entry = await parse_warranty_coverage(coverage, index, metadata)
		listing["warranty"]["coverages"].append(entry)

async def extract_additional_documents(page, listing, index, metadata):
	listing.setdefault("additional_docs", {})
	carfax_url = window_sticker_url = "Unavailable"
	try:
		link = await page.query_selector(WINDOW_STICKER_URL_ELEMENT)
		print(f"{link}")
		carfax_url = await link.get_attribute("href") if link else "Unavailable"
	except TimeoutError as e:
		# We are logging in warnings, but marking as unimportant because a user may not have cookies saved or plus privileges
		metadata["warnings"].append(f"[Info] Additional document timed out for listing #{index}. Cookies out of date/not set or subscription inactive")
	except Exception as err:
		# This is a serious error, output to the console
		logging.error(f"{err}")
	listing["additional_docs"]["carfax_url"] = carfax_url

	# Can't get the href directly because it is not constant between listsings
	try:
		link = await page.query_selector(WINDOW_STICKER_URL_ELEMENT)
		window_sticker_url = await link.get_attribute("href") if link else "Unavailable"
	except TimeoutError as e:
		# We are logging in warnings, but marking as unimportant because a user may not have cookies saved or plus privileges
		metadata["warnings"].append(f"[Info] Additional document timed out for listing #{index}. Cookies out of date/not set or subscription inactive")
	except Exception as err:
		# This is a serious error, output to the console
		logging.error(f"{err}")
	listing["additional_docs"]["window_sticker_url"] = window_sticker_url

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
	
	seller_info = await safe_text(seller_div, SELLER_NAME_ELEMENT, f"Seller Info #{index}", metadata)
	if " in " in seller_info:
		name, location = seller_info.split(" in ", 1)
		listing["seller"]["name"] = name
		listing["seller"]["location"] = location
	else:
		metadata["warnings"].append(f"Failed to read seller name/location in listing {index}")

	try:
		await page.wait_for_selector(GOOGLE_MAP_ELEMENT, timeout=2000)
		seller_map_url = await page.get_attribute(GOOGLE_MAP_ELEMENT, "href")
		listing["seller"]["map_url"] = seller_map_url
	except TimeoutError:
		metadata["warnings"].append(f"Failed to read Map URL for seller in listing {index}")

	button_elements = await seller_div.query_selector_all(BUTTON_ELEMENTS)
	stock_num = phone_num = "N/A"

	if len(button_elements) >= 1:
		stock_num = await safe_text(button_elements[0], STOCK_NUM_ELEMENT, f"Stock Num #{index}", metadata)

	if len(button_elements) >= 2:
		phone_num = await safe_text(button_elements[1], PHONE_NUM_ELEMENT, f"Phone Num #{index}", metadata)
	
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
				market_velocity["avg_days_on_market"] = int(days.strip().replace(" days", "").replace(" day", ""))
			if len(labels) >= 2:
				days = await labels[1].inner_text()
				market_velocity["this_vehicle_days"] = int(days.strip().replace(" days", "").replace(" day", ""))

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

async def extract_spec_details(page, listing, index, metadata):
				listing.setdefault("specs", {})
				specs = {}
				SKIP_LABELS = {"VIN", "Warranty Status"}  # These are already being handled in other parts of the code

				await page.wait_for_selector("tbody.w-full", timeout=2000)
				rows = await page.query_selector_all("tbody.w-full tr")

				for row in rows:
					cells = await row.query_selector_all("td")
					if not cells:
						continue
					
					# 4-column row: two spec pairs
					if len(cells) == 4:
						for i in (0, 2):
							label = (await cells[i].inner_text()).strip().rstrip(":")
							if label in SKIP_LABELS:
								continue
							value = (await cells[i+1].inner_text()).strip()
							specs[label] = value  # optionally normalize keys here

					# 2-column row: special handling
					elif len(cells) == 2:
						label = (await cells[0].inner_text()).strip().rstrip(":")
						content = cells[1]

						if label == "Installed Options":
							options = await content.inner_html()
							listing["options"] = "N/A" # TODO: Update in new commit

						elif label == "Additional Documentation":
							await extract_additional_documents(page, listing, index, metadata)
						elif label == "Seller":
							await extract_seller_info(page, listing, index, metadata)
				# Store specs in listing after loop
				if specs:
					listing["specs"] = specs	

async def extract_full_listing_details(browser, listing, index, metadata):	
	context = await browser.new_context()
	await context.add_cookies(convert_browser_cookies_to_playwright(".session/cookies.json"))
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
	except Exception as e:
		listing["error"] = f"Failed to fetch full details: {e}"
	finally:
		await detail_page.close()

async def extract_listings(browser, page, metadata, max_listings=50):
	listings = []
	cards = await page.query_selector_all(LISTING_CARD_SELCTOR)

	# Even though this is already an int, the runtime environment
	# may pass it as a string, so we ensure it's an int
	max_listings = int(max_listings)
	
	if len(cards) > max_listings:
		logging.info(f"Found {len(cards)} listings, but limiting to {max_listings} as per --max_listings.")
		cards = cards[:max_listings]

	for idx, card in enumerate(tqdm(cards, desc="Extracting listings", unit="car")):
		index = idx+1
		try:
			title = await safe_text(card, TITLE_ELEMENT, f"title #{index}", metadata)
			price = await safe_text(card, PRICE_ELEMENT, f"price #{index}", metadata)
			mileage = await extract_mileage(card, index, metadata)
			vin = await safe_vin(card, index, metadata)

			listing = {
				"id": index,
				"title": title,
				"price": price,
				"mileage": mileage,
				"vin": vin
			}

			await extract_full_listing_details(browser, listing, index, metadata)  # Fetch full details in background
			listings.append(listing)

		except Exception as e:		# pragma: no cover
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

async def extract_mileage(card, index, metadata):
	mileage = "N/A"
	try:
		blocks = await card.query_selector_all(TEXT_BLOCKS_SELECTOR)
		for block in blocks:
			try:
				text = (await block.inner_text()).strip()
				if "mi" in text and mileage == "N/A":
					mileage = text
			except:
				continue
	except Exception as e:
		msg = f"Listing #{index}: Failed to read mileage/listed: {e}"
		logging.warning(msg)
		metadata["warnings"].append(msg)
	return mileage

def save_results(listings, metadata, args, output_dir="output"):
	filename = f"{args.make}_{args.model}_listings_{current_timestamp()}.json".replace(" ", "_")
	path = os.path.join(output_dir, filename)
	with open(path, "w", encoding="utf-8") as f:
		json.dump({"metadata": metadata, "listings": listings}, f, indent=2, ensure_ascii=False)
	print(f"Saved {len(listings)} listings to {path}")

async def auto_scroll_to_load_all(page, metadata, max_listings=300, delay_ms=250):
	previous_count = 0
	i = 0
	print(f"Starting auto-scroll to load up to {max_listings} listings...")

	while True:
		cards = await page.query_selector_all(LISTING_CARD_SELCTOR)
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

		await page.evaluate(f"""
			const container = document.querySelector('{SCROLL_CONTAINER_SELECTOR}');
			if (container) container.scrollTop = container.scrollHeight;
		""")

		try:
			await page.wait_for_selector(f"{LISTING_CARD_SELCTOR} >> nth={previous_count}", timeout=5000)
		except:
			logging.info("No new listings detected after scroll wait.")
			break

		await page.wait_for_timeout(delay_ms)  # Optional: wait a little extra for UI to settle

	metadata["runtime"]["scrolls"] = i

async def scrape(args):

	if args.preset:
		if not PRESET_PATH.exists():
			logging.error(f"Preset file not found: {PRESET_PATH}")
			exit(1)
		with open(PRESET_PATH) as f:
			presets = json.load(f)
		if args.preset not in presets:
			logging.error(f"Profile '{args.preset}' not found.")
			exit(1)
		# Determine which args were passed explicitly on the command line
		explicit_flags = {arg.lstrip("-") for arg in sys.argv[1:] if arg.startswith("-")}

		# Fill in missing args with profile defaults
		for k, v in presets[args.preset].items():
			if k not in explicit_flags:
				setattr(args, k, v)

	if not args.preset and (not args.make or not args.model):
		logging.error("You must provide either a --preset OR both --make and --model.")
		exit(1)	

	metadata = build_metadata(args)
	query_params = build_query_params(args, metadata)
	url = f"{BASE_URL}?{urlencode(query_params)}"
	metadata["runtime"]["url"] = url
	warn_if_missing_env_vars("SB_DB_AUTH_TOKEN_0", "SB_DB_AUTH_TOKEN_1")

	async with async_playwright() as pw:
		browser = await pw.chromium.launch(headless=True)
		page = await browser.new_page()
		
		if not await fetch_page(page, url):
			await browser.close()
			return
		await extract_numbers_from_sidebar(page, metadata)
		await auto_scroll_to_load_all(page, metadata, max_listings=args.max_listings)
		listings = await extract_listings(browser, page, metadata, max_listings=args.max_listings)	# pragma: no cover
		save_results(listings, metadata, args)				# pragma: no cover
		await browser.close()								# pragma: no cover

# Entry point
def main():  # pragma: no cover

	parser = argparse.ArgumentParser(
		description="Scrape vehicle listings from visor.vin.",
		formatter_class=argparse.ArgumentDefaultsHelpFormatter
	)

	presets = parser.add_argument_group("Preset profiles")
	required = parser.add_argument_group("Required arguments")
	behavior = parser.add_argument_group("Scraper behavior")
	filters = parser.add_argument_group("Search filters")
	sorting = parser.add_argument_group("Sorting options")
	
	presets.add_argument("--preset", type=str, help="Optional preset name from presets.json")

	required.add_argument("-m", "--make", type=str, help="Vehicle make (e.g., Jeep)")
	required.add_argument("-o", "--model", type=str, help="Vehicle model (e.g., Wrangler)")

	behavior.add_argument("--max_listings", type=capped_max_listings, default=50, help="Maximum number of listings to retrieve (default: 50, max: 500)")

	filters.add_argument("-t", "--trim", nargs="+", type=str, help="One or more trim names (quoted if multi-word)")
	filters.add_argument("-y", "--year", nargs="+", help="Model years or ranges (e.g., 2021 2022-2024 20-22)")
	filters.add_argument("--min_miles", type=int, help="Minimum mileage")
	filters.add_argument("--max_miles", type=int, help="Maximum mileage")
	filters.add_argument("-l", "--miles", type=str, help="Mileage range (e.g., 10000-60000). Overrides min/max")
	filters.add_argument("--min_price", type=int, help="Minimum price")
	filters.add_argument("--max_price", type=int, help="Maximum price")
	filters.add_argument("-p", "--price", type=str, help="Price range (e.g., 10000-60000). Overrides min/max")
	filters.add_argument("-c", "--condition", choices=CONDITIONS, nargs="+", help="Condition(s) to filter (New, Used, Certified)")

	sorting.add_argument("-s", "--sort", choices=SORT_OPTIONS.keys(), default="Newest", help="Sort order for results")

	args = parser.parse_args()
	asyncio.run(scrape(args))

if __name__ == "__main__":	#pragma: no cover
	main()
'''
This script is designed to scrape vehicle listings from visor.vin using Playwright.
It accepts command-line arguments for vehicle make, model, and an optional agnostic mode.

v1 - Only meant to retrieve the first car on a page.
v2 - Retrieves all cars on the page (50) and saves them as a json file.
v3 - Expanded arguments and sorting functionality.
v4 - Adding advanced criteria for arguments, such as ranges and multiple selections.
v5 - Added error handling paired with test_scraper.py.
v6 - Added presets functionality.
v7 - Added auto-scrolling to load more listings, improved metadata structure.
'''
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from urllib.parse import urlencode
from playwright.async_api import async_playwright
from scraper.constants import *
from scraper.utils import (
	normalize_years,
	remove_null_entries,
	parse_range_arg,
	current_timestamp
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

def capped_max_listings(value):
	ivalue = int(value)
	if ivalue > MAX_LISTINGS:
		raise argparse.ArgumentTypeError(f"Maximum allowed listings is {MAX_LISTINGS}.")
	return ivalue

def build_metadata(args):
	if not args.make or not args.make.strip():
		logging.error("--make is required and cannot be empty.")
		exit(1)
	if not args.model or not args.model.strip():
		logging.error("--model is required and cannot be empty.")
		exit(1)

	metadata = {
    	"vehicle": {
			"make": args.make,
			"model": args.model,
			"trim": args.trim,
			"year": normalize_years(args.year) if args.year else []
		},
		"filters": remove_null_entries(vars(args).copy()),
		"site_info": {},  # filled later
		"runtime": {
			"timestamp": current_timestamp()
		},
		"warnings": []
	}

	filters = vars(args).copy()
	for k in ("make", "model", "trim", "year", "preset"):
		filters.pop(k, None)
	metadata["filters"] = remove_null_entries(filters)

	return metadata

def build_query_params(args, metadata):
	if args.miles:
		if args.min_miles or args.max_miles:
			logging.warning("--miles overrides --min_miles and --max_miles.")
		args.min_miles, args.max_miles = parse_range_arg("miles", args.miles)
	if args.price:
		if args.min_price or args.max_price:
			logging.warning("--price overrides --min_price and --max_price.")
		args.min_price, args.max_price = parse_range_arg("price", args.price)

	# Default fallback for condition to suppress unnecessary warnings
	if not args.condition:
		args.condition = []
	# Normalize sort key if applicable (mainly for presets)
	if args.sort in SORT_OPTIONS:
		args.sort = SORT_OPTIONS[args.sort]

	args_dict = vars(args)
	query_params = {}

	for key, value in args_dict.items():
		try:
			remapper = REMAPPING_RULES.get(key)
			param_name = PARAM_NAME_OVERRIDES.get(key, key)

			if isinstance(remapper, dict):
				query_params[param_name] = remapper.get(value, value)
			elif callable(remapper):
				query_params[param_name] = remapper(value)
			elif isinstance(value, list):
				query_params[param_name] = ",".join(map(str, value)) if value else None
			else:
				query_params[param_name] = str(value).lower() if isinstance(value, bool) else value
		except Exception as e:
			msg = f"Failed to process argument '{key}': {e}"
			logging.warning(msg)
			metadata["warnings"].append(msg)

	# Clean and validate
	cleaned = {}
	for k, v in query_params.items():
		if v in (None, "") or (isinstance(v, list) and not any(v)):
			continue		# value was empty and optional; no need to warn
		cleaned[k] = v

	return cleaned

async def fetch_page(page, url):
	try:
		await page.goto(url, timeout=60000)
		await page.wait_for_selector(HREF_ELEMENT, timeout=20000)
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

async def extract_listings(page, metadata):
	print("Extracting listings...")
	listings = []
	cards = await page.query_selector_all(HREF_ELEMENT)

	for idx, card in enumerate(cards):
		try:
			title = await safe_text(card, TITLE_ELEMENT, f"title #{idx+1}", metadata)
			price = await safe_text(card, PRICE_ELEMENT, f"price #{idx+1}", metadata)
			mileage, listed = await extract_mileage_and_listed(card, idx, metadata)
			location = await safe_text(card, LOCATION_ELEMENT, f"location #{idx+1}", metadata)
			vin = await safe_vin(card, idx, metadata)

			listings.append({
				"title": title,
				"price": price,
				"mileage": mileage,
				"listed": listed,
				"location": location,
				"vin": vin
			})
		except Exception as e:		# pragma: no cover
			metadata["warnings"].append(f"Skipping listing #{idx+1}: {e}")
	return listings

async def safe_text(card, selector, label, metadata):
	try:
		element = await card.query_selector(selector)
		return await element.inner_text() if element else "N/A"
	except Exception as e:
		msg = f"Failed to read {label}: {e}"
		logging.warning(msg)
		metadata["warnings"].append(msg)
		return "N/A"

async def safe_vin(card, idx, metadata):
	try:
		href = await card.get_attribute("href")
		return href.split("/")[-1].split("?")[0] if href else None
	except Exception as e:
		msg = f"Listing #{idx+1}: Failed to extract VIN: {e}"
		logging.warning(msg)
		metadata["warnings"].append(msg)
		return None

async def extract_mileage_and_listed(card, idx, metadata):
	mileage = "N/A"
	listed = "N/A"
	try:
		blocks = await card.query_selector_all(TEXT_SM_BLOCKS)
		for block in blocks:
			try:
				text = (await block.inner_text()).strip()
				if "mi" in text and mileage == "N/A":
					mileage = text
				elif "Listed" in text and listed == "N/A":
					listed = text
			except:
				continue
	except Exception as e:
		msg = f"Listing #{idx+1}: Failed to read mileage/listed: {e}"
		logging.warning(msg)
		metadata["warnings"].append(msg)
	return mileage, listed

def save_results(listings, metadata, args, output_dir="."):
	filename = f"{args.make}_{args.model}_listings_{current_timestamp()}.json".replace(" ", "_")
	path = os.path.join(output_dir, filename)
	with open(path, "w", encoding="utf-8") as f:
		json.dump({"metadata": metadata, "listings": listings}, f, indent=2, ensure_ascii=False)
	logging.info(f"Saved listings to {path}")

async def auto_scroll_to_load_all(page, metadata, max_listings=300, delay_ms=250):
	previous_count = 0
	i = 0

	while True:
		cards = await page.query_selector_all(HREF_ELEMENT)
		current_count = len(cards)

		if current_count >= int(max_listings):
			logging.info(f"Reached max listings limit: {current_count} listings.")
			break

		if current_count == previous_count:
			logging.info("No new listings loaded; reached end of results.")
			break

		logging.info(f"[Scroll {i+1}] Found {current_count} listings...")
		previous_count = current_count
		i += 1

		await page.evaluate(f"""
			const container = document.querySelector('{SCROLL_CONTAINER_SELECTOR}');
			if (container) container.scrollTop = container.scrollHeight;
		""")

		try:
			await page.wait_for_selector(f"{HREF_ELEMENT} >> nth={previous_count}", timeout=5000)
		except:
			logging.info("No new listings detected after scroll wait.")
			break

		await page.wait_for_timeout(delay_ms)  # Optional: wait a little extra for UI to settle

	metadata["runtime"]["scrolls"] = i


async def scrape(args):

	if args.preset:
		with open("presets.json") as f:
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
	logging.info(f"Navigating to: {url}")

	async with async_playwright() as pw:
		browser = await pw.chromium.launch(headless=True)
		page = await browser.new_page()
		if not await fetch_page(page, url):
			await browser.close()
			return
		await extract_numbers_from_sidebar(page, metadata)
		await auto_scroll_to_load_all(page, metadata, max_listings=args.max_listings)
		listings = await extract_listings(page, metadata)	# pragma: no cover
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
import asyncio
import json
import os
import re
from visor_scraper.constants import *
from playwright.async_api import async_playwright, TimeoutError
from visor_scraper.utils import convert_browser_cookies_to_playwright, stopwatch

import json
import math

 # 4S4BTGUDXR3181217 - Addon options
 # 4S4BTGSDXP3146838 - Carfax/Window
 # 4S4BTGSDXR3119061 - Price History
async def check_detail_page(vin="4S4BTGSDXR3119061"): # pragma: no cover
	url = f"https://visor.vin/search/listings/{vin}"
	async with async_playwright() as pw:
		browser = await pw.chromium.launch(headless=True)
		context = await browser.new_context()
		# await context.add_cookies(load_auth_cookies())
		await context.add_cookies(convert_browser_cookies_to_playwright(".session/cookies.json"))
		page = await context.new_page()

		try:
			with stopwatch("Page Load Time"):
				await page.goto(url)
				await page.wait_for_selector(DETAIL_PAGE_ELEMENT, timeout=10000)
			
			with stopwatch("Price History"):
				price_history = []
				await page.wait_for_selector("div.space-y-3.pt-3.w-full", timeout=2000)
				price_changes = await page.query_selector_all("div.flex.items-center.justify-between.text-base")
				
				for idx, change in enumerate(price_changes):
					i = idx+1
					entry = {
						"date": None,
						"price": None,
						"price_change": None,
						"mileage": None,
						"lowest": False
					}

					blocks = await change.query_selector_all("div.space-y-1")
					if len(blocks) != 2:
						print(f"Too few/many blocks in ${i}!")
						continue

					# Left Block
					left_divs = await blocks[0].query_selector_all("div")
					if len(left_divs) >= 1:
						entry["date"] = (await left_divs[0].inner_text()).strip()
					if len(left_divs) >= 2:
						price_change_text = await left_divs[1].inner_text()
						match = re.search(r"(-?\$[\d,]+)", price_change_text)
						if match:
							entry["price_change"] = int(match.group(1).replace("$", "").replace(",", ""))

					# Right Block
					right_divs = await blocks[1].query_selector_all("div")
					if len(right_divs) >= 1:
						price_text = await right_divs[0].inner_text()
						entry["lowest"] = "Lowest" in price_text
						if "$" in price_text:
							price_match = re.search(r"\$([\d,]+)", price_text)
							if price_match:
								entry["price"] = int(price_match.group(1).replace(",", ""))

					if len(right_divs) >= 2:
						miles_text = await right_divs[1].inner_text()
						miles_match = re.search(r"([\d,]+)", miles_text)
						if miles_match:
							entry["mileage"] = int(miles_match.group(1).replace(",", ""))

					price_history.append(entry)
				print(json.dumps(price_history, indent=2))
				
		except Exception as e:
			print(f"❌ Failed to load or find element: {e}")
		finally:
			await browser.close()

asyncio.run(check_detail_page()) # pragma: no cover
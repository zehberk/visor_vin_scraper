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
async def check_detail_page(vin="4S4BTGUD4S3167531"): # pragma: no cover
	# url = f"https://visor.vin/search/listings/{vin}"
	url = "https://windowsticker.subaru.com/customerMonroneyLabel/pdf?jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3Nzg1MzYwMDEsImlzcyI6InN1YmFydSIsImF1ZCI6InNob3dtYXgiLCJlbnYiOiJwcm9kIiwid3MiOiJ3aW5kb3dTdGlja2VyL3IifQ.i6582N-cIJqcTGswegYQZUFCQLA_OlXUoI6E9ATcIdM&vin=4S4BTGUD0S3311415"
	async with async_playwright() as pw:
		browser = await pw.chromium.launch(headless=True)
		context = await browser.new_context(accept_downloads=True)
		# await context.add_cookies(load_auth_cookies())
		await context.add_cookies(convert_browser_cookies_to_playwright(".session/cookies.json"))
		page = await context.new_page()

		try:
			# with stopwatch("Page Load Time"):
			# 	await page.goto(url)
			# 	await page.wait_for_selector(DETAIL_PAGE_ELEMENT, timeout=10000)
			
			with stopwatch("Window Sticker Download"):
				output_file = "sticker.pdf"
				req = await pw.request.new_context()
				resp = await req.get(url)
				if not resp.ok:
					raise RuntimeError(f"Failed: {resp.status}")
				with open(output_file, "wb") as f:
					f.write(await resp.body())
				await req.dispose()
				print(f"Downloaded to {output_file}")
				
		except Exception as e:
			print(f"Failed to save sticker: {e}")
		finally:
			await browser.close()

asyncio.run(check_detail_page()) # pragma: no cover
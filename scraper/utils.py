# utils.py
import argparse
import logging
import os
import re
import time
from scraper.constants import *
from contextlib import contextmanager
from dotenv import load_dotenv
from datetime import datetime

@contextmanager
def stopwatch(label="Elapsed"):
	start = time.time()
	yield
	end = time.time()
	print(f"{label}: {end - start:.2f} seconds")

def normalize_years(raw_years):
	result = set()
	errors = []

	def convert_year(year_str: str) -> int:
		y = int(year_str)
		if len(year_str) == 4:
			return y
		elif y >= 50:
			return 1900 + y
		else:
			return 2000 + y

	for entry in raw_years:
		try:
			if "-" in entry:
				start_str, end_str = entry.split("-")
				start = convert_year(start_str)
				end = convert_year(end_str)
				if start > end:
					raise ValueError(f"Start year '{start}' is after end year '{end}'")
				result.update(range(start, end + 1))
			else:
				result.add(convert_year(entry))
		except ValueError as e:
			errors.append(f"[Year Error] Skipping '{entry}': {e}")
		except Exception as e:
			errors.append(f"[Year Error] Could not parse '{entry}': {e}")

	if not result:
		for msg in errors:
			print(msg)
		print("[Error] No valid years provided. Please check your --year format.")
		exit(1)
	elif errors:
		for msg in errors:
			print(msg)

	return sorted(result)

def remove_null_entries(d: dict) -> dict:
	return {k: v for k, v in d.items() if v is not None}

def sanitize_numeric_range(raw: str) -> str:
	return re.sub(r"[^\d\-]", "", raw)

def parse_range_arg(name: str, raw: str):
	try:
		raw = sanitize_numeric_range(raw)
		parts = raw.split("-")
		if len(parts) == 2:
			min_val = int(parts[0]) if parts[0] else None
			max_val = int(parts[1]) if parts[1] else None
		elif len(parts) == 1:
			min_val = int(parts[0])
			max_val = None
		else:
			raise ValueError("Too many hyphens in range input.")

		if min_val is None and max_val is None:
			raise ValueError(f"{name} range cannot be completely empty.")
		if min_val and max_val and min_val > max_val:
			raise ValueError(f"{name} range start cannot exceed end.")
		
		return min_val, max_val
	except Exception as e:
		print(f"[Error] Invalid format for --{name}: '{raw}' → {e}")
		exit(1)

def current_timestamp():
	return datetime.now().strftime("%Y%m%d_%H%M%S")

def load_auth_cookies():
	load_dotenv()
	cookies = []
	token0 = os.getenv("SB_DB_AUTH_TOKEN_0")
	token1 = os.getenv("SB_DB_AUTH_TOKEN_1")

	if token0:
		cookies.append({"name": "sb-db-auth-token.0", "value": token0, "domain": "visor.vin", "path": "/"})
	if token1:
		cookies.append({"name": "sb-db-auth-token.1", "value": token1, "domain": "visor.vin", "path": "/"})
	return cookies


async def safe_text(card, selector, label, metadata):
	try:
		element = await card.query_selector(selector)
		return await element.inner_text() if element else "N/A"
	except Exception as e:
		msg = f"Failed to read {label}: {e}"
		logging.warning(msg)
		metadata["warnings"].append(msg)
		return "N/A"

def warn_if_missing_env_vars(*keys):
	load_dotenv()
	for key in keys:
		if not os.getenv(key):
			logging.info(f"Optional environment variable not set: {key}. Premium features will not be scraped from the webpage")


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


# utils.py
import argparse, json, logging, math, os, re

from datetime import datetime
from playwright.async_api import Page

from utils.constants import MAX_LISTINGS, PARAM_NAME_OVERRIDES, SORT_OPTIONS


def format_years(metadata_years: list[str]) -> str:
    """Turn ['2017','2020','2023','2024','2025'] into '2017, 2020, 2023–2025'."""
    years = sorted(set(int(y) for y in metadata_years))
    ranges = []
    start = prev = years[0]

    for y in years[1:]:
        if y == prev + 1:
            prev = y
        else:
            if start == prev:
                ranges.append(f"{start}")
            else:
                ranges.append(f"{start}–{prev}")
            start = prev = y
    # flush last
    if start == prev:
        ranges.append(f"{start}")
    else:
        ranges.append(f"{start}–{prev}")

    return ", ".join(ranges)


def normalize_years(raw_years):
    result = set()

    def convert_year(year_str: str) -> int:
        y = int(year_str.strip())
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
            logging.error(f"[Year Error] Skipping '{entry}': {e}")
        except Exception as e:
            logging.error(f"[Year Error] Could not parse '{entry}': {e}")

    if not result or len(result) == 0:
        logging.error("No valid years provided. Please check your --year format.")
        exit(1)

    return ",".join(str(y) for y in sorted(result))


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
        logging.error(f"[Error] Invalid format for --{name}: '{raw}' → {e}")
        exit(1)


def current_timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


async def safe_text(card, selector, label, metadata, default="N/A"):
    try:
        element = await card.query_selector(selector)
        return await element.inner_text() if element else default
    except Exception as e:
        msg = f"Failed to read {label}: {e}"
        logging.warning(msg)
        metadata["warnings"].append(msg)
        return default


async def safe_inner_text(element, label, index, metadata):
    try:
        return (await element.inner_text()).strip()
    except Exception as e:
        metadata["warnings"].append(f"Listing #{index}: Failed to read {label}: {e}")
        return None


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
            "year": normalize_years(args.year) if args.year else [],
        },
        "filters": remove_null_entries(vars(args).copy()),
        "site_info": {},  # filled later
        "runtime": {"timestamp": current_timestamp()},
        "warnings": [],
    }

    filters = vars(args).copy()
    for k in ("make", "model", "trim", "year", "preset", "save_preset"):
        filters.pop(k, None)
    metadata["filters"] = remove_null_entries(filters)

    return metadata


def build_query_params(args, metadata):
    if args.miles:
        if args.min_miles or args.max_miles:
            logging.warning("--miles overrides --min_miles and --max_miles.")
        args.min_miles, args.max_miles = parse_range_arg("miles", args.miles)
        del metadata["filters"]["miles"]
        metadata["filters"]["min_miles"] = args.min_miles
        metadata["filters"]["max_miles"] = args.max_miles
    if args.price:
        if args.min_price or args.max_price:
            logging.warning("--price overrides --min_price and --max_price.")
        args.min_price, args.max_price = parse_range_arg("price", args.price)
        del metadata["filters"]["price"]
        metadata["filters"]["min_price"] = args.min_price
        metadata["filters"]["max_price"] = args.max_price

    # Default fallback for condition to suppress unnecessary warnings
    if not args.condition:
        args.condition = []
    # Normalize sort key if applicable (mainly for presets)
    if args.sort in SORT_OPTIONS:
        args.sort = SORT_OPTIONS[args.sort]

    # Remapping constants for query parameters
    # This allows for more user-friendly input while maintaining the correct URL parameters
    REMAPPING_RULES = {
        "sort": SORT_OPTIONS,
        "condition": lambda values: ",".join(v.lower() for v in values),
        "year": normalize_years if args.year else [],
    }
    IGNORE_ARGS = {
        "max_listings",
        "price",
        "miles",
        "preset",
        "save_preset",
        "save_docs",
        "force",
        "level1",
        "level2",
        "level3",
    }
    VALID_ARGS = {"make", "model", "trim", "year", "sort"}
    VALID_KEYS = set(VALID_ARGS) | set(PARAM_NAME_OVERRIDES.keys())

    args_dict = {k: v for k, v in vars(args).items() if k not in IGNORE_ARGS}
    query_params = {}

    for key, value in args_dict.items():
        try:
            remapper = REMAPPING_RULES.get(key)
            param_name = PARAM_NAME_OVERRIDES.get(key, key)

            if key not in VALID_KEYS:
                msg = f"Failed to process argument '{param_name}' is not a valid argument."
                logging.warning(msg)
                metadata["warnings"].append(msg)
                continue

            if isinstance(remapper, dict):
                query_params[param_name] = remapper.get(value, value)
            elif callable(remapper):
                query_params[param_name] = remapper(value)
            elif isinstance(value, list):
                query_params[param_name] = ",".join(map(str, value)) if value else None
            else:
                query_params[param_name] = (
                    str(value).lower() if isinstance(value, bool) else value
                )
        except Exception as e:
            msg = f"Failed to process argument '{key}': {e}"
            logging.warning(msg)
            metadata["warnings"].append(msg)

    # Clean and validate
    cleaned = {}
    for k, v in query_params.items():
        if v in (None, "") or (isinstance(v, list) and not any(v)):
            continue  # value was empty and optional; no need to warn
        cleaned[k] = v

    return cleaned


def convert_browser_cookies_to_playwright(path):
    # If file missing, just return empty
    if not os.path.exists(path):
        return []

    with open(path, "r") as f:
        content = f.read().strip()
        if not content:  # empty file
            return []

    try:
        raw_cookies = json.loads(content)
    except Exception:
        # Invalid JSON
        return []

    if not isinstance(raw_cookies, list) or not raw_cookies:
        return []

    playwright_cookies = []
    for c in raw_cookies:
        try:
            playwright_cookie = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "sameSite": c.get("sameSite", "Lax").capitalize(),
            }
            if "expirationDate" in c:
                # expirationDate is a float (Chrome), expires must be int (Playwright)
                playwright_cookie["expires"] = int(math.floor(c["expirationDate"]))
            playwright_cookies.append(playwright_cookie)
        except KeyError:
            continue

    return playwright_cookies


def cookies_file_is_empty(path: str = ".session/cookies.json") -> bool:
    """Quick check: return True if no cookies are stored or file is invalid."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return not bool(data)
    except Exception:
        return True


async def cookies_are_valid(page: Page) -> bool:
    """
    Confirm that cookies are still valid by checking for elements that require a subscription.
    Returns False if cookies are missing/expired.
    """
    try:
        await page.goto(
            "https://visor.vin/search/listings?make=Mazda&model=Miata&agnostic=false",
            timeout=3000,
        )
        await page.wait_for_selector("div.blur-xs", timeout=3000)
        return False
    except Exception:
        return True


async def get_url(page, selector, index, metadata):
    url = "Unavailable"

    try:
        link = await page.query_selector(selector)
        url = await link.get_attribute("href") if link else "Unavailable"
    except TimeoutError as e:
        # We are logging in warnings, but marking as unimportant because a user may not have cookies saved or plus privileges
        metadata["warnings"].append(
            f"[Info] Additional document timed out for listing #{index}. Cookies out of date/not set or subscription inactive"
        )
    except Exception as err:
        # This is a serious error, output to the console
        logging.error(f"{err}")

    return url

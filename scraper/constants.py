from pathlib import Path

PRESET_PATH = Path(__file__).parent.parent / "presets" / "presets.json"

# URL strings
BASE_URL = "https://visor.vin/search/listings"
VIN_DETAILS_URL = BASE_URL + "/{vin}"

CONDITIONS = {"New", "Used", "Certified"} # Vehicle conditions
SORT_OPTIONS = {
    "Best Match": "best_match",
    "Lowest Price": "cheapest",
    "Highest Price": "expensive",
    "Newest": "recent",
    "Oldest": "oldest",
    "Lowest Mileage": "lowest_miles",
    "Highest Mileage": "highest_miles",
}
PARAM_NAME_OVERRIDES = {        # For user-friendly parameter names
    "condition": "car_type",
    "min_miles": "miles_min",
    "max_miles": "miles_max",
    "min_price": "price_min",
    "max_price": "price_max"
}

# Remapping constants for query parameters
# This allows for more user-friendly input while maintaining the correct URL parameters
REMAPPING_RULES = {
    "sort": SORT_OPTIONS,
    "condition": lambda values: ",".join(v.lower() for v in values)
}

# HTML element selectors for main listing page
LISTING_CARD_SELCTOR = "a[href^='/search/listings/']"
TITLE_ELEMENT = "h2"
PRICE_ELEMENT = "div.absolute.bottom-2.left-2 span"
TEXT_BLOCKS_SELECTOR = "div.text-sm"
LOCATION_ELEMENT = "div.flex.items-start span"
SCROLL_CONTAINER_SELECTOR = "div.h-dvh.overflow-y-auto"

#HTML element selectors for detail page
DETAIL_PAGE_ELEMENT = "div.h-dvh.w-full.space-y-3.overflow-y-auto"
WARRANTY_STATUS_TEXT_ELEMENT = "div.text-base.text-black"
COVERAGE_ELEMENTS = "div.grid.grid-cols-1.gap-6 div.bg-\\[\\#F6F6F6\\].p-3.space-y-3"
COVERAGE_TYPE_ELEMENT = "div.bg-\\[\\#3B3B3B\\]"
COVERAGE_STATUS_ELEMENT = "div.text-sm div.text-sm"
COVERAGE_LIMIT_ELEMENTS = "div.space-y-1 div.text-sm"
CARFAX_URL_ELEMENT = 'a[data-posthog-event="View Carfax Report"]'
WINDOW_STICKER_URL_ELEMENT = 'a[data-posthog-event="View Window Sticker"]'
LISTING_URL_ELEMENT = 'a[data-posthog-event="View Listing"]'

MAX_LISTINGS = 500  # Maximum listings to retrieve

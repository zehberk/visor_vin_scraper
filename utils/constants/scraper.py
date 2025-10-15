from pathlib import Path
import re

PRESET_PATH = Path(__file__).parent.parent / "presets" / "presets.json"
LISTINGS_PATH = Path(__file__).parent.parent / "output" / "raw"
DOC_PATH = Path(__file__).parent.parent / "output" / "vehicles"

# URL strings
BASE_URL = "https://visor.vin/search/listings"
VIN_DETAILS_URL = BASE_URL + "/{vin}"

CONDITIONS = {"New", "Used", "Certified"}  # Vehicle conditions
SORT_OPTIONS = {
    "Lowest Price": "cheapest",
    "Highest Price": "expensive",
    "Newest": "newest",
    "Oldest": "oldest",
    "Lowest Mileage": "lowest_miles",
    "Highest Mileage": "highest_miles",
}
PARAM_NAME_OVERRIDES = {  # For user-friendly parameter names
    "condition": "car_type",
    "min_miles": "miles_min",
    "max_miles": "miles_max",
    "min_price": "price_min",
    "max_price": "price_max",
}
MAX_LISTINGS = 500  # Maximum listings to retrieve

# HTML element selectors for main listing page
NO_LISTINGS_FOUND_TEXT = "No listings to see"
LISTING_CARD_SELECTOR = "a[href^='/search/listings/']"
TITLE_ELEMENT = "h2"
PRICE_ELEMENT = "div.absolute.bottom-2.left-2 > span"
CONDITION_ELEMENT = (
    "div.inline-flex.items-center.border.font-semibold.transition-colors.text-white"
)
MILEAGE_ELEMENT = "div.flex.flex-row.gap-x-2 > div.text-sm"
SCROLL_CONTAINER_SELECTOR = "div.h-dvh.overflow-y-auto"

# HTML element selector for detail page
DETAIL_PAGE_ELEMENT = "div.h-dvh.w-full.space-y-3.overflow-y-auto"
# HTML element selectors for warranty
WARRANTY_STATUS_TEXT_ELEMENT = "div.text-base.text-black"
COVERAGE_ELEMENTS = "div.grid.grid-cols-1.gap-6 div.bg-\\[\\#F6F6F6\\].p-3.space-y-3"
COVERAGE_TYPE_ELEMENT = "div.bg-\\[\\#3B3B3B\\]"
COVERAGE_STATUS_ELEMENT = "div.text-sm > div.text-sm"
COVERAGE_LIMIT_ELEMENTS = "div.space-y-1"
COVERAGE_LIMIT_VALUES_ELEMENTS = "div.flex.justify-between div.text-sm"
AUTOCHECK_URL_ELEMENT = 'a[data-posthog-event="View AutoCheck Report"]'
CARFAX_URL_ELEMENT = 'a[data-posthog-event="View Carfax Report"]'
WINDOW_STICKER_URL_ELEMENT = 'a[data-posthog-event="View Window Sticker"]'
# HTML element selectors for seller
LISTING_URL_ELEMENT = 'a[data-posthog-event="View Listing"]'
SELLER_BLOCK_ELEMENT = "td.p-3.border-input.align-middle.space-y-1\\.5 div.space-y-2"
SELLER_NAME_ELEMENT = "div.order-2"
GOOGLE_MAP_ELEMENT = 'a[data-posthog-event="Google Maps"]'
BUTTON_ELEMENTS = 'button[data-slot="tooltip-trigger"]'
STOCK_NUM_ELEMENT = "div > div"
PHONE_NUM_ELEMENT = "div"
# HTML element selectors for market velocity
VELOCITY_ELEMENTS = "div.space-y-4 div.grid.gap-4 div.bg-\\[\\#F6F6F6\\]"
VELOCITY_SECTION_ELEMENTS = "div.space-y-4 div.grid.gap-4 div.bg-\\[\\#F6F6F6\\]"
VEHICLE_SOLD_ELEMENT = "div.text-lg"
DAYS_ON_MARKET_ELEMENT = "div.flex div.text-sm.font-medium"
DEMAND_ELEMENT = "div.text-lg.font-medium"
# HTML element selectors for vehicle specs
SPEC_TABLE_ELEMENT = "tbody.w-full"
SPEC_ROW_ELEMENTS = "tbody.w-full > tr"
# HTML element selectors for add-ons
ADDON_LI_ELEMENTS = "ul.list-disc.list-inside > li"
# HTML element selectors for price history
PRICE_HISTORY_ELEMENT = "div.space-y-3.pt-3.w-full"
PRICE_CHANGE_ELEMENTS = "div.flex.items-center.justify-between.text-base"

# Regex
TOTAL_NATIONWIDE_REGEX = re.compile(r"(\d[\d,]*) for sale nationwide")
ADDON_REGEX = re.compile(r"^(.*?)[\u00a0 ]*\(\$(\d[\d,]*)\)")
PRICE_CHANGE_REGEX = re.compile(r"(-?\$[\d,]+)")
PRICE_MATCH_REGEX = re.compile(r"\$([\d,]+)")
MILES_MATCH_REGEX = re.compile(r"([\d,]+)")

# Cache file paths
LISTINGS_CACHE = Path("cache") / "listings.cache"

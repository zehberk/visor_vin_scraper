
# --- Constants ---

# String constants for the scraper
BASE_URL = "https://visor.vin/search/listings"

CONDITIONS = {"New", "Used", "Certified"} # Vehicle conditions
SORT_OPTIONS = {
    "Best Match": "best_match",
    "Lowest Price": "cheapest",
    "Highest Price": "expensive",
    "Newest Listings": "recent",
    "Oldest Listings": "oldest",
    "Lowest Mileage": "lowest_miles",
    "Highest Mileage": "highest_miles",
}
PARAM_NAME_OVERRIDES = {        # For user-friendly parameter names
    "condition": "car_type",
    "min_miles": "miles_min",
    "max_miles": "miles_max",
    "min_price": "price_min",
    "max_price": "price_max",
    # Add more if needed
}

# Remapping constants for query parameters
# This allows for more user-friendly input while maintaining the correct URL parameters
REMAPPING_RULES = {
    "sort": SORT_OPTIONS,
    "condition": lambda values: ",".join(v.lower() for v in values)
}

# HTML element selectors
HREF_ELEMENT = "a[href^='/search/listings/']"
LISTING_COUNT_ELEMENT = "span.text-xl.text-gray-600"
TITLE_ELEMENT = "h2"
PRICE_ELEMENT = "div.absolute.bottom-2.left-2 span"
TEXT_SM_BLOCKS = "div.text-sm"
LOCATION_ELEMENT = "div.flex.items-start span"
SCROLL_CONTAINER_SELECTOR = "div.h-dvh.overflow-y-auto"

MAX_LISTINGS = 500  # Maximum listings to retrieve

import re

from datetime import timedelta
from pathlib import Path

KBB_CAR_PRICES_URL = "https://www.kbb.com/car-prices/"
KBB_WHATS_MY_CAR_WORTH_URL = "https://www.kbb.com/whats-my-car-worth/"
KBB_LOOKUP_BASE_URL = "https://kbb.com/{make}/{model}/{year}/"
KBB_LOOKUP_STYLES_URL = KBB_LOOKUP_BASE_URL + "styles/?intent=trade-in-sell&mileage=1"
KBB_LOOKUP_TRIM_URL = KBB_LOOKUP_BASE_URL + "/{trim}/"


KBB_VARIANT_CACHE = cache_path = Path("cache") / "kbb.cache"
PRICING_CACHE = Path("cache") / "pricing.cache"
ANALYSIS_CACHE = Path("cache") / "analysis.cache"
CACHE_TTL = timedelta(days=7)

BAD_STRINGS = {"", "unavailable", "n/a", "none", "null", "-", "not specified"}
DRIVETRAINS = {"4x4", "4wd", "2wd", "4xe", "awd", "rwd"}
ENGINE_DISPLACEMENT_RE = re.compile(
    r"\b"  # start at a word boundary
    r"(\d+(?:\.\d+)?)"  # displacement number: integer or decimal (e.g. 2 or 2.5)
    r"(?:L|cc)"  # unit: liters (L) or cubic centimeters (cc)
    r"\b",  # end at a word boundary
    re.IGNORECASE,  # allow l/L/cc/CC
)
BED_LENGTH_RE = re.compile(
    r"\b([3-9])"  # feet, restricted to 3–9
    r"(?:[\.\-]?\d\/\d)?"  # optional fraction (e.g. -1/2, .1/4)
    r"(?:\.\d+)?"  # or optional decimal (e.g. 6.5)
    r"\s?(?:ft|['’])"  # ft, ' or ’
    r"(?:\s?(\d{1,2})\")?",  # optional inches (e.g. 7")
    re.IGNORECASE,
)
BODY_STYLE_RE = re.compile(
    r"\b("  # leading space
    r"Sedan|Coupe|Hatchback|"
    r"Sport Utility|Sport Utility Vehicle|SUV|"
    r"Crew Cab|Extended Cab|SuperCrew Cab|"
    r"Van|Wagon|Pickup"  # body style group
    r")\s+(\dD)\b",  # door count, e.g. 2D, 4D
    re.IGNORECASE,
)
BODY_STYLE_ALIASES = {"SUV 4D": "Sport Utility Vehicle 4D"}


DEAL_ORDER = ["Great", "Good", "Fair", "Poor", "Bad"]
COND_ORDER = ["New", "Certified", "Used"]

# Level 3 regex for dealer fees
FEE_KEYWORDS = [
    r"dealer fee",
    r"dealer fees",
    r"doc fee",
    r"doc fees",
    r"document fee",
    r"document fees",
    r"documentation fee",
    r"documentation fees",
    r"dealer doc fee",
    r"dealer document fee",
    r"dealer documentation fee",
    r"dealer transfer services fees",
    r"dealer and handling fees",
    r"dealer and handling fee",
]
FEE_PATTERN = re.compile("|".join(rf"{kw}" for kw in FEE_KEYWORDS), re.I)


NO_FEE_RE = re.compile(
    r"\bno\s+(?:dealer|doc(?:ument(?:ation)?)?)\s*fees?\b"
    r"|\bzero\s+dealer\s*fees?\b",
    re.I,
)

FEE_PHRASE_RE = re.compile(
    r"|".join(rf"\b{kw}\b" for kw in FEE_KEYWORDS),
    re.I,
)

AMOUNT_RE = re.compile(
    r"|".join(
        [
            # phrase first, amount after
            rf"{kw}[^$]{{0,50}}\$\s*([0-9][0-9,]*(?:\.\d{{2}})?)"
            for kw in FEE_KEYWORDS
        ]
        + [
            # amount first, phrase after
            rf"\$\s*([0-9][0-9,]*(?:\.\d{{2}})?)\s*.{{0,50}}{kw}"
            for kw in FEE_KEYWORDS
        ]
    ),
    re.I,
)

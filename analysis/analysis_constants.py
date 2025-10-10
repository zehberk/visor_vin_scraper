import re

from datetime import timedelta
from pathlib import Path

KBB_VARIANT_CACHE = cache_path = Path("cache") / "kbb.cache"
PRICING_CACHE = Path("cache") / "level1_pricing.cache"
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

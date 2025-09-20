BAD_STRINGS = {"", "unavailable", "n/a", "none", "null"}


def bool_from_url(val: str | None) -> bool:
    """True if a usable URL string appears present (not 'Unavailable'/empty/None)."""
    if not val:
        return False
    s = str(val).strip().lower()
    return s not in {"", "unavailable", "n/a", "none", "null"}


def percentile(values: list[int], p: float) -> float:
    """Inclusive-linear percentile; p in [0,1]."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    i = p * (len(s) - 1)
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    frac = i - lo
    return s[lo] * (1 - frac) + s[hi] * frac


# Price/mileage may be strings like "$32,500" or "52,025 mi"
def to_int(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    chars = "".join(ch for ch in str(val) if ch.isdigit())
    return int(chars) if chars else None


def get_relevant_entries(
    cache: dict, make: str, model: str, year=""
) -> dict[str, dict]:
    model_key = f"{year} {make} {model}" if year else f"{make} {model}"
    hybrid_model_key = f"{year} {make} {model}" if year else f"{make} {model}"
    return {k: v for k, v in cache.items() if model_key in k or hybrid_model_key in k}


def is_trim_version_valid(trim_version: str) -> bool:
    if not trim_version or trim_version.strip().lower() in BAD_STRINGS:
        return False
    return any(c.isalnum() for c in trim_version)

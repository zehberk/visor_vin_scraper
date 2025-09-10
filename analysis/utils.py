def bool_from_url(val: str | None) -> bool:
    """True iff a usable URL string appears present (not 'Unavailable'/empty/None)."""
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


def money_to_int(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip()
    if "—" in s or "N/A" in s or s == "":
        return None
    num = "".join(ch for ch in s if ch.isdigit())
    return int(num) if num else None

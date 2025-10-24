from datetime import datetime
from typing import Optional

from utils.models import CarListing, DealBin


DEAL_ORDER = ["Great", "Good", "Fair", "Poor", "Bad"]
COND_ORDER = ["New", "Certified", "Used"]


def rate_uncertainty(listing) -> str:
    report_present = listing["report_present"]
    window_sticker_present = listing["window_sticker_present"]
    warranty_info_present = listing["warranty_info_present"]

    if not report_present and not window_sticker_present and not warranty_info_present:
        return "High"
    elif not report_present and window_sticker_present and warranty_info_present:
        return "Some"
    else:
        return "Low"


def rate_deal(
    price: int,
    delta: int,
    compare_price: int,
    fpp_local: int,
    fmr_low: int,
    fmr_high: int,
) -> str:
    if price == 0:
        return "No price"

    # --- Case 1: Use fair-market range if available ---
    if compare_price == fpp_local:
        increment = fmr_high - fpp_local

        if price < fmr_low - increment:
            return "Great"
        elif fmr_low - increment <= price < fmr_low:
            return "Good"
        elif fmr_low <= price <= fmr_high:
            return "Fair"
        elif fmr_high + increment >= price > fmr_high:
            return "Poor"
        else:
            return "Bad"

    # --- Case 2: Fall back to delta/ratio logic ---
    if delta < -2000 or price <= compare_price * 0.93:
        return "Great"
    elif (-2000 <= delta < -1000) or (
        compare_price * 0.93 < price <= compare_price * 0.97
    ):
        return "Good"
    elif (-1000 <= delta <= 1000) or (
        compare_price * 0.97 < price < compare_price * 1.03
    ):
        return "Fair"
    elif (2000 >= delta > 1000) or (
        compare_price * 1.03 <= price < compare_price * 1.07
    ):
        return "Poor"
    else:
        return "Bad"


def rate_risk(listing, price, fmv) -> str:
    year = int(listing["title"][:4])
    avg_miles_per_day = 13500 / 365
    est_days_since_manufacture = (datetime.now() - datetime(year, 1, 1)).days
    expected_miles = est_days_since_manufacture * avg_miles_per_day
    mileage = int(listing["mileage"])
    if price == 0:
        return "Unknown"
    if (mileage >= expected_miles * 1.35) or (
        mileage >= expected_miles * 1.2 and price >= fmv * 1.1
    ):
        return "High"
    elif (mileage >= expected_miles * 1.2) or (price >= fmv * 1.1):
        return "Some"
    else:
        return "Low"


def deviation_pct(
    price: int | float, compare_price: int | float | None
) -> Optional[float]:
    if compare_price and compare_price > 0 and isinstance(price, (int, float)):
        return (price - compare_price) / compare_price
    return None


def build_bins_and_crosstab(listings: list[CarListing]) -> tuple[list[DealBin], dict]:
    """
    Returns (deal_bins:list[DealBin], crosstab:dict)
    - deal_bins includes avg_deviation_pct, condition_counts, percent_of_total
    - crosstab is a nested dict: {bin: {condition: count}}
    """
    # totals
    total = 0
    for row in listings:
        if row.deviation_pct is None:
            row.deviation_pct = deviation_pct(row.price, row.compare_price)
        total += 1

    # group by bin
    by_bin: dict[str, list[CarListing]] = {k: [] for k in DEAL_ORDER}
    for row in listings:
        if row.deal_rating in by_bin:
            by_bin[row.deal_rating].append(row)

    # cross-tab counts
    crosstab: dict[str, dict[str, int]] = {
        b: {c: 0 for c in COND_ORDER} for b in DEAL_ORDER
    }
    for row in listings:
        if row.deal_rating in DEAL_ORDER and row.condition in COND_ORDER:
            crosstab[row.deal_rating][row.condition] += 1

    # build DealBin objects with summaries
    deal_bins: list[DealBin] = []
    for b in DEAL_ORDER:
        items = by_bin[b]
        count = len(items)

        # avg deviation (only valid numbers)
        sum_dev = 0.0
        n_dev = 0
        for r in items:
            if isinstance(r.deviation_pct, (int, float)):
                sum_dev += r.deviation_pct
                n_dev += 1
        avg_dev = (sum_dev / n_dev) if n_dev else None

        # condition breakdown for this bin
        cond_counts = {c: crosstab[b][c] for c in COND_ORDER}

        deal_bins.append(
            DealBin(
                category=b,
                listings=items,
                count=count,
                avg_deviation_pct=avg_dev,
                condition_counts=cond_counts,
                percent_of_total=(count / total * 100.0) if total else 0.0,
            )
        )

    return deal_bins, crosstab


def compute_condition_distribution_total(
    all_listings: list[CarListing],
    no_price_bin: DealBin | None = None,
) -> dict[str, int]:
    counts = {c: 0 for c in COND_ORDER}

    def bump(c: str | None):
        c = c if c in counts else "Used"  # keep matrix tidy
        counts[c] += 1

    for r in all_listings:
        bump(getattr(r, "condition", None))

    if no_price_bin:
        for r in no_price_bin.listings:
            bump(getattr(r, "condition", None))

    return counts

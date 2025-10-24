import re

from datetime import datetime
from typing import Optional

from analysis.utils import to_int
from utils.models import (
    CarfaxData,
    CarListing,
    DamageSeverity,
    DealBin,
    StructuralStatus,
)


DEAL_ORDER = ["Great", "Good", "Fair", "Poor", "Bad"]
COND_ORDER = ["New", "Certified", "Used"]


SEVERITY_SCORES: dict[DamageSeverity, float] = {
    DamageSeverity.MINOR: 1.0,
    DamageSeverity.MODERATE: 2.5,
    DamageSeverity.SEVERE: 5.0,
}
STRUCTURAL_SCORES: dict[StructuralStatus, float] = {
    StructuralStatus.NONE: 0.0,
    StructuralStatus.POSSIBLE: 1.0,
    StructuralStatus.CONFIRMED: 2.5,
}


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


def rate_risk_level1(listing, price, fmv) -> str:
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


def rate_risk_level2(carfax: CarfaxData, listing: dict) -> float:
    score: float = score_title_status(carfax)
    score += score_mileage_use(carfax, listing)
    score += score_warranty_status(carfax, listing)
    return score


def score_title_status(carfax: CarfaxData) -> float:
    """
    Computes a composite title risk score on a 0-10 scale based on title type, structural status,
    damage history, and key risk factors (airbags, odometer).
    """
    score = 0.0

    # 1. Base damage severity (non-linear, cumulative
    damage_score = get_cumulative_damage_score(carfax.damage_severities)

    # 2. Title-related risk (clean vs branded/total loss)
    score = get_branded_score(carfax.is_branded, carfax.is_total_loss, damage_score)

    # 3. Structural risk, scaled down to reduce overlap with title weighting
    score += get_structure_score(carfax.structural_status, damage_score)

    # 4. Airbag deployment: hidden safety concern, flat addition
    if carfax.airbags_deployed:
        score += 2.5

    return min(score, 10.0)


def get_cumulative_damage_score(severities: list[DamageSeverity]) -> float:
    """
    Returns a cumulative score for a list of damages. Subsequent damages are multipled by 10%,
    with an additional 5% for each after the second damage event. This will return a max of 10
    """
    score = 0.0
    for i, damage in enumerate(severities):
        base: float = SEVERITY_SCORES.get(damage, 0.0)
        multiplier = 1 if i == 0 else 1.1 + (0.05 * (i - 1))
        score += base * multiplier
    return min(score, 10.0)


def get_structure_score(status: StructuralStatus, damage_score: float) -> float:
    """
    Returns a structure score modified by the likelihood of damage on a non-linear scale.
    Values typically range from ~0.25 to 2.5 before scaling.
    POSSIBLE statuses are scaled with damage; CONFIRMED remains fixed.
    """
    score = STRUCTURAL_SCORES.get(status, 0.0)
    if status == StructuralStatus.POSSIBLE:
        if damage_score <= 0:
            return 0.0
        scale = (damage_score / 10) ** 1.2
        score = 0.1 + scale * (2.5 - 0.1)

    # Scale down to give more breathing room with the title
    return score * 0.7


def get_branded_score(
    is_branded: bool, is_total_loss: bool, damage_score: float
) -> float:
    """
    Returns a scaled title score depending on the vehicle's damage. Values range from 0-9.

                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    Scoring logic:
    - No title issues, no damage: 0
    - No title issues with damage present: 2.25 → 7.5 (nonlinear)
    - Title issues, no damage: 7
    - Title issues with damage present 4 → 9 (nonlinear)
    """
    if not (is_branded or is_total_loss):
        if damage_score <= 0:
            return 0.0
        else:
            scale = (damage_score / 10) ** 0.78
            return 2.0 + scale * 5.5  # clean-title curve: 2.25 → 7.5

    if damage_score <= 0:
        return 7.0  # suspicious: title issue with no visible damage

    scale = ((damage_score / 10) ** 1.3) * 1.05
    return 4.0 + min(scale, 1.0) * 5.0  # branded curve: 4 → 9


def score_warranty_status(carfax: CarfaxData, listing: dict) -> float:
    """
    Finds the rating score for a vehicle's warranty status. Range is -2 to 0.
    Having the original bumper-to-bumper/basic warranty still active will reward the highest score.
    """
    basic_months: int = 0
    basic_miles: int = 0
    coverages: list[dict] = listing.get("warranty", {}).get("coverages", [])

    if carfax.has_accident or carfax.has_damage:
        return 0.0

    if carfax.is_basic_warranty_active:
        basic_months, basic_miles = carfax.remaining_warranty

    if basic_months == 0 or basic_miles == 0:
        basic = next((c for c in coverages if c.get("type") == "Basic"), None)
        if basic and basic.get("status", "") != "Fully expired":
            time_left = basic.get("time_left", "")
            year_pattern = re.compile(r"(\d+)\s+yr")
            match = year_pattern.search(time_left)
            years = to_int(match[0] if match else 0)

            month_pattern = re.compile(r"(\d+)\s+mo")
            match = month_pattern.search(time_left)
            months = to_int(match[0] if match else 0)
            basic_months = (years * 12) + months if years and months else 0

            miles_nums = to_int(basic.get("miles_left", ""))
            basic_miles = miles_nums * 1000 if miles_nums else 0

    if basic_months > 12 and basic_miles > 12000:
        rating = -2.0
    elif basic_months > 12 or basic_miles > 12000:
        rating = -1.5
    elif basic_months > 6 or basic_miles > 6000:
        rating = -1.0
    elif basic_months > 0 and basic_miles > 0:
        rating = -0.5
    else:
        rating = 0.0

    return rating


def score_mileage_use(carfax: CarfaxData, listing: dict) -> float:
    """
    Calculates a mileage-based risk modifier on a -1.0 to 2.0 scale.

    The score reflects how far the vehicle's mileage deviates from the expected
    use, assuming an average of 13,500 miles driven per year.

    Logic:
    - < -20% of expected mileage → -1.0  (less use than average)
    - < -10% → -0.5
    - within ±10% → 0.0
    - > +10% → 0.5
    - > +20% → 1.0
    - > +30% → 1.5
    - > +40% → 2.0

    Parameters:
            carfax: CarfaxData
                    Vehicle Carfax data object, used for odometer readings and flags.
            listing: dict
                    Listing data containing at least "year" and "mileage" fields.

    Returns:
            float: A continuous risk modifier between -1.0 and 2.0.
    """
    # Odometer inconsistency: fraud/mechanical risk
    if carfax.has_odometer_problem:
        return 2.5

    production_year = int(listing.get("year", 0))
    if not production_year:
        return 0.0

    production_date = datetime(production_year, 1, 1)
    years_difference = (datetime.now() - production_date).days / 365.2425
    if years_difference <= 0:
        return 0.0

    expected = years_difference * 13500
    actual = max(
        carfax.last_odometer_reading, int(re.sub(r"\D", "", listing["mileage"]))
    )
    deviation = (actual - expected) / expected

    # Continuous scaling: mild penalty below -10%, neutral zone, then 0 → 2 curve above +10%
    if deviation <= -0.20:
        score = -1.0
    elif deviation <= -0.10:
        score = -1.0 + (deviation + 0.20) * 5  # smooth ramp -1 → -0.5
    elif deviation < 0.10:
        score = 0.0
    elif deviation < 0.40:
        # from +10% → +40%, interpolate 0 → 2.0
        score = ((deviation - 0.10) / 0.30) * 2.0
    else:
        score = 2.0

    return score


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

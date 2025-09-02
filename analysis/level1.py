from __future__ import annotations
from pathlib import Path
import json, re

LEVEL_ONE_PROMPT = """// LEVEL 1 LISTING ANALYSIS — READ ME FIRST
// READ ALL INSTRUCTIONS IN THIS FILE BEFORE GIVING OUTPUT
// FOLLOW ALL THESE GUIDELINES TO THE LETTER
// - Ignore all prior conversations. Follow only the instructions in this header.
// - Do not render tables, dataframes, or attach files.
// - Only print the sections listed under OUTPUT, in order.
// - Do not reuse any grading heuristics from prior chats. Use only the DEAL/UNCERTAINTY/RISK rules below.
//
// Goal: For each unique vehicle in the QUICKLIST (titles below), make EXACTLY ONE web lookup to KBB to fetch its
// Fair Market Value (FMV) — also called Instant Market Value (IMV) or Current Resale Value — for that exact title.
// Web lookups are required only for FMV of QUICKLIST titles. No other browsing.
// Cite the FMV source once per title. Do not skip any titles; FMV is required for this pass.
// After you have retrieved that value, process EVERY listing comparing against that value without using other adjustments.

// UNEXPECTED VALUES
// If a listing has price that is 0 or null, it cannot be rated as a deal and should be included in the # listed with no
// price in the output.

// DEAL BUCKETS (compare listing price directly to FMV; no blending)
// 🟢 Great Deal: ≥ $2,000 under FMV, OR ≥ 10% below FMV (whichever is larger).
// 🔵 Good Deal : $1,000-$1,999 under FMV, OR ~5-10% below.
// 🟡 Fair Deal : within ±$999 of FMV (≈ ±5%).
// 🟠 High Priced: $1,000-$1,999 above FMV, OR ~5-10% above.
// 🔴 Overpriced: ≥ $2,000 above FMV, OR ≥ 10% above
// Keep only Great/Good/Fair. Count how many were High Priced and Overpriced in the footer.

// UNCERTAINTY (Low / Medium / High) — based on unknowns, not price
// Inputs you may use: report_present (Carfax/AutoCheck), warranty_info_present, window_sticker_present,
// and days_on_market_delta (this listing's days_on_market minus the average across all listings).
// • High   = report_present = false  OR  days_on_market_delta ≥ 30.
// • Medium = report_present = true but warranty_info_present = false  OR  window_sticker_present = false  OR  |days_on_market_delta| < 30.
// • Low    = report_present = true AND (warranty_info_present = true OR window_sticker_present = true) AND days_on_market_delta ≤ 30.

// RISK (Low / Some / High) — quick read on potential downsides
// Expected mileage per year is 12,500. You can assume that this car was available to drive on the January 1st of it's year.
// High: mileage ≥ 35% over expected (regardless of price), or price ≥10% under FMV and mileage ≥20% over expected.
// Some: mileage 20-35% over expected, or price ≥10% under FMV (but mileage <20%).
// Low: otherwise.

// OUTPUT
// • Do NOT ask to confirm. Do NOT render tables, dataframes, or attach files.
// • Allowed sections ONLY, in this exact order:
//
//   1) "# Fair Market Value"
//      - One line per QUICKLIST title: "<title> (<N listings>) — $XX,XXX current resale value. [source]"
//
//   2) "🟢 Great Deals"
//   3) "🔵 Good Deals"
//   4) "🟡 Fair Deals"
//
//   – For each kept listing, one line (no bullets):
//     "#<id> • VIN <vin> • <title> • $<price> • <miles> mi • <$X under/over market> • Uncertainty: <Low/Medium/High> • Risk: <Low/Some/High>"
//   – Each kept listing must match this exact line template. No extra fields, no bullets.
//   – If the listing has null miles, it can be treated as 0 miles.
//   – Within each bin, order the listings by <id>
//
//   5) Footer (single line):
//      “Filtered out: 🟠 # High Priced, 🔴 # Overpriced, ❔ # with no listed price”
//
// • Do NOT invent other sections or Names for grading (e.g., Excellent, Uncertain).
// • Do NOT include listings that are deemed High Price, Overpriced, or who don't have a price
// • Final self-check: 
//    – Output must contain exactly the 4 headings above and exactly 1 footer line—no extra paragraphs, tables, or summaries.
//    – All listings thaat are Great, Good, or Fair deals must be displayed.

// VERIFICATION
// If the total counts for the QUICKLIST lines do not match the amount of listings binned, try again.

// DATA YOU WILL RECEIVE
// {
//   "listings": [
//     {
//       "id": 1,							// The id for the listing
//       "vin": "1C6RREJT9RN219453",
//       "title": "2024 RAM 1500 Laramie",	// Year make model and trim of the car
//       "price": 32500,
//       "mileage": 52025,
//       "days_on_market_delta": 15,		// How many days over or under the average listing time
//       "report_present": false,			// Is the Carfax or AutoCheck available?
//       "warranty_info_present": false,	// Do we have warranty info available?
//       "sticker_present": true			// Do we have a window sticker?
//     }
//   ]
// }
"""


def _bool_from_url(val: str | None) -> bool:
    """True iff a usable URL string appears present (not 'Unavailable'/empty/None)."""
    if not val:
        return False
    s = str(val).strip().lower()
    return s not in {"", "unavailable", "n/a", "none", "null"}


def _price_history_lowest(price_history: list[dict] | None) -> bool:
    """True if any entry marks lowest=True."""
    if not price_history:
        return False
    for p in price_history:
        try:
            if bool(p.get("lowest")):
                return True
        except Exception:
            pass
    return False


def _days_on_market(listing: dict) -> int | None:
    """Pull DOM from common locations."""
    # Preferred: nested velocity block
    try:
        dom = listing.get("market_velocity", {}).get("this_vehicle_days")
        avg = listing.get("market_velocity", {}).get("avg_days_on_market")
        return int(dom) - int(avg) if dom is not None and avg is not None else None
    except Exception:
        pass
    return None


def build_quicklist(slimmed: list[dict], make: str, model: str) -> str:
    """
    Returns a comment block listing unique year+trim combos, e.g.:
    // UNIQUE YEAR+TRIM (deduped)
    // 2024 RAM 1500 Laramie RWD
    // 2025 RAM 1500 Laramie 4WD
    """

    def _year_key(t: str) -> int:
        m = re.match(r"^\s*(\d{4})\b", t)
        return int(m.group(1)) if m else 9999

    titles = [str(l.get("title", "")) for l in slimmed if l.get("title")]
    unique = sorted(set(titles), key=lambda t: (_year_key(t), t.lower()))
    lines = ["//", "// QUICKLIST - unique (year+trim) for one-call lookups"]
    lines += [f"// {title}" for title in unique]
    return "\n".join(lines) + "\n"


def _slim(listing: dict) -> dict:
    """Convert a raw listing into the minimal Level-1 schema."""

    # Price/mileage may be strings like "$32,500" or "52,025 mi"
    def _to_int(val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return int(val)
        chars = "".join(ch for ch in str(val) if ch.isdigit())
        return int(chars) if chars else None

    addl = listing.get("additional_docs", {}) or {}
    carfax_present = _bool_from_url(addl.get("carfax_url"))
    autocheck_present = _bool_from_url(addl.get("autocheck_url"))
    sticker_present = _bool_from_url(addl.get("window_sticker_url"))

    war = listing.get("warranty", {}) or {}
    # Treat "present" as: either a non-unknown overall_status or any coverages listed
    warranty_present = bool(war.get("coverages")) or (
        str(war.get("overall_status", "")).strip().lower()
        not in {"", "unknown", "n/a", "none"}
    )

    return {
        "id": listing.get("id"),
        "vin": listing.get("vin"),
        "title": listing.get("title"),
        "price": _to_int(listing.get("price")),
        "mileage": _to_int(listing.get("mileage")),
        "days_on_market_delta": _days_on_market(listing),
        "price_history_lowest": _price_history_lowest(listing.get("price_history")),
        "report_present": carfax_present or autocheck_present,
        "window_sticker_present": sticker_present,
        "warranty_info_present": warranty_present,
    }


def create_level1_file(
    listings: list[dict], metadata: dict, args, timestamp: str
) -> Path:
    """
    Builds 'level1_input_<Make>_<Model>_<Timestamp>.jsonc' next to your outputs.
    Returns the file path.
    Call this AFTER you've saved listings.json and closed the browser.
    """
    if not listings:
        raise ValueError("No listings provided to create_level1_file().")

    # Derive make/model from args when available; else from first listing/title.
    make = getattr(args, "make", None) or listings[0].get("make") or "Unknown"
    model = getattr(args, "model", None) or listings[0].get("model") or "Unknown"

    # Slim all listings
    slimmed = [_slim(l) for l in listings if l is not None]
    payload = {"listings": slimmed}

    # Output pathing
    out_dir = Path("output") / "level1"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"level1_{make}_{model}_{timestamp}.json"

    # Write header (comments) + JSON data
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return out_path

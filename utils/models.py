import re

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from utils.constants import (
    BED_LENGTH_RE,
    BODY_STYLE_ALIASES,
    BODY_STYLE_RE,
    DRIVETRAINS,
    ENGINE_DISPLACEMENT_RE,
)


@dataclass
class TrimValuation:
    model: str
    kbb_trim: str

    # Values pulled from the model-level page (national baseline)
    msrp: int
    fpp_natl: int

    # Values pulled from the trim-level page (localized)
    fmr_low: int
    fmr_high: int
    fpp_local: int
    fmv: int

    # Only two sources — one for each page type
    natl_source: str
    local_source: str

    def __repr__(self):
        return (
            f"TrimValuation(model={self.model}, "
            f"kbb_trim={self.kbb_trim!r}, "
            f"msrp={self.fmv}, "
            f"fpp_natl={self.fpp_natl}, "
            f"fmr_low={self.fmr_low}, "
            f"fmr_high={self.fmr_high}, "
            f"fpp_local={self.fpp_local}, "
            f"fmv={self.fmv}, "
            f"model_source={self.natl_source!r})"
            f"trim_source={self.local_source!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "kbb_trim": self.kbb_trim,
            "msrp": self.msrp,
            "fpp_natl": self.fpp_natl,
            "fmr_low": self.fmr_low,
            "fmr_high": self.fmr_high,
            "fpp_local": self.fpp_local,
            "fmv": self.fmv,
            "natl_source": self.natl_source,
            "local_source": self.local_source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrimValuation":
        return cls(
            model=data["model"],
            kbb_trim=data["kbb_trim"],
            msrp=data["msrp"],
            fpp_natl=data["fpp_natl"],
            fmr_low=data["fmr_low"],
            fmr_high=data["fmr_high"],
            fpp_local=data["fpp_local"],
            fmv=data["fmv"],
            natl_source=data["natl_source"],
            local_source=data["local_source"],
        )


@dataclass
class CarListing:
    id: str
    vin: str
    year: int
    make: str
    model: str
    trim: str
    trim_version: str
    title: str
    cache_key: str
    condition: str  # "New" | "Used" | "Certified"
    miles: int
    price: int
    price_delta: int
    uncertainty: str
    risk: str
    msrp: int
    fpp: int
    fmv: int
    compare_price: int
    deal_rating: Optional[str] = (
        None  # "Great" | "Good" | "Fair" | "Poor" | "Bad" | None if no price
    )
    deviation_pct: Optional[float] = None  # signed; negative = under FMV

    def __repr__(self):
        return (
            f"CarListing(id={self.id!r}, "
            f"vin={self.vin!r}, "
            f"year={self.year}, "
            f"make={self.make}, "
            f"model={self.model}, "
            f"trim={self.trim}, "
            f"trim_version={self.trim_version}, "
            f"title={self.title}, "
            f"cache_key={self.cache_key}, "
            f"condition={self.condition}, "
            f"miles={self.miles!r}, "
            f"price={self.price!r}, "
            f"price_delta={self.price_delta!r}, "
            f"uncertainty={self.uncertainty!r}, "
            f"risk={self.risk!r}, "
            f"msrp={self.msrp!r}, "
            f"fpp={self.fpp!r}, "
            f"fmv={self.fmv!r}, "
            f"compare_price={self.compare_price!r}, "
            f"deal_rating={self.deal_rating!r}, "
            f"deviation_pct={self.deviation_pct!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "vin": self.vin,
            "year": self.year,
            "make": self.make,
            "model": self.model,
            "trim": self.trim,
            "trim_version": self.trim_version,
            "title": self.title,
            "cache_key": self.cache_key,
            "condition": self.condition,
            "miles": self.miles,
            "price": self.price,
            "price_delta": self.price_delta,
            "uncertainty": self.uncertainty,
            "risk": self.risk,
            "msrp": self.msrp,
            "fpp": self.fpp,
            "fmv": self.fmv,
            "compare_price": self.compare_price,
            "deal_rating": self.deal_rating,
            "deviation_pct": self.deviation_pct,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CarListing":
        return cls(
            id=data["id"],
            vin=data["vin"],
            year=data["year"],
            make=data["make"],
            model=data["model"],
            trim=data["trim"],
            trim_version=data["trim_version"],
            title=data["title"],
            cache_key=data["cache_key"],
            condition=data["condition"],
            miles=data["miles"],
            price=data["price"],
            price_delta=data["price_delta"],
            uncertainty=data["uncertainty"],
            risk=data["risk"],
            msrp=data["msrp"],
            fpp=data["fpp"],
            fmv=data["fmv"],
            compare_price=data["compare_price"],
            deal_rating=data["deal_rating"],
            deviation_pct=data["deviation_pct"],
        )


@dataclass
class DealBin:
    category: str
    listings: list[CarListing]
    count: int
    avg_deviation_pct: Optional[float] = None
    condition_counts: Dict[str, int] = field(default_factory=dict)  # {"New": 2, ...}
    percent_of_total: Optional[float] = None  # 0..100

    @property
    def new_listings_count(self) -> int:
        return sum(1 for l in self.listings if l.condition == "New")

    @property
    def new_listings_pct(self) -> float:
        if len(self.listings) == 0:
            return 0.0
        return self.new_listings_count / len(self.listings)

    @property
    def certified_listings_count(self) -> int:
        return sum(1 for l in self.listings if l.condition == "Certified")

    @property
    def certified_listings_pct(self) -> float:
        if len(self.listings) == 0:
            return 0.0
        return self.certified_listings_count / len(self.listings)

    @property
    def used_listings_count(self) -> int:
        return sum(1 for l in self.listings if l.condition == "Used")

    @property
    def used_listings_pct(self) -> float:
        if len(self.listings) == 0:
            return 0.0
        return self.used_listings_count / len(self.listings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "count": self.count,
            "percent_of_total": self.percent_of_total,
            "avg_deviation_pct": self.avg_deviation_pct,
            "condition_counts": self.condition_counts,
            "listings": [listing.to_dict() for listing in self.listings],
        }


@dataclass
class TrimProfile:
    engine: str | None
    bed_length: str | None
    drivetrain: str | None
    body_style: str | None
    tokens: list[str]
    full_trim: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "full_trim": self.full_trim,
            "tokens": self.tokens,
            "engine": self.engine,
            "bed_length": self.bed_length,
            "drivetrain": self.drivetrain,
        }

    @classmethod
    def from_string(cls, raw: str) -> "TrimProfile":
        engine = None
        bed_length = None
        drivetrain = None
        body_style = None
        full_trim = raw

        match = ENGINE_DISPLACEMENT_RE.search(raw)
        if match:
            engine = match.group(0).strip()
            raw = raw.replace(engine, "")

        match = BED_LENGTH_RE.search(raw)
        if match:
            bed_length = match.group(0).strip()
            raw = raw.replace(bed_length, "")

        match = BODY_STYLE_RE.search(raw)
        if match:
            body_style = match.group(0).strip()
            raw = raw.replace(body_style, "")
            if body_style in BODY_STYLE_ALIASES:
                body_style = BODY_STYLE_ALIASES[body_style]

        for dt in DRIVETRAINS:
            if dt in raw.lower().split():
                drivetrain = dt.strip()
                raw = raw.replace(drivetrain, "")
                break

        # Tokenize the rest
        tokens = raw.lower().split()

        return cls(engine, bed_length, drivetrain, body_style, tokens, full_trim)

    def build_compare_string(
        self,
        compare_engine: bool,
        compare_drivetrain: bool,
        compare_body: bool,
        compare_bed: bool,
    ) -> str:

        # Order matters, so the best approximation is:
        # Engine, Trim Tokens, Drivetrain, Body Style, and Bed
        parts = []
        if compare_engine and self.engine:
            parts.append(self.engine)

        parts.extend(self.tokens)

        if compare_drivetrain and self.drivetrain:
            parts.append(self.drivetrain)
        if compare_body and self.body_style:
            parts.append(self.body_style)
        if compare_bed and self.bed_length:
            parts.append(self.bed_length)

        return " ".join(parts).lower()


class StructuralStatus(str, Enum):
    NONE = "none"
    POSSIBLE = "possible"
    CONFIRMED = "confirmed"


@dataclass
class CarfaxData:
    summary: dict[str, str]
    accident_damage: dict[str, dict[str, str]]
    additional_history: dict[str, str]
    ownership_history: dict[str, dict[str, str]]
    detailed_history: list[tuple[str, str, str, str]]

    @property
    def is_branded(self) -> bool:
        # Check summary first
        summary_text = self.summary.get("accident_status", "").lower()
        if "branded title" in summary_text:
            return True

        # Scan detailed history
        for _, _, _, comments in self.detailed_history:
            if not comments:
                continue

            if any(
                phrase in comments
                for phrase in ("TITLE/CERTIFICATE ISSUED", "TITLE ISSUED")
            ):
                return True

        return False

    @property
    def has_accident(self) -> bool:
        # Check summary first
        summary_text = self.summary.get("accident_status", "").lower()
        # important to include the ':'
        if "accident reported:" in summary_text:
            return True

        # Check accident / damage history
        for event in self.accident_damage.values():
            event_summary = event.get("summary", "").lower()
            if "accident reported" in event_summary:
                return True

        return False

    @property
    def has_damage(self) -> bool:
        # Check summary first
        summary_text = self.summary.get("accident_status", "").lower()
        if any(
            damage in summary_text
            for damage in ("minor damage", "moderate damage", "severe damage")
        ):
            return True

        # Check accident / damage history
        for event in self.accident_damage.values():
            event_summary = event.get("summary", "").lower()
            if "damage" in event_summary:
                return True

        # Check additional history
        # value will always contain 'total loss', so look for specific date format
        pattern = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        total_loss = self.additional_history.get("Accident / Damage", "").lower()
        if pattern.search(total_loss):
            return True

        return False

    @property
    def is_total_loss(self) -> bool:
        # Check summary first
        summary_text = self.summary.get("accident_status", "").lower()
        if "total loss" in summary_text:
            return True

        # Check accident / damage history
        for event in self.accident_damage.values():
            event_summary = event.get("summary", "").lower()
            if "total loss vehicle" in event_summary:
                return True

        # Check additional history
        # value will always contain 'total loss', so look for specific date format
        pattern = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        total_loss = self.additional_history.get("Total Loss", "").lower()
        if pattern.search(total_loss):
            return True

        # Scan detailed history
        for _, _, _, comments in self.detailed_history:
            if comments and "TOTAL LOSS VEHICLE" in comments:
                return True

        return False

    @property
    def structural_status(self) -> StructuralStatus:
        # Logic is backwards because I haven't found a report that has structural damage
        status = self.additional_history.get("Structural Damage", "")
        if status == "No structural damage reported to CARFAX.":
            return StructuralStatus.NONE

        if (
            status
            == "CARFAX recommends that you have this vehicle inspected by a collision repair specialist."
        ):
            return StructuralStatus.POSSIBLE

        return StructuralStatus.CONFIRMED

    @property
    def airbags_deployed(self) -> bool:
        # Check additional history
        # value will always contain 'total loss', so look for specific date format
        pattern = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
        deployed = self.additional_history.get("Total Loss", "").lower()
        if pattern.search(deployed):
            return True

        return False

    @property
    def has_recall(self) -> bool:
        # Logic is backwards because I haven't found a report that has structural damage
        status = self.additional_history.get("Manufacturer Recall", "")
        if status == "No open recalls reported to CARFAX.":
            return False

        return True

    @property
    def has_odometer_problem(self) -> bool:
        text = self.additional_history.get("Odometer Check", "")
        if text == "DMV title problems reported.":
            return True

        return False

    @property
    def is_warranty_active(self) -> bool:
        if self.has_damage or self.has_damage:
            return False

        # Match phrases like: "estimated to have 20 months or 24,135 miles remaining"
        pattern = re.compile(
            r"estimated to have\s+(\d+)\s+months?\s+or\s+([\d,]+)\s+miles?\s+remaining"
        )
        basic_warranty = self.additional_history.get("Basic Warranty", "").lower()
        match = pattern.search(basic_warranty)
        return bool(match)

    @property
    def service_record_count(self) -> int:
        repairs = self.summary.get("repairs")
        if repairs:
            numbers_only = re.sub(r"\D", "", repairs)
            try:
                return int(numbers_only)
            except ValueError:
                print("Unable to cast repair record count to integer:", repairs)
                return -1
        # Carfax may not have records
        return 0

    @property
    def owner_count(self) -> int:
        owner_text = self.summary.get("owners")
        if owner_text:
            numbers_only = re.sub(r"\D", "", owner_text)
            try:
                return int(numbers_only)
            except ValueError:
                print("Unable to cast owner count to integer:", owner_text)
                return -1
        # Carfax may not have records
        return 0

    @property
    def last_odometer_reading(self) -> int:
        odometer = self.summary.get("odometer")
        if odometer:
            numbers_only = re.sub(r"\D", "", odometer)
            try:
                return int(numbers_only)
            except ValueError:
                print("Unable to cast odometer to integer:", odometer)
                return -1
        # Carfax may not have records
        return 0

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from visor_scraper.constants import (
    BED_LENGTH_RE,
    BODY_STYLE_RE,
    DRIVETRAINS,
    ENGINE_DISPLACEMENT_RE,
)


@dataclass
class TrimValuation:
    model: str
    kbb_trim: str
    fmv: int
    fmv_source: str
    msrp: int
    msrp_source: str
    fpp: int
    fpp_source: str

    def __repr__(self):
        return (
            f"TrimValuation(model={self.model}, "
            f"kbb_trim={self.kbb_trim!r}, "
            f"fmv={self.fmv}, "
            f"fmv_source={self.fmv_source!r})"
            f"msrp={self.fmv}, "
            f"msrp_source={self.msrp_source!r})"
            f"fpp={self.fpp}, "
            f"fpp_source={self.fpp_source!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "kbb_trim": self.kbb_trim,
            "fmv": self.fmv,
            "fmv_source": self.fmv_source,
            "msrp": self.msrp,
            "msrp_source": self.msrp_source,
            "fpp": self.fpp,
            "fpp_source": self.fpp_source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrimValuation":
        return cls(
            model=data["model"],
            kbb_trim=data["kbb_trim"],
            fmv=data["fmv"],
            fmv_source=data["fmv_source"],
            msrp=data["msrp"],
            msrp_source=data["msrp_source"],
            fpp=data["fpp"],
            fpp_source=data["fpp_source"],
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
            engine = match.group(0)
            raw = raw.replace(engine, "")

        match = BED_LENGTH_RE.search(raw)
        if match:
            bed_length = match.group(0)
            raw = raw.replace(bed_length, "")

        match = BODY_STYLE_RE.search(raw)
        if match:
            body_style = match.group(0)
            raw = raw.replace(body_style, "")

        for dt in DRIVETRAINS:
            if dt in raw.lower().split():
                drivetrain = dt
                raw = raw.replace(drivetrain, "")
                break

        # Tokenize the rest
        tokens = raw.split()

        return cls(engine, bed_length, drivetrain, body_style, tokens, full_trim)

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TrimValuation:
    visor_trim: str
    kbb_trim: str
    fmv: int
    fmv_source: str
    msrp: int
    msrp_source: str
    fpp: int
    fpp_source: str

    def __repr__(self):
        return (
            f"TrimValuation(visor_trim={self.visor_trim!r}, "
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
            "visor_trim": self.visor_trim,
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
            visor_trim=data["visor_trim"],
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
    title: str
    condition: str  # "New" | "Used" | "Certified"
    miles: int
    price: int
    price_delta: int
    uncertainty: str
    risk: str
    deal_rating: Optional[str] = (
        None  # "Great" | "Good" | "Fair" | "Poor" | "Bad" | None if no price
    )
    fmv: Optional[int] = None
    deviation_pct: Optional[float] = None  # signed; negative = under FMV

    def __repr__(self):
        return (
            f"CarListing(id={self.id!r}, "
            f"vin={self.vin!r}, "
            f"title={self.title}, "
            f"condition={self.condition}, "
            f"miles={self.miles!r}, "
            f"price={self.price!r}, "
            f"price_delta={self.price_delta!r}, "
            f"uncertainty={self.uncertainty!r}, "
            f"risk={self.risk!r}, "
            f"deal_rating={self.deal_rating!r}, "
            f"fmv={self.fmv!r}, "
            f"deviation_pct={self.deviation_pct!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "vin": self.vin,
            "title": self.title,
            "condition": self.condition,
            "miles": self.miles,
            "price": self.price,
            "price_delta": self.price_delta,
            "uncertainty": self.uncertainty,
            "risk": self.risk,
            "deal_rating": self.deal_rating,
            "fmv": self.fmv,
            "deviation_pct": self.deviation_pct,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CarListing":
        return cls(
            id=data["id"],
            vin=data["vin"],
            title=data["title"],
            condition=data["condition"],
            miles=data["miles"],
            price=data["price"],
            price_delta=data["price_delta"],
            uncertainty=data["uncertainty"],
            risk=data["risk"],
            deal_rating=data["deal_rating"],
            fmv=data["fmv"],
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "count": self.count,
            "percent_of_total": self.percent_of_total,
            "avg_deviation_pct": self.avg_deviation_pct,
            "condition_counts": self.condition_counts,
            "listings": [listing.to_dict() for listing in self.listings],
        }

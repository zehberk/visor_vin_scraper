from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class TrimValuation:
    visor_trim: str
    kbb_trim: str
    fmv: int
    source: str

    def __repr__(self):
        return (
            f"TrimValuation(visor_trim={self.visor_trim!r}, "
            f"kbb_trim={self.kbb_trim!r}, "
            f"fmv={self.fmv}, "
            f"source={self.source!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "visor_trim": self.visor_trim,
            "kbb_trim": self.kbb_trim,
            "fmv": self.fmv,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrimValuation":
        return cls(
            visor_trim=data["visor_trim"],
            kbb_trim=data["kbb_trim"],
            fmv=data["fmv"],
            source=data["source"],
        )


@dataclass
class CarListing:
    id: str
    vin: str
    title: str
    miles: int
    price: int
    price_delta: int
    uncertainty: str
    risk: str

    def __repr__(self):
        return (
            f"CarListing(id={self.id!r}, "
            f"vin={self.vin!r}, "
            f"title={self.title}, "
            f"miles={self.miles!r}, "
            f"price={self.price!r}, "
            f"price_delta={self.price_delta!r}, "
            f"uncertainty={self.uncertainty!r}, "
            f"risk={self.risk!r})"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "vin": self.vin,
            "title": self.title,
            "miles": self.miles,
            "price": self.price,
            "price_delta": self.price_delta,
            "uncertainty": self.uncertainty,
            "risk": self.risk,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CarListing":
        return cls(
            id=data["id"],
            vin=data["vin"],
            title=data["title"],
            miles=data["miles"],
            price=data["price"],
            price_delta=data["price_delta"],
            uncertainty=data["uncertainty"],
            risk=data["risk"],
        )


@dataclass
class DealBin:
    category: str
    listings: list[CarListing]
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "count": self.count,
            "listings": [listing.to_dict() for listing in self.listings],
        }

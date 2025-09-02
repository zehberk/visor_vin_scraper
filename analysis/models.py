from dataclasses import dataclass, field
from typing import List


@dataclass
class CarEntry:
    visor_trim: str
    kbb_trim: str
    fmv: int
    source: str
    count: int = 0

    def __repr__(self):
        return (
            f"CarEntry(visor_trim={self.visor_trim!r}, "
            f"kbb_trim={self.kbb_trim!r}, "
            f"fmv={self.fmv}, "
            f"source={self.source!r}, "
            f"count={self.count})"
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

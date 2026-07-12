"""Allocation drift vs. the target allocation in config/targets.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CashBalance, Lot

UNASSIGNED = "unassigned"
CASH_CLASS = "cash"


@dataclass
class ClassAllocation:
    name: str
    target: float
    value_eur: float = 0.0
    weight: float = 0.0
    symbols: set[str] = field(default_factory=set)

    def breached(self, abs_band: float, rel_band: float) -> bool:
        if self.name == UNASSIGNED:
            return self.value_eur > 0
        drift = abs(self.weight - self.target)
        return drift > abs_band or (self.target > 0 and drift / self.target > rel_band)


def compute_allocations(
    lots: list[Lot], cash: list[CashBalance], targets_cfg: dict
) -> list[ClassAllocation]:
    classes = {
        name: ClassAllocation(name=name, target=float(spec.get("target", 0)))
        for name, spec in targets_cfg["asset_classes"].items()
    }
    classes.setdefault(CASH_CLASS, ClassAllocation(name=CASH_CLASS, target=0.0))
    classes[UNASSIGNED] = ClassAllocation(name=UNASSIGNED, target=0.0)

    ticker_to_class = {
        ticker.upper(): name
        for name, spec in targets_cfg["asset_classes"].items()
        for ticker in spec.get("tickers", [])
    }

    for lot in lots:
        cls = ticker_to_class.get(lot.symbol.upper(), UNASSIGNED)
        classes[cls].value_eur += lot.value_eur
        classes[cls].symbols.add(lot.symbol)

    for bal in cash:
        classes[CASH_CLASS].value_eur += bal.value_eur

    total = sum(c.value_eur for c in classes.values())
    if total > 0:
        for c in classes.values():
            c.weight = c.value_eur / total

    return [c for c in classes.values() if c.target > 0 or c.value_eur != 0]

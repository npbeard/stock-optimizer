"""Dual-regime (Spain residence + US citizenship) tax cost estimation.

Model, per lot sold today:
  - Spain: gain taxed at savings-income marginal rates, stacked on gains
    already realized this year. FIFO ordering is Spain's mandatory method,
    which matches IBKR's default lot reporting.
  - US: long-term (held > 1 year) at the configured LTCG rate, otherwise the
    ordinary marginal rate; optional 3.8% NIIT on top.
  - Combined: max(ES, US) — a simplified foreign-tax-credit model. Spain
    taxes first as residence country; the US credits Spanish tax against the
    US liability, so the total is approximately the higher of the two.
    Good enough to rank trade decisions; not a filing-grade calculation.

Losses reduce estimated tax at the same marginal rates (sign-symmetric),
which approximates their offset value against other gains this year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .models import Lot, Trade

US_LONG_TERM_DAYS = 365


def spain_savings_tax(gain: float, brackets: list[tuple[float, float]]) -> float:
    """Progressive tax on a positive savings-income amount. Negative -> 0."""
    if gain <= 0:
        return 0.0
    tax = 0.0
    lower = 0.0
    for upper, rate in brackets:
        if gain > lower:
            tax += (min(gain, upper) - lower) * rate
            lower = upper
        else:
            break
    return tax


def spain_marginal_tax(gain: float, ytd_gains: float, brackets: list[tuple[float, float]]) -> float:
    """Incremental Spanish tax from realizing `gain` on top of `ytd_gains`.

    For losses, returns the (negative) tax saved assuming the loss offsets
    gains taxed at the current marginal level.
    """
    base = max(ytd_gains, 0.0)
    if gain >= 0:
        return spain_savings_tax(base + gain, brackets) - spain_savings_tax(base, brackets)
    return -(spain_savings_tax(base, brackets) - spain_savings_tax(max(base + gain, 0.0), brackets))


@dataclass
class LotTaxEstimate:
    lot: Lot
    gain_eur: float
    is_us_long_term: bool
    es_tax: float
    us_tax: float

    @property
    def effective_tax(self) -> float:
        """Simplified FTC: combined liability ~ the higher of the two regimes."""
        if self.gain_eur >= 0:
            return max(self.es_tax, self.us_tax)
        return min(self.es_tax, self.us_tax)  # most negative = larger saving


def estimate_lot_tax(lot: Lot, tax_cfg: dict, as_of: date) -> LotTaxEstimate:
    gain = lot.unrealized_eur
    holding_days = (as_of - lot.open_date).days if lot.open_date else 0
    long_term = holding_days > US_LONG_TERM_DAYS

    brackets = [(float(u), float(r)) for u, r in tax_cfg["spain_brackets"]]
    es_tax = spain_marginal_tax(gain, float(tax_cfg.get("es_ytd_realized_gains", 0)), brackets)

    us_rate = float(tax_cfg["us_ltcg_rate"]) if long_term else float(tax_cfg["us_stcg_rate"])
    if tax_cfg.get("apply_niit", False):
        us_rate += 0.038
    us_tax = gain * us_rate

    return LotTaxEstimate(lot=lot, gain_eur=gain, is_us_long_term=long_term, es_tax=es_tax, us_tax=us_tax)


@dataclass
class WashSaleStatus:
    us_blocked: bool
    es_blocked: bool
    last_buy: date | None


def wash_sale_status(
    symbol: str, trades: list[Trade], as_of: date, us_days: int, es_days: int
) -> WashSaleStatus:
    """Would selling `symbol` at a loss today have its loss disallowed?

    Checks purchases within the lookback window. (Forward windows — buying
    back after the sale — are the user's responsibility and are noted in the
    report text.)
    """
    buys = [
        t.trade_date
        for t in trades
        if t.symbol == symbol and t.buy_sell.upper().startswith("BUY") and t.quantity > 0
    ]
    last_buy = max(buys) if buys else None
    us_blocked = any(as_of - timedelta(days=us_days) <= d <= as_of for d in buys)
    es_blocked = any(as_of - timedelta(days=es_days) <= d <= as_of for d in buys)
    return WashSaleStatus(us_blocked=us_blocked, es_blocked=es_blocked, last_buy=last_buy)


def looks_like_pfic(lot: Lot) -> bool:
    """Heuristic PFIC flag: a fund/ETF whose ISIN is not US-prefixed.

    Non-US pooled investments (e.g. Irish/Luxembourg UCITS) are generally
    PFICs for a US taxpayer. Individual stocks are not affected.
    """
    category = lot.asset_category.upper()
    if category not in {"FUND", "ETF"} and not (
        category == "STK" and "ETF" in lot.description.upper()
    ):
        return False
    return bool(lot.isin) and not lot.isin.startswith("US")

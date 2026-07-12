from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Lot:
    """One tax lot from an IBKR Flex 'Open Positions' section (lot level of detail).

    Money fields are in the position's local currency; fx_rate_to_base converts
    to the account base currency (EUR).
    """

    report_date: date
    account_id: str
    symbol: str
    description: str
    isin: str
    asset_category: str  # STK, ETF (reported as STK/FUND), BOND, CASH...
    currency: str
    fx_rate_to_base: float
    open_date: date | None
    quantity: float
    cost_basis_money: float
    mark_price: float
    position_value: float

    @property
    def value_eur(self) -> float:
        return self.position_value * self.fx_rate_to_base

    @property
    def cost_eur(self) -> float:
        """Cost basis converted at *today's* FX rate.

        Simplification: Spain requires the purchase-date FX rate for the cost
        leg. Historical FX per lot is a planned enhancement; the difference is
        usually small but can matter for old USD lots.
        """
        return self.cost_basis_money * self.fx_rate_to_base

    @property
    def unrealized_eur(self) -> float:
        return self.value_eur - self.cost_eur


@dataclass
class Trade:
    trade_id: str
    account_id: str
    symbol: str
    isin: str
    asset_category: str
    currency: str
    fx_rate_to_base: float
    trade_date: date
    buy_sell: str  # BUY / SELL
    quantity: float
    trade_price: float
    fifo_pnl_realized: float


@dataclass
class CashBalance:
    report_date: date
    account_id: str
    currency: str
    amount: float
    fx_rate_to_base: float = 1.0

    @property
    def value_eur(self) -> float:
        return self.amount * self.fx_rate_to_base


@dataclass
class FlexStatement:
    account_id: str
    report_date: date
    lots: list[Lot]
    trades: list[Trade]
    cash: list[CashBalance]

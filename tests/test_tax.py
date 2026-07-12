import math
from datetime import date

import pytest

from portfolio_optimizer.models import Lot, Trade
from portfolio_optimizer.tax import (
    estimate_lot_tax,
    looks_like_pfic,
    spain_marginal_tax,
    spain_savings_tax,
    wash_sale_status,
)

BRACKETS = [(6000, 0.19), (50000, 0.21), (200000, 0.23), (300000, 0.27), (math.inf, 0.30)]

TAX_CFG = {
    "spain_brackets": BRACKETS,
    "es_ytd_realized_gains": 0,
    "us_ltcg_rate": 0.15,
    "us_stcg_rate": 0.32,
    "apply_niit": False,
}


def make_lot(**overrides):
    defaults = dict(
        report_date=date(2026, 7, 10), account_id="U1", symbol="AAPL",
        description="APPLE INC", isin="US0378331005", asset_category="STK",
        currency="USD", fx_rate_to_base=1.0, open_date=date(2024, 1, 2),
        quantity=10, cost_basis_money=1000, mark_price=150, position_value=1500,
    )
    defaults.update(overrides)
    return Lot(**defaults)


def test_spain_savings_tax_brackets():
    assert spain_savings_tax(6000, BRACKETS) == pytest.approx(1140)
    # 6000*0.19 + 4000*0.21
    assert spain_savings_tax(10000, BRACKETS) == pytest.approx(1980)
    assert spain_savings_tax(-500, BRACKETS) == 0


def test_spain_marginal_stacks_on_ytd_gains():
    # With 50k already realized, an extra 1k is taxed fully at 23%.
    assert spain_marginal_tax(1000, 50000, BRACKETS) == pytest.approx(230)
    # A loss saves tax at the marginal level.
    assert spain_marginal_tax(-1000, 50000, BRACKETS) == pytest.approx(-210)


def test_us_holding_period_split():
    lt = estimate_lot_tax(make_lot(open_date=date(2024, 1, 2)), TAX_CFG, date(2026, 7, 10))
    st = estimate_lot_tax(make_lot(open_date=date(2026, 5, 1)), TAX_CFG, date(2026, 7, 10))
    assert lt.is_us_long_term and not st.is_us_long_term
    assert lt.us_tax == pytest.approx(500 * 0.15)
    assert st.us_tax == pytest.approx(500 * 0.32)


def test_effective_tax_is_max_of_regimes_for_gains():
    est = estimate_lot_tax(make_lot(), TAX_CFG, date(2026, 7, 10))
    assert est.effective_tax == pytest.approx(max(est.es_tax, est.us_tax))
    assert est.effective_tax > 0


def test_wash_sale_windows():
    trades = [
        Trade(
            trade_id="1", account_id="U1", symbol="AAPL", isin="", asset_category="STK",
            currency="USD", fx_rate_to_base=1.0, trade_date=date(2026, 6, 25),
            buy_sell="BUY", quantity=10, trade_price=200, fifo_pnl_realized=0,
        )
    ]
    # 15 days after the buy: both windows blocked.
    ws = wash_sale_status("AAPL", trades, date(2026, 7, 10), us_days=30, es_days=61)
    assert ws.us_blocked and ws.es_blocked
    # 45 days after: US clear, Spain (2 months) still blocked.
    ws = wash_sale_status("AAPL", trades, date(2026, 8, 9), us_days=30, es_days=61)
    assert not ws.us_blocked and ws.es_blocked
    # 70 days after: both clear.
    ws = wash_sale_status("AAPL", trades, date(2026, 9, 3), us_days=30, es_days=61)
    assert not ws.us_blocked and not ws.es_blocked


def test_pfic_heuristic():
    ucits = make_lot(symbol="VUAA", asset_category="FUND", isin="IE00BFMXXD54")
    us_etf = make_lot(symbol="VOO", asset_category="FUND", isin="US9229083632")
    stock = make_lot(symbol="SAN", asset_category="STK", isin="ES0113900J37")
    assert looks_like_pfic(ucits)
    assert not looks_like_pfic(us_etf)
    assert not looks_like_pfic(stock)

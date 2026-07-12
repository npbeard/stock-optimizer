import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from portfolio_optimizer.backtest import walk_forward
from portfolio_optimizer.models import Lot
from portfolio_optimizer.optimizer import annualized_covariance, target_weights
from portfolio_optimizer.rebalance import generate_trade_plan

RNG = np.random.default_rng(42)


def synthetic_returns(n_days=800, vols=(0.01, 0.01, 0.03)):
    """Three assets: A and B correlated and calm, C volatile and independent."""
    common = RNG.normal(0, 0.008, n_days)
    data = {
        "AAA": common + RNG.normal(0.0003, vols[0], n_days),
        "BBB": common + RNG.normal(0.0003, vols[1], n_days),
        "CCC": RNG.normal(0.0005, vols[2], n_days),
    }
    idx = pd.bdate_range("2022-01-03", periods=n_days)
    return pd.DataFrame(data, index=idx)


SETTINGS = {
    "tax": {
        "spain_brackets": [[6000, 0.19], [50000, 0.21], [200000, 0.23], [300000, 0.27], [math.inf, 0.30]],
        "es_ytd_realized_gains": 0,
        "us_ltcg_rate": 0.15,
        "us_stcg_rate": 0.32,
        "apply_niit": False,
    },
    "report": {"harvest_min_loss_eur": 500, "wash_sale_days_us": 30, "wash_sale_days_es": 61},
    "optimizer": {
        "universe": [], "lookback_days": 756, "backtest_window_days": 504,
        "max_weight": 0.6, "risk_aversion": 5.0, "tax_aversion": 1.0,
        "fee_rate": 0.001, "min_trade_eur": 100, "min_cash_eur": 0,
    },
}


def make_lot(symbol, value, cost, opened, qty=100):
    return Lot(
        report_date=date(2026, 7, 10), account_id="U1", symbol=symbol,
        description=symbol, isin="US0000000000", asset_category="STK",
        currency="EUR", fx_rate_to_base=1.0, open_date=opened,
        quantity=qty, cost_basis_money=cost, mark_price=value / qty, position_value=value,
    )


def test_target_weights_sum_and_cap():
    returns = synthetic_returns()
    weights = target_weights(returns, cash_target=0.05, max_weight=0.6)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["CASH"] == pytest.approx(0.05)
    risky = {k: v for k, v in weights.items() if k != "CASH"}
    assert all(w <= 0.6 * 0.95 + 1e-9 for w in risky.values())
    # The volatile independent asset should get less than the calm ones.
    assert weights["CCC"] < weights["AAA"]
    assert weights["CCC"] < weights["BBB"]


def test_trade_plan_moves_toward_target_and_respects_fifo():
    returns = synthetic_returns()
    cov = annualized_covariance(returns)
    # Grossly overweight CCC via two lots; FIFO: old lot (big gain), new lot (small gain).
    lots = [
        make_lot("CCC", 80000, 20000, date(2019, 1, 2)),
        make_lot("CCC", 20000, 18000, date(2025, 1, 2)),
        make_lot("AAA", 10000, 9000, date(2023, 1, 2)),
    ]
    targets = {"AAA": 0.4, "BBB": 0.35, "CCC": 0.2, "CASH": 0.05}
    plan = generate_trade_plan(
        lots=lots, trades=[], cash_eur=5000, targets=targets, cov=cov,
        settings=SETTINGS, as_of=date(2026, 7, 10),
    )
    assert plan.solver_status in ("optimal", "optimal_inaccurate")
    assert any(leg.symbol == "CCC" for leg in plan.sells)
    assert any(leg.symbol == "BBB" for leg in plan.buys)
    assert plan.tracking_vol_after < plan.tracking_vol_before
    # Strict FIFO: the newer CCC lot may only sell once the older lot is fully sold.
    ccc = {leg.lot_open: leg.fraction for leg in plan.sells if leg.symbol == "CCC"}
    if ccc.get(date(2025, 1, 2), 0) > 0:
        assert ccc.get(date(2019, 1, 2), 0) == pytest.approx(1.0)


def test_higher_tax_aversion_sells_less():
    returns = synthetic_returns()
    cov = annualized_covariance(returns)
    lots = [make_lot("CCC", 80000, 5000, date(2019, 1, 2)), make_lot("AAA", 10000, 9000, date(2023, 1, 2))]
    targets = {"AAA": 0.45, "BBB": 0.3, "CCC": 0.2, "CASH": 0.05}

    def total_sold(tax_aversion):
        settings = {**SETTINGS, "optimizer": {**SETTINGS["optimizer"], "tax_aversion": tax_aversion}}
        plan = generate_trade_plan(
            lots=lots, trades=[], cash_eur=5000, targets=targets, cov=cov,
            settings=settings, as_of=date(2026, 7, 10),
        )
        return sum(leg.proceeds_eur for leg in plan.sells)

    assert total_sold(10.0) < total_sold(0.0)


def test_walk_forward_runs_and_reports_three_strategies():
    returns = synthetic_returns(n_days=760)
    results = walk_forward(returns, {"AAA": 0.5, "CCC": 0.5}, window_days=504)
    assert [r.name for r in results] == [
        "HRP (monthly rebalance)", "Equal weight (monthly rebalance)", "Current weights (buy & hold)",
    ]
    for r in results:
        assert r.ann_vol > 0
        assert r.max_drawdown <= 0

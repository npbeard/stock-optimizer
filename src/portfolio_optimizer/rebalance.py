"""Tax-aware trade generation via convex optimization (after Moehle,
Kochenderfer, Boyd & Ang 2021, simplified).

Decision variables: per-lot sell fractions and per-symbol buy amounts.
Objective (all in EUR):

    minimize  risk_aversion * W * TrackingVariance(post-trade weights, target)
            + tax_aversion  * TaxCost(lot sales)
            + fee_rate      * Turnover

subject to long-only, Spain's mandatory FIFO (within a symbol, a newer lot
cannot be sold ahead of an older one), and a minimum cash floor.

Tax per lot is linear in the sold fraction, using this project's dual-regime
(ES/US) effective tax from tax.py. Loss lots whose wash-sale window is
blocked get no credit. The optimizer therefore trades only when the risk
reduction is worth the tax bill — turn `tax_aversion` up to make it more
reluctant, down toward 0 to ignore taxes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import cvxpy as cp
import numpy as np
import pandas as pd

from .models import Lot, Trade
from .tax import estimate_lot_tax, spain_marginal_tax, wash_sale_status


@dataclass
class SellLeg:
    symbol: str
    lot_open: date | None
    fraction: float
    quantity: float
    proceeds_eur: float
    gain_eur: float
    tax_eur: float


@dataclass
class BuyLeg:
    symbol: str
    amount_eur: float


@dataclass
class TradePlan:
    sells: list[SellLeg] = field(default_factory=list)
    buys: list[BuyLeg] = field(default_factory=list)
    total_tax_eur: float = 0.0
    total_fees_eur: float = 0.0
    tracking_vol_before: float = 0.0
    tracking_vol_after: float = 0.0
    solver_status: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.sells and not self.buys


def generate_trade_plan(
    lots: list[Lot],
    trades: list[Trade],
    cash_eur: float,
    targets: dict[str, float],  # symbol -> weight, must include "CASH"
    cov: pd.DataFrame,          # annualized covariance over the risky symbols
    settings: dict,             # full settings dict (tax + report + optimizer)
    as_of: date,
) -> TradePlan:
    opt = settings["optimizer"]
    tax_cfg = settings["tax"]
    report_cfg = settings["report"]

    symbols = [s for s in targets if s != "CASH"]
    index = {s: i for i, s in enumerate(symbols)}
    n = len(symbols)

    # Lots sorted oldest-first within each symbol (FIFO order).
    sorted_lots = sorted(
        [l for l in lots if l.symbol in index],
        key=lambda l: (l.symbol, l.open_date or as_of),
    )
    n_lots = len(sorted_lots)
    lot_value = np.array([l.value_eur for l in sorted_lots])

    # Per-lot tax if fully sold; blocked-wash loss lots earn no credit.
    lot_tax = np.zeros(n_lots)
    for i, lot in enumerate(sorted_lots):
        est = estimate_lot_tax(lot, tax_cfg, as_of)
        tax = est.effective_tax
        if est.gain_eur < 0:
            ws = wash_sale_status(
                lot.symbol, trades, as_of,
                int(report_cfg["wash_sale_days_us"]), int(report_cfg["wash_sale_days_es"]),
            )
            if ws.us_blocked or ws.es_blocked:
                tax = 0.0
        lot_tax[i] = tax

    held = np.zeros(n)
    for lot in sorted_lots:
        held[index[lot.symbol]] += lot.value_eur
    wealth = held.sum() + cash_eur
    if wealth <= 0:
        return TradePlan(solver_status="no_wealth")

    sigma = np.zeros((n + 1, n + 1))
    sigma[:n, :n] = cov.loc[symbols, symbols].values
    w_target = np.array([targets[s] for s in symbols] + [targets["CASH"]])

    risk_aversion = float(opt["risk_aversion"])
    tax_aversion = float(opt["tax_aversion"])
    fee_rate = float(opt["fee_rate"])

    s = cp.Variable(n_lots, nonneg=True)
    b = cp.Variable(n, nonneg=True)

    sell_matrix = np.zeros((n, n_lots))
    for i, lot in enumerate(sorted_lots):
        sell_matrix[index[lot.symbol], i] = lot.value_eur
    sold_per_symbol = sell_matrix @ s

    tax_total = lot_tax @ s
    turnover = cp.sum(cp.multiply(lot_value, s)) + cp.sum(b)
    fees = fee_rate * turnover
    cash_post = cash_eur + cp.sum(sold_per_symbol) - cp.sum(b) - tax_total - fees
    x = held - sold_per_symbol + b
    w_post = cp.hstack([x, cash_post]) / wealth

    tracking = cp.quad_form(w_post - w_target, cp.psd_wrap(sigma))
    objective = cp.Minimize(
        risk_aversion * wealth * tracking + tax_aversion * tax_total + fees
    )

    constraints = [s <= 1, cash_post >= float(opt["min_cash_eur"])]
    # FIFO: within a symbol, an older lot's sold fraction bounds any newer lot's.
    for i in range(1, n_lots):
        if sorted_lots[i].symbol == sorted_lots[i - 1].symbol:
            constraints.append(s[i] <= s[i - 1])

    problem = cp.Problem(objective, constraints)
    problem.solve(solver=cp.CLARABEL)
    if problem.status not in ("optimal", "optimal_inaccurate"):
        return TradePlan(solver_status=problem.status)

    min_trade = float(opt["min_trade_eur"])
    s_val = np.clip(s.value, 0, 1)
    b_val = np.maximum(b.value, 0)

    plan = TradePlan(solver_status=problem.status)

    # The solver's proportional lot split is a convex relaxation of Spain's
    # mandatory FIFO. Keep its per-symbol sale totals, but reallocate them
    # strictly oldest-lot-first and recompute exact taxes on that allocation.
    sold_total: dict[str, float] = {}
    for i, lot in enumerate(sorted_lots):
        sold_total[lot.symbol] = sold_total.get(lot.symbol, 0.0) + float(lot_value[i] * s_val[i])

    for symbol, amount in sorted(sold_total.items()):
        if amount < min_trade:
            continue
        remaining = amount
        for i, lot in enumerate(sorted_lots):
            if lot.symbol != symbol or remaining <= 0:
                continue
            take = min(remaining, float(lot_value[i]))
            remaining -= take
            fraction = take / float(lot_value[i]) if lot_value[i] else 0.0
            plan.sells.append(SellLeg(
                symbol=symbol,
                lot_open=lot.open_date,
                fraction=fraction,
                quantity=float(lot.quantity * fraction),
                proceeds_eur=take,
                gain_eur=float(lot.unrealized_eur * fraction),
                tax_eur=float(lot_tax[i] * fraction),
            ))
    for i, symbol in enumerate(symbols):
        if b_val[i] >= min_trade:
            plan.buys.append(BuyLeg(symbol=symbol, amount_eur=float(b_val[i])))

    # Anti-wash guard: never buy a symbol we are selling at a loss.
    loss_sells = {leg.symbol for leg in plan.sells if leg.gain_eur < 0}
    plan.buys = [leg for leg in plan.buys if leg.symbol not in loss_sells]

    # Summary tax: aggregate the Spanish side (gains from all legs stack the
    # savings brackets within the year) vs the summed US side, then max(ES, US)
    # — consistent with the simplified FTC model in tax.py.
    brackets = [(float(u), float(r)) for u, r in tax_cfg["spain_brackets"]]
    net_gain = sum(leg.gain_eur for leg in plan.sells)
    es_total = spain_marginal_tax(net_gain, float(tax_cfg.get("es_ytd_realized_gains", 0)), brackets)
    us_total = 0.0
    for leg in plan.sells:
        for i, lot in enumerate(sorted_lots):
            if lot.symbol == leg.symbol and lot.open_date == leg.lot_open:
                est = estimate_lot_tax(lot, tax_cfg, as_of)
                us_total += est.us_tax * leg.fraction
                break
    plan.total_tax_eur = float(max(es_total, us_total)) if net_gain >= 0 else float(min(es_total, us_total))
    plan.total_fees_eur = float(
        fee_rate * (sum(l.proceeds_eur for l in plan.sells) + sum(l.amount_eur for l in plan.buys))
    )

    def tracking_vol(values: np.ndarray, cash: float) -> float:
        w = np.append(values, cash) / wealth - w_target
        return float(np.sqrt(w @ sigma @ w))

    sold_final = np.zeros(n)
    for leg in plan.sells:
        sold_final[index[leg.symbol]] += leg.proceeds_eur
    bought_final = np.zeros(n)
    for leg in plan.buys:
        bought_final[index[leg.symbol]] += leg.amount_eur
    cash_final = cash_eur + sold_final.sum() - bought_final.sum() - plan.total_tax_eur - plan.total_fees_eur
    plan.tracking_vol_before = tracking_vol(held, cash_eur)
    plan.tracking_vol_after = tracking_vol(held - sold_final + bought_final, cash_final)
    return plan

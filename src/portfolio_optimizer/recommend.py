"""Orchestrates the Phase 2 recommendation: prices -> HRP targets ->
tax-aware trade plan -> markdown section for the daily report."""

from __future__ import annotations

import sqlite3

from .models import FlexStatement
from .optimizer import annualized_covariance, target_weights
from .prices import returns_matrix
from .rebalance import TradePlan, generate_trade_plan


def _eur(x: float) -> str:
    if abs(x) < 0.5:
        x = 0.0
    return f"€{x:,.0f}"


def universe_symbols(stmt: FlexStatement, settings: dict) -> list[str]:
    held = sorted({l.symbol for l in stmt.lots})
    extra = [s.upper() for s in settings["optimizer"].get("universe", [])]
    return sorted(set(held) | set(extra))


def build_recommendation(
    conn: sqlite3.Connection, stmt: FlexStatement, settings: dict, targets_cfg: dict
) -> tuple[TradePlan, dict[str, float], str]:
    """Returns (plan, target weights, markdown section)."""
    opt = settings["optimizer"]
    symbols = universe_symbols(stmt, settings)
    returns = returns_matrix(conn, symbols, int(opt["lookback_days"]))

    cash_target = float(targets_cfg["asset_classes"].get("cash", {}).get("target", 0.05))
    targets = target_weights(
        returns, cash_target=cash_target, max_weight=opt.get("max_weight")
    )
    cov = annualized_covariance(returns)
    cash_eur = sum(c.value_eur for c in stmt.cash)
    plan = generate_trade_plan(
        lots=stmt.lots, trades=stmt.trades, cash_eur=cash_eur,
        targets=targets, cov=cov, settings=settings, as_of=stmt.report_date,
    )
    return plan, targets, _markdown(plan, targets, stmt)


def _markdown(plan: TradePlan, targets: dict[str, float], stmt: FlexStatement) -> str:
    total = sum(l.value_eur for l in stmt.lots) + sum(c.value_eur for c in stmt.cash)
    held: dict[str, float] = {}
    for lot in stmt.lots:
        held[lot.symbol] = held.get(lot.symbol, 0.0) + lot.value_eur
    held["CASH"] = sum(c.value_eur for c in stmt.cash)

    lines = ["## Recommended trades (HRP target, tax-aware)", ""]
    lines.append("| Asset | Current | HRP target |")
    lines.append("|---|---:|---:|")
    for symbol in sorted(targets, key=lambda s: -targets[s]):
        current = held.get(symbol, 0.0) / total if total else 0.0
        lines.append(f"| {symbol} | {current * 100:.1f}% | {targets[symbol] * 100:.1f}% |")
    lines.append("")

    if plan.solver_status not in ("optimal", "optimal_inaccurate"):
        lines.append(f"Trade optimizer did not converge (status: {plan.solver_status}).")
        return "\n".join(lines) + "\n"

    if plan.is_empty:
        lines.append(
            "**No trades recommended.** At current settings the tax + friction cost "
            "of moving toward target outweighs the tracking-risk reduction."
        )
        return "\n".join(lines) + "\n"

    lines.append("| Action | Asset | Lot | Amount | Realized gain | Tax |")
    lines.append("|---|---|---|---:|---:|---:|")
    for leg in plan.sells:
        lot_label = leg.lot_open.isoformat() if leg.lot_open else "?"
        lines.append(
            f"| SELL {leg.quantity:.0f} sh ({leg.fraction * 100:.0f}% of lot) | {leg.symbol} | "
            f"{lot_label} | {_eur(leg.proceeds_eur)} | {_eur(leg.gain_eur)} | {_eur(leg.tax_eur)} |"
        )
    for leg in plan.buys:
        lines.append(f"| BUY | {leg.symbol} | — | {_eur(leg.amount_eur)} | — | — |")
    lines.append("")
    lines.append(
        f"Est. total tax **{_eur(plan.total_tax_eur)}**, fees ~{_eur(plan.total_fees_eur)}. "
        f"Tracking error vs target: {plan.tracking_vol_before * 100:.1f}% → "
        f"{plan.tracking_vol_after * 100:.1f}% annualized."
    )
    lines.append("")
    lines.append(
        "*Recommendations only — nothing is executed. Review lot selection against "
        "your broker's cost-basis method before trading.*"
    )
    return "\n".join(lines) + "\n"

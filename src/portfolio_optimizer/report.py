"""Daily markdown report: valuation, drift, lot-level tax view, TLH candidates."""

from __future__ import annotations

from collections import defaultdict

from .drift import UNASSIGNED, compute_allocations
from .models import FlexStatement
from .tax import estimate_lot_tax, looks_like_pfic, wash_sale_status


def _eur(x: float) -> str:
    if abs(x) < 0.5:
        x = 0.0
    return f"€{x:,.0f}"


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def build_report(stmt: FlexStatement, settings: dict, targets: dict) -> str:
    tax_cfg = settings["tax"]
    report_cfg = settings["report"]
    as_of = stmt.report_date

    estimates = [estimate_lot_tax(lot, tax_cfg, as_of) for lot in stmt.lots]
    total_positions = sum(l.value_eur for l in stmt.lots)
    total_cash = sum(c.value_eur for c in stmt.cash)
    total = total_positions + total_cash
    total_unrealized = sum(e.gain_eur for e in estimates)

    lines: list[str] = []
    lines.append(f"# Portfolio report — {as_of.isoformat()}")
    lines.append("")
    lines.append(f"Account **{stmt.account_id}** · Total **{_eur(total)}** "
                 f"(positions {_eur(total_positions)}, cash {_eur(total_cash)}) · "
                 f"Unrealized P&L **{_eur(total_unrealized)}**")
    lines.append("")

    # --- Allocation vs target ---
    lines.append("## Allocation vs target")
    lines.append("")
    lines.append("| Class | Value | Weight | Target | Drift | Status |")
    lines.append("|---|---:|---:|---:|---:|---|")
    abs_band = float(targets["bands"]["absolute"])
    rel_band = float(targets["bands"]["relative"])
    any_breach = False
    for cls in compute_allocations(stmt.lots, stmt.cash, targets):
        breach = cls.breached(abs_band, rel_band)
        any_breach = any_breach or breach
        status = "⚠️ REBALANCE" if breach else "ok"
        if cls.name == UNASSIGNED and cls.value_eur > 0:
            status = "⚠️ classify in targets.yaml"
        lines.append(
            f"| {cls.name} | {_eur(cls.value_eur)} | {_pct(cls.weight)} | "
            f"{_pct(cls.target)} | {_pct(cls.weight - cls.target)} | {status} |"
        )
    lines.append("")
    if any_breach:
        lines.append(f"Bands: ±{_pct(abs_band)} absolute or ±{_pct(rel_band)} relative to target. "
                     "Prefer rebalancing with new cash/dividends before selling — selling realizes taxable gains.")
        lines.append("")

    # --- Positions summary ---
    by_symbol: dict[str, list] = defaultdict(list)
    for est in estimates:
        by_symbol[est.lot.symbol].append(est)

    lines.append("## Positions")
    lines.append("")
    lines.append("| Symbol | Qty | Value (EUR) | Weight | Unrealized | Est. tax if sold* |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for symbol in sorted(by_symbol, key=lambda s: -sum(e.lot.value_eur for e in by_symbol[s])):
        ests = by_symbol[symbol]
        value = sum(e.lot.value_eur for e in ests)
        lines.append(
            f"| {symbol} | {sum(e.lot.quantity for e in ests):g} | {_eur(value)} | "
            f"{_pct(value / total) if total else '—'} | "
            f"{_eur(sum(e.gain_eur for e in ests))} | {_eur(sum(e.effective_tax for e in ests))} |"
        )
    lines.append("")
    lines.append("\\*max(Spain, US) per lot — simplified foreign-tax-credit model, not filing-grade.")
    lines.append("")

    # --- Lot detail ---
    lines.append("## Tax lots")
    lines.append("")
    lines.append("| Symbol | Opened | Qty | Cost (EUR) | Value (EUR) | Gain | US term | Tax ES | Tax US | Effective |")
    lines.append("|---|---|---:|---:|---:|---:|---|---:|---:|---:|")
    for est in sorted(estimates, key=lambda e: (e.lot.symbol, e.lot.open_date or as_of)):
        lot = est.lot
        lines.append(
            f"| {lot.symbol} | {lot.open_date.isoformat() if lot.open_date else '?'} | "
            f"{lot.quantity:g} | {_eur(lot.cost_eur)} | {_eur(lot.value_eur)} | "
            f"{_eur(est.gain_eur)} | {'LT' if est.is_us_long_term else 'ST'} | "
            f"{_eur(est.es_tax)} | {_eur(est.us_tax)} | {_eur(est.effective_tax)} |"
        )
    lines.append("")

    # --- Tax-loss harvesting candidates ---
    min_loss = float(report_cfg["harvest_min_loss_eur"])
    candidates = [e for e in estimates if e.gain_eur <= -min_loss]
    lines.append("## Tax-loss harvesting candidates")
    lines.append("")
    if not candidates:
        lines.append(f"None (no lot with an unrealized loss ≥ {_eur(min_loss)}).")
    else:
        lines.append("| Symbol | Lot opened | Loss | Est. tax saved | Wash-sale check |")
        lines.append("|---|---|---:|---:|---|")
        for est in sorted(candidates, key=lambda e: e.gain_eur):
            ws = wash_sale_status(
                est.lot.symbol, stmt.trades, as_of,
                int(report_cfg["wash_sale_days_us"]), int(report_cfg["wash_sale_days_es"]),
            )
            flags = []
            if ws.us_blocked:
                flags.append("US 30d ⚠️")
            if ws.es_blocked:
                flags.append("ES 2m ⚠️")
            check = ", ".join(flags) if flags else "clear (lookback)"
            lines.append(
                f"| {est.lot.symbol} | {est.lot.open_date.isoformat() if est.lot.open_date else '?'} | "
                f"{_eur(est.gain_eur)} | {_eur(-est.effective_tax)} | {check} |"
            )
        lines.append("")
        lines.append("Lookback only: after selling, do **not** repurchase the same security for "
                     "30 days (US) / 2 months (Spain) or the loss is disallowed.")
    lines.append("")

    # --- Warnings ---
    warnings: list[str] = []
    pfics = sorted({e.lot.symbol for e in estimates if looks_like_pfic(e.lot)})
    if pfics:
        warnings.append(
            f"**Possible PFICs held: {', '.join(pfics)}.** Non-US funds/ETFs are punitively "
            "taxed for US citizens (Form 8621). Review with your US tax advisor."
        )
    unassigned = sorted(
        {e.lot.symbol for e in estimates}
        - {t.upper() for spec in targets["asset_classes"].values() for t in spec.get("tickers", [])}
    )
    if unassigned:
        warnings.append(f"Unclassified tickers (add to config/targets.yaml): {', '.join(unassigned)}.")
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("---")
    lines.append("*Informational only — estimates use simplified ES/US tax models "
                 "(today's FX for cost basis, max(ES,US) FTC approximation). Not tax advice.*")
    return "\n".join(lines) + "\n"

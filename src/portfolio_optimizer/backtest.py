"""Walk-forward backtest: HRP vs equal-weight vs holding current weights.

Monthly rebalance, trailing-window estimation, no look-ahead. Deliberately
ignores taxes and fees — it validates the *allocation engine*, not the trade
generator, so treat the spread between strategies as an upper bound.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .optimizer import TRADING_DAYS, hrp_weights


@dataclass
class StrategyResult:
    name: str
    cagr: float
    ann_vol: float
    sharpe: float
    max_drawdown: float


def _metrics(name: str, daily_returns: pd.Series) -> StrategyResult:
    wealth = (1 + daily_returns).cumprod()
    years = len(daily_returns) / TRADING_DAYS
    cagr = float(wealth.iloc[-1] ** (1 / years) - 1) if years > 0 else 0.0
    vol = float(daily_returns.std() * np.sqrt(TRADING_DAYS))
    sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(TRADING_DAYS)) if daily_returns.std() else 0.0
    drawdown = float((wealth / wealth.cummax() - 1).min())
    return StrategyResult(name, cagr, vol, sharpe, drawdown)


def walk_forward(
    returns: pd.DataFrame,
    current_weights: dict[str, float],
    window_days: int = 504,
    max_weight: float | None = None,
) -> list[StrategyResult]:
    """Monthly-rebalanced HRP vs equal weight vs static current weights."""
    if len(returns) <= window_days + TRADING_DAYS // 2:
        raise RuntimeError(
            f"Need > {window_days + TRADING_DAYS // 2} days of returns, have {len(returns)}"
        )
    symbols = list(returns.columns)
    month_starts = returns.index[window_days:].to_series().groupby(
        [returns.index[window_days:].year, returns.index[window_days:].month]
    ).first()

    hrp_daily, ew_daily = [], []
    ew = np.full(len(symbols), 1 / len(symbols))
    for i, start in enumerate(month_starts):
        end = month_starts.iloc[i + 1] if i + 1 < len(month_starts) else None
        train = returns.loc[:start].iloc[:-1].tail(window_days)
        weights = hrp_weights(train, max_weight=max_weight)
        w = np.array([weights[s] for s in symbols])
        period = returns.loc[start:end]
        if end is not None:
            period = period.iloc[:-1]
        hrp_daily.append(period @ w)
        ew_daily.append(period @ ew)

    hrp_series = pd.concat(hrp_daily)
    ew_series = pd.concat(ew_daily)
    held = np.array([current_weights.get(s, 0.0) for s in symbols])
    if held.sum() > 0:
        held = held / held.sum()
    current_series = returns.loc[hrp_series.index] @ held

    return [
        _metrics("HRP (monthly rebalance)", hrp_series),
        _metrics("Equal weight (monthly rebalance)", ew_series),
        _metrics("Current weights (buy & hold)", current_series),
    ]


def backtest_markdown(results: list[StrategyResult], n_days: int) -> str:
    lines = [
        f"Walk-forward, {n_days} trading days out-of-sample, monthly rebalance, "
        "taxes/fees ignored (validates allocation only).",
        "",
        "| Strategy | CAGR | Ann. vol | Sharpe | Max drawdown |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {r.cagr * 100:.1f}% | {r.ann_vol * 100:.1f}% | "
            f"{r.sharpe:.2f} | {r.max_drawdown * 100:.1f}% |"
        )
    return "\n".join(lines)

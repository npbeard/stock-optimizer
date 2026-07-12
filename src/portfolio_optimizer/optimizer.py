"""Target weights: Hierarchical Risk Parity on a Ledoit-Wolf covariance.

HRP (Lopez de Prado 2016) clusters assets by correlation and allocates
inverse-variance down the tree — no expected-return estimates, no matrix
inversion, materially better out-of-sample variance than mean-variance
at this portfolio size. Ledoit-Wolf shrinkage stabilizes the covariance
estimate for short histories.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf as SkLedoitWolf
from skfolio.moments import LedoitWolf
from skfolio.optimization import HierarchicalRiskParity
from skfolio.prior import EmpiricalPrior

TRADING_DAYS = 252


def hrp_weights(returns: pd.DataFrame, max_weight: float | None = None) -> dict[str, float]:
    """Risky-asset weights (sum to 1) from daily returns."""
    model = HierarchicalRiskParity(
        prior_estimator=EmpiricalPrior(covariance_estimator=LedoitWolf()),
        max_weights=max_weight,
    )
    model.fit(returns)
    return dict(zip(returns.columns, (float(w) for w in model.weights_)))


def target_weights(
    returns: pd.DataFrame, cash_target: float, max_weight: float | None = None
) -> dict[str, float]:
    """Full target allocation: HRP over risky assets scaled to (1 - cash_target),
    plus a 'CASH' entry."""
    risky = hrp_weights(returns, max_weight=max_weight)
    scale = 1.0 - cash_target
    weights = {symbol: w * scale for symbol, w in risky.items()}
    weights["CASH"] = cash_target
    return weights


def annualized_covariance(returns: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf shrunk covariance of daily returns, annualized."""
    lw = SkLedoitWolf().fit(returns.values)
    cov = lw.covariance_ * TRADING_DAYS
    return pd.DataFrame(cov, index=returns.columns, columns=returns.columns)


def portfolio_volatility(weights: np.ndarray, cov: np.ndarray) -> float:
    return float(np.sqrt(weights @ cov @ weights))

"""Daily price history: yfinance -> SQLite -> returns matrix.

Prices are stored auto-adjusted (splits + dividends) in the security's
trading currency. Covariance/optimization runs on these local-currency
returns; for a EUR investor holding USD-traded names the common EURUSD
leg shifts all of them together, which HRP treats as shared risk anyway.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

PRICES_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    adj_close REAL NOT NULL,
    PRIMARY KEY (symbol, date)
);
"""


def update_prices(conn: sqlite3.Connection, symbols: list[str], lookback_days: int = 1260) -> int:
    """Download adjusted closes and upsert. Returns number of rows stored."""
    conn.executescript(PRICES_SCHEMA)
    start = (date.today() - timedelta(days=lookback_days + 30)).isoformat()
    data = yf.download(
        sorted(set(symbols)), start=start, auto_adjust=True,
        progress=False, group_by="column",
    )
    closes = data["Close"]
    if isinstance(closes, pd.Series):  # single symbol
        closes = closes.to_frame(symbols[0])

    rows = [
        (symbol, day.date().isoformat(), float(value))
        for symbol in closes.columns
        for day, value in closes[symbol].dropna().items()
    ]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?)", rows)
    return len(rows)


def load_prices(conn: sqlite3.Connection, symbols: list[str]) -> pd.DataFrame:
    """Wide DataFrame of adjusted closes indexed by date, one column per symbol."""
    conn.executescript(PRICES_SCHEMA)
    placeholders = ",".join("?" * len(symbols))
    df = pd.read_sql_query(
        f"SELECT symbol, date, adj_close FROM prices WHERE symbol IN ({placeholders})",
        conn, params=list(symbols),
    )
    if df.empty:
        return pd.DataFrame(columns=list(symbols))
    wide = df.pivot(index="date", columns="symbol", values="adj_close").sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide


def returns_matrix(conn: sqlite3.Connection, symbols: list[str], lookback_days: int) -> pd.DataFrame:
    """Daily simple returns for the trailing window, rows with any NaN dropped."""
    closes = load_prices(conn, symbols)
    if closes.empty:
        raise RuntimeError("No price history — run `portfolio-optimizer prices` first.")
    closes = closes.tail(lookback_days + 1)
    returns = closes.pct_change().dropna(how="any")
    missing = [s for s in symbols if s not in returns.columns or returns[s].isna().all()]
    if missing:
        raise RuntimeError(f"No usable price history for: {', '.join(missing)}")
    return returns[list(symbols)]

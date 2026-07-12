"""SQLite persistence: daily lot snapshots, trade history, cash balances."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .models import CashBalance, FlexStatement, Lot, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
    report_date TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    description TEXT,
    isin TEXT,
    asset_category TEXT,
    currency TEXT,
    fx_rate_to_base REAL,
    open_date TEXT,
    quantity REAL,
    cost_basis_money REAL,
    mark_price REAL,
    position_value REAL
);
CREATE INDEX IF NOT EXISTS idx_lots_date ON lots (report_date);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    account_id TEXT,
    symbol TEXT,
    isin TEXT,
    asset_category TEXT,
    currency TEXT,
    fx_rate_to_base REAL,
    trade_date TEXT,
    buy_sell TEXT,
    quantity REAL,
    trade_price REAL,
    fifo_pnl_realized REAL
);

CREATE TABLE IF NOT EXISTS cash (
    report_date TEXT NOT NULL,
    account_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount REAL,
    fx_rate_to_base REAL,
    PRIMARY KEY (report_date, account_id, currency)
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def store_statement(conn: sqlite3.Connection, stmt: FlexStatement) -> None:
    day = stmt.report_date.isoformat()
    with conn:
        # Snapshot semantics: replace the day's lots/cash, upsert trades.
        conn.execute("DELETE FROM lots WHERE report_date = ?", (day,))
        conn.executemany(
            "INSERT INTO lots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    day, l.account_id, l.symbol, l.description, l.isin,
                    l.asset_category, l.currency, l.fx_rate_to_base,
                    l.open_date.isoformat() if l.open_date else None,
                    l.quantity, l.cost_basis_money, l.mark_price, l.position_value,
                )
                for l in stmt.lots
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    t.trade_id, t.account_id, t.symbol, t.isin, t.asset_category,
                    t.currency, t.fx_rate_to_base, t.trade_date.isoformat(),
                    t.buy_sell, t.quantity, t.trade_price, t.fifo_pnl_realized,
                )
                for t in stmt.trades
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO cash VALUES (?,?,?,?,?)",
            [
                (day, c.account_id, c.currency, c.amount, c.fx_rate_to_base)
                for c in stmt.cash
            ],
        )


def latest_report_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT MAX(report_date) FROM lots").fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def load_statement(conn: sqlite3.Connection, day: date) -> FlexStatement:
    iso = day.isoformat()
    lots = [
        Lot(
            report_date=day, account_id=r[1], symbol=r[2], description=r[3] or "",
            isin=r[4] or "", asset_category=r[5] or "", currency=r[6] or "EUR",
            fx_rate_to_base=r[7] or 1.0,
            open_date=date.fromisoformat(r[8]) if r[8] else None,
            quantity=r[9], cost_basis_money=r[10], mark_price=r[11], position_value=r[12],
        )
        for r in conn.execute("SELECT * FROM lots WHERE report_date = ?", (iso,))
    ]
    trades = [
        Trade(
            trade_id=r[0], account_id=r[1], symbol=r[2], isin=r[3] or "",
            asset_category=r[4] or "", currency=r[5] or "EUR", fx_rate_to_base=r[6] or 1.0,
            trade_date=date.fromisoformat(r[7]), buy_sell=r[8],
            quantity=r[9], trade_price=r[10], fifo_pnl_realized=r[11],
        )
        for r in conn.execute("SELECT * FROM trades")
    ]
    cash = [
        CashBalance(report_date=day, account_id=r[1], currency=r[2], amount=r[3], fx_rate_to_base=r[4] or 1.0)
        for r in conn.execute("SELECT * FROM cash WHERE report_date = ?", (iso,))
    ]
    account_id = lots[0].account_id if lots else (cash[0].account_id if cash else "")
    return FlexStatement(account_id=account_id, report_date=day, lots=lots, trades=trades, cash=cash)

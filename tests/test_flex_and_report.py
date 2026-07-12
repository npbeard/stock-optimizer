from datetime import date
from pathlib import Path

import pytest
import yaml

from portfolio_optimizer import db
from portfolio_optimizer.drift import compute_allocations
from portfolio_optimizer.flex import parse_statement
from portfolio_optimizer.report import build_report

FIXTURE = Path(__file__).parent / "fixtures" / "sample_flex.xml"
CONFIG_DIR = Path(__file__).parents[1] / "config"


@pytest.fixture
def stmt():
    return parse_statement(FIXTURE.read_text())


@pytest.fixture
def settings():
    return yaml.safe_load((CONFIG_DIR / "settings.example.yaml").read_text())


@pytest.fixture
def targets():
    return yaml.safe_load((CONFIG_DIR / "targets.example.yaml").read_text())


def test_parse_drops_summary_rows_when_lots_exist(stmt):
    aapl = [l for l in stmt.lots if l.symbol == "AAPL"]
    assert len(aapl) == 2  # SUMMARY row for AAPL dropped
    assert sum(l.quantity for l in aapl) == 150
    assert all(l.open_date is not None for l in aapl)


def test_parse_basics(stmt):
    assert stmt.account_id == "U1234567"
    assert stmt.report_date == date(2026, 7, 10)
    assert {l.symbol for l in stmt.lots} == {"AAPL", "MSFT", "VUAA"}
    assert len(stmt.trades) == 2
    # BASE_SUMMARY excluded from cash
    assert {c.currency for c in stmt.cash} == {"EUR", "USD"}


def test_eur_conversion(stmt):
    msft = next(l for l in stmt.lots if l.symbol == "MSFT")
    assert msft.value_eur == pytest.approx(20000 * 0.86)
    assert msft.unrealized_eur == pytest.approx((20000 - 10000) * 0.86)


def test_db_roundtrip(tmp_path, stmt):
    conn = db.connect(tmp_path / "test.db")
    db.store_statement(conn, stmt)
    db.store_statement(conn, stmt)  # idempotent re-run same day
    day = db.latest_report_date(conn)
    loaded = db.load_statement(conn, day)
    assert len(loaded.lots) == len(stmt.lots)
    assert len(loaded.trades) == len(stmt.trades)
    assert sum(c.value_eur for c in loaded.cash) == pytest.approx(15000 + 5000 * 0.86)


def test_allocations_include_cash_and_unassigned(stmt, targets):
    allocations = {a.name: a for a in compute_allocations(stmt.lots, stmt.cash, targets)}
    assert allocations["cash"].value_eur == pytest.approx(15000 + 5000 * 0.86)
    # VUAA is not in the default targets -> unassigned and flagged
    assert "VUAA" in allocations["unassigned"].symbols
    assert allocations["unassigned"].breached(0.05, 0.25)
    total = sum(a.value_eur for a in allocations.values())
    assert sum(a.weight for a in allocations.values()) == pytest.approx(1.0)
    assert total > 0


def test_report_contents(stmt, settings, targets):
    report = build_report(stmt, settings, targets)
    assert "Portfolio report — 2026-07-10" in report
    # The short-term AAPL lot has a -860 EUR loss -> harvest candidate,
    # and the 2026-06-25 AAPL buy is inside both wash-sale lookbacks.
    assert "US 30d ⚠️" in report and "ES 2m ⚠️" in report
    # VUAA is an Irish-domiciled fund -> PFIC warning.
    assert "Possible PFICs held: VUAA" in report
    assert "Not tax advice" in report

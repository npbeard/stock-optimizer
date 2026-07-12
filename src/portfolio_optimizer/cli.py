"""CLI: fetch from IBKR, ingest a local XML, and generate the daily report.

Usage:
  portfolio-optimizer fetch                 # pull Flex statement -> data/portfolio.db
  portfolio-optimizer ingest statement.xml  # load a downloaded Flex XML (testing/backfill)
  portfolio-optimizer prices                # update daily price history (yfinance)
  portfolio-optimizer report                # report for the latest stored day (+ trade recs)
  portfolio-optimizer recommend             # just the tax-aware trade recommendations
  portfolio-optimizer backtest              # walk-forward: HRP vs equal weight vs current
  portfolio-optimizer run                   # fetch + prices + report
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from . import db, flex
from .report import build_report

# Project root = where you run the command from (or set PORTFOLIO_OPTIMIZER_HOME).
# config/, data/ and reports/ live under it.
PROJECT_ROOT = Path(os.environ.get("PORTFOLIO_OPTIMIZER_HOME", Path.cwd()))
DB_PATH = PROJECT_ROOT / "data" / "portfolio.db"
REPORTS_DIR = PROJECT_ROOT / "reports"
CONFIG_DIR = PROJECT_ROOT / "config"


def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        sys.exit(f"Missing {path} — copy config/{path.stem}.example.yaml to "
                 f"config/{name} and edit it.")
    with open(path) as f:
        return yaml.safe_load(f)


def _load_env_file() -> None:
    """Minimal .env loader so we don't need python-dotenv."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def cmd_fetch() -> None:
    _load_env_file()
    token = os.environ.get("IBKR_FLEX_TOKEN")
    query_id = os.environ.get("IBKR_FLEX_QUERY_ID")
    if not token or not query_id:
        sys.exit("Set IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID (see .env.example).")
    print("Requesting Flex statement from IBKR...")
    xml_text = flex.fetch_statement_xml(token, query_id)
    stmt = flex.parse_statement(xml_text)
    conn = db.connect(DB_PATH)
    db.store_statement(conn, stmt)
    print(f"Stored {len(stmt.lots)} lots, {len(stmt.trades)} trades, "
          f"{len(stmt.cash)} cash balances for {stmt.report_date}.")


def cmd_ingest(xml_path: str) -> None:
    stmt = flex.parse_statement(Path(xml_path).read_text())
    conn = db.connect(DB_PATH)
    db.store_statement(conn, stmt)
    print(f"Ingested {xml_path}: {len(stmt.lots)} lots, {len(stmt.trades)} trades "
          f"for {stmt.report_date}.")


def _latest_statement(conn):
    day = db.latest_report_date(conn)
    if day is None:
        sys.exit("No data yet — run `portfolio-optimizer fetch` (or `ingest <xml>`) first.")
    return db.load_statement(conn, day)


def cmd_prices() -> None:
    from . import prices
    from .recommend import universe_symbols

    settings = _load_yaml("settings.yaml")
    conn = db.connect(DB_PATH)
    stmt = _latest_statement(conn)
    symbols = universe_symbols(stmt, settings)
    # Fetch ~5y so the walk-forward backtest has out-of-sample room beyond
    # the covariance lookback.
    calendar_days = max(1825, int(settings["optimizer"]["lookback_days"]) * 2)
    n = prices.update_prices(conn, symbols, calendar_days)
    print(f"Stored {n} price rows for {len(symbols)} symbols.")


def _recommendation_section(conn, stmt, settings) -> str:
    from .recommend import build_recommendation

    try:
        _, _, section = build_recommendation(conn, stmt, settings, _load_yaml("targets.yaml"))
        return section
    except RuntimeError as exc:
        return f"## Recommended trades\n\n_Unavailable: {exc}_\n"


def cmd_report() -> None:
    settings = _load_yaml("settings.yaml")
    conn = db.connect(DB_PATH)
    stmt = _latest_statement(conn)
    report = build_report(stmt, settings, _load_yaml("targets.yaml"))
    report += "\n" + _recommendation_section(conn, stmt, settings)
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{stmt.report_date.isoformat()}.md"
    out.write_text(report)
    print(report)
    print(f"Saved to {out}", file=sys.stderr)


def cmd_recommend() -> None:
    settings = _load_yaml("settings.yaml")
    conn = db.connect(DB_PATH)
    print(_recommendation_section(conn, _latest_statement(conn), settings))


def cmd_backtest() -> None:
    from .backtest import backtest_markdown, walk_forward
    from .prices import returns_matrix
    from .recommend import universe_symbols

    settings = _load_yaml("settings.yaml")
    opt = settings["optimizer"]
    conn = db.connect(DB_PATH)
    stmt = _latest_statement(conn)
    symbols = universe_symbols(stmt, settings)
    returns = returns_matrix(conn, symbols, 100_000)  # all stored history
    current: dict[str, float] = {}
    for lot in stmt.lots:
        current[lot.symbol] = current.get(lot.symbol, 0.0) + lot.value_eur
    results = walk_forward(
        returns, current, window_days=int(opt["backtest_window_days"]),
        max_weight=opt.get("max_weight"),
    )
    print(backtest_markdown(results, len(returns) - int(opt["backtest_window_days"])))


def main() -> None:
    parser = argparse.ArgumentParser(prog="portfolio-optimizer", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="Pull today's Flex statement from IBKR")
    ingest = sub.add_parser("ingest", help="Load a locally downloaded Flex XML file")
    ingest.add_argument("xml_path")
    sub.add_parser("prices", help="Update daily price history from yfinance")
    sub.add_parser("report", help="Generate the report for the latest stored day")
    sub.add_parser("recommend", help="Print tax-aware trade recommendations")
    sub.add_parser("backtest", help="Walk-forward backtest of the allocation engine")
    sub.add_parser("run", help="fetch + prices + report")

    args = parser.parse_args()
    if args.command == "fetch":
        cmd_fetch()
    elif args.command == "ingest":
        cmd_ingest(args.xml_path)
    elif args.command == "prices":
        cmd_prices()
    elif args.command == "report":
        cmd_report()
    elif args.command == "recommend":
        cmd_recommend()
    elif args.command == "backtest":
        cmd_backtest()
    elif args.command == "run":
        cmd_fetch()
        cmd_prices()
        cmd_report()


if __name__ == "__main__":
    main()

"""CLI: fetch from IBKR, ingest a local XML, and generate the daily report.

Usage:
  portfolio-optimizer fetch                 # pull Flex statement -> data/portfolio.db
  portfolio-optimizer ingest statement.xml  # load a downloaded Flex XML (testing/backfill)
  portfolio-optimizer report                # report for the latest stored day
  portfolio-optimizer run                   # fetch + report
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


def cmd_report() -> None:
    conn = db.connect(DB_PATH)
    day = db.latest_report_date(conn)
    if day is None:
        sys.exit("No data yet — run `portfolio-optimizer fetch` (or `ingest <xml>`) first.")
    stmt = db.load_statement(conn, day)
    report = build_report(stmt, _load_yaml("settings.yaml"), _load_yaml("targets.yaml"))
    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"{day.isoformat()}.md"
    out.write_text(report)
    print(report)
    print(f"Saved to {out}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(prog="portfolio-optimizer", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="Pull today's Flex statement from IBKR")
    ingest = sub.add_parser("ingest", help="Load a locally downloaded Flex XML file")
    ingest.add_argument("xml_path")
    sub.add_parser("report", help="Generate the report for the latest stored day")
    sub.add_parser("run", help="fetch + report")

    args = parser.parse_args()
    if args.command == "fetch":
        cmd_fetch()
    elif args.command == "ingest":
        cmd_ingest(args.xml_path)
    elif args.command == "report":
        cmd_report()
    elif args.command == "run":
        cmd_fetch()
        cmd_report()


if __name__ == "__main__":
    main()

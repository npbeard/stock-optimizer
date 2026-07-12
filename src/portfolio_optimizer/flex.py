"""IBKR Flex Web Service client and statement parser.

Flow: SendRequest(token, queryId) -> reference code -> GetStatement(token, ref)
-> XML statement. Statement generation is asynchronous, so GetStatement is
polled until ready.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date, datetime

import requests

from .models import CashBalance, FlexStatement, Lot, Trade

FLEX_BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
IN_PROGRESS_CODES = {"1019"}  # statement generation in progress
USER_AGENT = {"User-Agent": "portfolio-optimizer/0.1"}


class FlexError(RuntimeError):
    pass


def fetch_statement_xml(token: str, query_id: str, max_wait_seconds: int = 300) -> str:
    resp = requests.get(
        f"{FLEX_BASE}/SendRequest",
        params={"t": token, "q": query_id, "v": "3"},
        headers=USER_AGENT,
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    if root.findtext("Status") != "Success":
        raise FlexError(
            f"SendRequest failed: {root.findtext('ErrorCode')} {root.findtext('ErrorMessage')}"
        )
    reference_code = root.findtext("ReferenceCode")

    deadline = time.monotonic() + max_wait_seconds
    delay = 5.0
    while True:
        resp = requests.get(
            f"{FLEX_BASE}/GetStatement",
            params={"t": token, "q": reference_code, "v": "3"},
            headers=USER_AGENT,
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.text
        root = ET.fromstring(text)
        if root.tag == "FlexQueryResponse":
            return text
        error_code = root.findtext("ErrorCode")
        if error_code in IN_PROGRESS_CODES:
            if time.monotonic() > deadline:
                raise FlexError("Timed out waiting for statement generation")
            time.sleep(delay)
            delay = min(delay * 1.5, 30)
            continue
        raise FlexError(f"GetStatement failed: {error_code} {root.findtext('ErrorMessage')}")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.split(";")[0].split(",")[0].strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


def _f(el: ET.Element, attr: str, default: float = 0.0) -> float:
    raw = el.get(attr)
    if raw in (None, ""):
        return default
    return float(raw)


def parse_statement(xml_text: str) -> FlexStatement:
    root = ET.fromstring(xml_text)
    stmt = root.find(".//FlexStatement")
    if stmt is None:
        raise FlexError("No FlexStatement element found in response")

    account_id = stmt.get("accountId", "")
    report_date = _parse_date(stmt.get("toDate")) or date.today()

    # With "Lot" level of detail IBKR emits both SUMMARY and LOT rows;
    # keep only LOT rows for symbols that have them to avoid double counting.
    position_rows = list(stmt.iterfind(".//OpenPositions/OpenPosition"))
    symbols_with_lots = {
        p.get("symbol") for p in position_rows if p.get("levelOfDetail", "").upper() == "LOT"
    }
    lots: list[Lot] = []
    for pos in position_rows:
        detail = pos.get("levelOfDetail", "SUMMARY").upper()
        if detail == "SUMMARY" and pos.get("symbol") in symbols_with_lots:
            continue
        lots.append(
            Lot(
                report_date=_parse_date(pos.get("reportDate")) or report_date,
                account_id=pos.get("accountId", account_id),
                symbol=pos.get("symbol", ""),
                description=pos.get("description", ""),
                isin=pos.get("isin", ""),
                asset_category=pos.get("assetCategory", ""),
                currency=pos.get("currency", "EUR"),
                fx_rate_to_base=_f(pos, "fxRateToBase", 1.0),
                open_date=_parse_date(pos.get("openDateTime") or pos.get("holdingPeriodDateTime")),
                quantity=_f(pos, "position"),
                cost_basis_money=_f(pos, "costBasisMoney"),
                mark_price=_f(pos, "markPrice"),
                position_value=_f(pos, "positionValue"),
            )
        )

    trades: list[Trade] = []
    for tr in stmt.iterfind(".//Trades/Trade"):
        trades.append(
            Trade(
                trade_id=tr.get("tradeID", ""),
                account_id=tr.get("accountId", account_id),
                symbol=tr.get("symbol", ""),
                isin=tr.get("isin", ""),
                asset_category=tr.get("assetCategory", ""),
                currency=tr.get("currency", "EUR"),
                fx_rate_to_base=_f(tr, "fxRateToBase", 1.0),
                trade_date=_parse_date(tr.get("tradeDate")) or report_date,
                buy_sell=tr.get("buySell", ""),
                quantity=_f(tr, "quantity"),
                trade_price=_f(tr, "tradePrice"),
                fifo_pnl_realized=_f(tr, "fifoPnlRealized"),
            )
        )

    cash: list[CashBalance] = []
    for cur in stmt.iterfind(".//CashReport/CashReportCurrency"):
        currency = cur.get("currency", "")
        if currency == "BASE_SUMMARY":
            continue
        cash.append(
            CashBalance(
                report_date=report_date,
                account_id=cur.get("accountId", account_id),
                currency=currency,
                amount=_f(cur, "endingCash"),
                fx_rate_to_base=_f(cur, "fxRateToBase", 1.0),
            )
        )

    return FlexStatement(account_id=account_id, report_date=report_date, lots=lots, trades=trades, cash=cash)

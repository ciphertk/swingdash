"""
Parsers for NSE's reference files. Pure text/JSON in, domain objects out,
so they are tested against captured real files in tests/fixtures/nse.

Every CSV parser checks its required columns first: if NSE reshapes a file,
failing loudly beats silently showing wrong bands or surveillance stages.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections.abc import Iterator
from typing import Any

from swingdash.adapters.nse.http import NseFormatError
from swingdash.domain.securities import (
    EQ_SERIES,
    BandEntry,
    Etf,
    IndexRow,
    ListedEquity,
    PriceBand,
    Surveillance,
)

# --- CSV archives -------------------------------------------------------------


def parse_equity_list(text: str) -> list[ListedEquity]:
    """
    EQUITY_L.csv, every series. Used only for listing details (name, ISIN,
    listing date): its SERIES column lags a day behind the price band list,
    so a stock just moved BE -> EQ still reads BE here. Which stocks are EQ
    is decided by the price band list instead.
    """
    return [
        ListedEquity(
            symbol=row["SYMBOL"],
            name=row["NAME OF COMPANY"],
            isin=row["ISIN NUMBER"] or None,
            listed_on=_date(row["DATE OF LISTING"]),
        )
        for row in _csv_rows(text, {"SYMBOL", "NAME OF COMPANY", "DATE OF LISTING", "ISIN NUMBER"})
    ]


def parse_price_bands(text: str) -> list[BandEntry]:
    """
    sec_list.csv, EQ-series rows only - the securities tradable in EQ on the
    next session, with their bands. This includes ETFs, which trade as EQ.
    """
    return [
        BandEntry(
            symbol=row["Symbol"], name=row["Security Name"], band=PriceBand.parse(row["Band"])
        )
        for row in _csv_rows(text, {"Symbol", "Series", "Security Name", "Band"})
        if row["Series"] == EQ_SERIES
    ]


def parse_etf_list(text: str) -> list[Etf]:
    """
    eq_etfseclist.csv. This file's own name columns are poor (an AMC code, or
    sometimes just the index); the price band list's full fund name is
    preferred when the two are joined.
    """
    return [
        Etf(
            symbol=row["Symbol"],
            name=row["Underlying Asset"] or row["SecurityName"],
            underlying=row["Underlying Key"],
            asset_class=row["ETF Underlying"],
            isin=row["ISINNumber"] or None,
            listed_on=_date(row["DateofListing"]),
        )
        for row in _csv_rows(
            text,
            {"Symbol", "Underlying Asset", "SecurityName", "DateofListing", "ISINNumber"}
            | {"ETF Underlying", "Underlying Key"},
        )
    ]


# REG1_IND column-name prefixes -> Surveillance field. Stage values are the
# stage number; 100 means the measure doesn't apply.
_REG_COLUMNS = {
    "GSM": "gsm",
    "ESM": "esm",
    "Long_Term_Additional_Surveillance_Measure": "ltasm",
    "Short_Term_Additional_Surveillance_Measure": "stasm",
    "Insolvency_Resolution_Process": "ibc",
}
_NOT_APPLICABLE = "100"


def parse_surveillance_file(text: str) -> dict[str, Surveillance]:
    """
    REG1_IND{DDMMYY}.csv - the regulatory indicators in force on that date.
    Only the fallback: the report endpoints already carry the next session's
    lists, which this file lags by a day.
    """
    reader = csv.reader(io.StringIO(text))
    header = [name.strip() for name in next(reader, [])]
    columns: dict[str, int] = {}
    for prefix, field in _REG_COLUMNS.items():
        index = next((i for i, name in enumerate(header) if name.startswith(prefix)), None)
        if index is None:
            raise NseFormatError(f"surveillance file has no {prefix} column")
        columns[field] = index
    if "Symbol" not in header:
        raise NseFormatError("surveillance file has no Symbol column")
    symbol_at = header.index("Symbol")

    result: dict[str, Surveillance] = {}
    for row in reader:
        if len(row) < len(header):
            continue
        stages = {
            field: int(row[index])
            for field, index in columns.items()
            if row[index].strip() not in ("", _NOT_APPLICABLE)
        }
        if stages:
            result[row[symbol_at].strip()] = Surveillance(**stages)
    return result


# --- JSON reports -------------------------------------------------------------

_MEASURE = re.compile(r"^(GSM|ESM|LTASM|STASM|IBC)\s*(?:-\s*)?(Receipt|VI|IV|V|III|II|I|0)$")
_STAGES = {"0": 0, "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "Receipt": 0}


def parse_surveillance_code(code: str) -> Surveillance:
    """
    NSE's combined survCode, e.g. "LTASM - I (13)", "ESM II & GSM 0 (37)",
    "GSM IV & IBC - Receipt (66)". Unknown parts are ignored rather than
    guessed.
    """
    stages: dict[str, int] = {}
    for part in re.sub(r"\s*\(\d+\)\s*$", "", code).split("&"):
        match = _MEASURE.match(part.strip())
        if match:
            stages[match.group(1).lower()] = _STAGES[match.group(2)]
    return Surveillance(**stages)


def parse_surveillance_reports(
    asm: Any, gsm: Any, esm: Any
) -> tuple[dict[str, Surveillance], dt.date | None]:
    """reportASM (longterm/shortterm), reportGSM and reportESM, merged per symbol."""
    try:
        records: list[dict[str, Any]] = [
            *asm["longterm"]["data"],
            *asm["shortterm"]["data"],
            *gsm,
            *esm,
        ]
    except (KeyError, TypeError) as exc:
        raise NseFormatError("unexpected surveillance report shape") from exc

    result: dict[str, Surveillance] = {}
    as_of: dt.date | None = None
    for record in records:
        symbol, code = record.get("symbol"), record.get("survCode")
        if not symbol or not code:
            continue
        stage = parse_surveillance_code(code)
        result[symbol] = result[symbol].merge(stage) if symbol in result else stage
        stamp = record.get("asmTime") or record.get("gsmTime") or record.get("esmTime")
        as_of = as_of or _date(str(stamp)[:11] if stamp else "")
    return result, as_of


_CATEGORY_LABELS = {"INDICES ELIGIBLE IN DERIVATIVES": "Derivatives"}


def parse_all_indices(payload: Any) -> tuple[list[IndexRow], dt.date | None]:
    """api/allIndices."""
    try:
        entries: list[dict[str, Any]] = payload["data"]
    except (KeyError, TypeError) as exc:
        raise NseFormatError("unexpected allIndices shape") from exc
    rows = [
        IndexRow(
            name=entry["index"],
            category=_category(entry.get("key", "")),
            last=_float(entry.get("last")),
            change_pct=_float(entry.get("percentChange")),
            pe=_positive(entry.get("pe")),
            pb=_positive(entry.get("pb")),
            dividend_yield=_positive(entry.get("dy")),
            year_high=_positive(entry.get("yearHigh")),
            year_low=_positive(entry.get("yearLow")),
            advances=_int(entry.get("advances")),
            declines=_int(entry.get("declines")),
        )
        for entry in entries
        if entry.get("index")
    ]
    return rows, _date(str(payload.get("timestamp", ""))[:11])


# --- helpers ------------------------------------------------------------------


def _csv_rows(text: str, required: set[str]) -> Iterator[dict[str, str]]:
    reader = csv.reader(io.StringIO(text))
    header = [name.strip() for name in next(reader, [])]
    missing = required - set(header)
    if missing:
        raise NseFormatError(f"missing columns {sorted(missing)}")
    for row in reader:
        if row:
            yield {name: value.strip() for name, value in zip(header, row, strict=False)}


def _date(text: str) -> dt.date | None:
    for fmt in ("%d-%b-%Y", "%d-%b-%y"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _float(value: object) -> float | None:
    if value is None or value in ("", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _positive(value: object) -> float | None:
    """For ratios and ranges, where NSE writes 0 to mean 'not applicable'."""
    number = _float(value)
    return number if number else None


def _int(value: object) -> int | None:
    number = _float(value)
    return None if number is None else int(number)


def _category(key: str) -> str:
    return _CATEGORY_LABELS.get(key) or key.removesuffix(" INDICES").title()

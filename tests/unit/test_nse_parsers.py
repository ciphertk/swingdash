"""Parsers against trimmed real NSE files captured on 13 Sep 2026."""

import datetime as dt
import json
from pathlib import Path

import pytest

from swingdash.adapters.nse.http import NseFormatError
from swingdash.adapters.nse.parsers import (
    parse_all_indices,
    parse_equity_list,
    parse_etf_list,
    parse_price_bands,
    parse_surveillance_code,
    parse_surveillance_file,
    parse_surveillance_reports,
)
from swingdash.domain.securities import PriceBand, Surveillance

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nse"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _json(name: str):
    return json.loads(_text(name))


def test_equity_list_keeps_every_series_for_details_lookup():
    listed = {e.symbol: e for e in parse_equity_list(_text("EQUITY_L.csv"))}
    reliance = listed["RELIANCE"]
    assert reliance.name == "Reliance Industries Limited"
    assert reliance.isin == "INE002A01018"
    assert reliance.listed_on == dt.date(1995, 11, 29)
    # Still BE here although it trades EQ next session - lookup must still find it.
    assert "SADBHIN" in listed


def test_price_bands_are_eq_series_only_and_parse_no_band():
    bands = {b.symbol: b.band for b in parse_price_bands(_text("sec_list.csv"))}
    assert bands["RELIANCE"] is PriceBand.NO_BAND
    assert bands["RAYMOND"] is PriceBand.P20
    assert bands["ATALREAL"] is PriceBand.P10
    assert bands["SADBHIN"] is PriceBand.P5  # moved to EQ
    assert "SREEL" not in bands  # moved to BE
    assert "3IINFOLTD" not in bands  # BE
    assert bands["NIFTYBEES"] is PriceBand.NO_BAND  # ETFs trade as EQ


def test_etf_list():
    etfs = {e.symbol: e for e in parse_etf_list(_text("eq_etfseclist.csv"))}
    gold = etfs["GOLDBEES"]
    assert gold.asset_class == "COMMODITY"
    assert gold.underlying == "GOLD"
    assert gold.isin == "INF204KB17I5"
    assert gold.listed_on == dt.date(2007, 3, 19)  # two-digit year


@pytest.mark.parametrize(
    ("code", "expected", "labels"),
    [
        ("LTASM - I (13)", Surveillance(ltasm=1), ("LTASM-I",)),
        ("STASM - II (12)", Surveillance(stasm=2), ("STASM-II",)),
        ("GSM - 0 (99)", Surveillance(gsm=0), ("GSM-0",)),
        ("GSM - VI (6)", Surveillance(gsm=6), ("GSM-VI",)),
        ("ESM II & GSM 0 (37)", Surveillance(gsm=0, esm=2), ("GSM-0", "ESM-II")),
        ("LTASM I & GSM 0 (50)", Surveillance(gsm=0, ltasm=1), ("GSM-0", "LTASM-I")),
        ("IBC - Receipt & GSM 0 (62)", Surveillance(gsm=0, ibc=0), ("GSM-0", "IBC")),
        ("IBC I & GSM 0 (58)", Surveillance(gsm=0, ibc=1), ("GSM-0", "IBC-I")),
        ("GSM IV & IBC - Receipt (66)", Surveillance(gsm=4, ibc=0), ("GSM-IV", "IBC")),
        ("SOMETHING NEW (77)", Surveillance(), ()),
    ],
)
def test_surveillance_codes(code, expected, labels):
    parsed = parse_surveillance_code(code)
    assert parsed == expected
    assert parsed.labels() == labels
    assert parsed.flagged == bool(labels)


def test_surveillance_reports_merge_per_symbol():
    stages, as_of = parse_surveillance_reports(
        _json("reportASM.json"), _json("reportGSM.json"), _json("reportESM.json")
    )
    assert as_of == dt.date(2026, 9, 11)
    assert stages["RAYMOND"] == Surveillance(stasm=1)
    assert stages["TBZ"] == Surveillance(ltasm=3)
    assert stages["AQYLON"] == Surveillance(gsm=0, ltasm=1)
    # Listed in both the GSM and ESM reports.
    assert stages["EUROTEXIND"] == Surveillance(gsm=0, esm=2)
    assert "RELIANCE" not in stages


def test_surveillance_reports_reject_an_unknown_shape():
    with pytest.raises(NseFormatError):
        parse_surveillance_reports({"data": []}, [], [])


def test_surveillance_file_fallback():
    stages = parse_surveillance_file(_text("REG1_IND110926.csv"))
    assert stages["TAKE"] == Surveillance(ltasm=2)
    assert stages["AGSTRA"] == Surveillance(gsm=0, ibc=0)
    assert stages["BYKE"] == Surveillance(esm=1)
    assert "RELIANCE" not in stages  # 100 everywhere = not under surveillance


def test_csv_with_missing_columns_fails_loudly():
    with pytest.raises(NseFormatError):
        parse_price_bands("Symbol,Series,Security Name\nRELIANCE,EQ,RELIANCE\n")
    with pytest.raises(NseFormatError):
        parse_surveillance_file("Symbol,Series,GSM\nRELIANCE,EQ,100\n")


def test_all_indices():
    rows, as_of = parse_all_indices(_json("allIndices.json"))
    assert as_of == dt.date(2026, 9, 11)
    by_name = {r.name: r for r in rows}
    nifty = by_name["NIFTY 50"]
    assert nifty.category == "Derivatives"
    assert nifty.last == 23398.1
    assert nifty.pe == 19.78
    assert (nifty.advances, nifty.declines) == (12, 37)
    assert by_name["NIFTY AUTO"].category == "Sectoral"
    gsec = by_name["NIFTY 8-13 YR G-SEC"]
    assert gsec.category == "Fixed Income"
    assert gsec.pe is None and gsec.year_high is None  # NSE writes 0 for n/a
    assert gsec.advances is None

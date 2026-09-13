import csv
import datetime as dt

from swingdash.ui.export import ExportTable, write_csv


def test_write_csv_writes_headers_and_rows(tmp_path):
    table = ExportTable(
        name="live-rvol", headers=["SYMBOL", "LTP"], rows=[["RELIANCE", 1257.5], ["TCS", None]]
    )
    path = write_csv(table, tmp_path, clock=lambda: dt.datetime(2026, 9, 15, 10, 32, 5))

    assert path.name == "live-rvol_2026-09-15_103205.csv"
    assert path.parent == tmp_path
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    assert rows == [["SYMBOL", "LTP"], ["RELIANCE", "1257.5"], ["TCS", ""]]


def test_write_csv_sanitises_the_name_for_a_filename(tmp_path):
    table = ExportTable(name="securities/stocks weird*name", headers=["A"], rows=[[1]])
    path = write_csv(table, tmp_path, clock=lambda: dt.datetime(2026, 1, 1))
    assert path.name.startswith("securities-stocks-weird-name_")


def test_write_csv_creates_the_directory_if_missing(tmp_path):
    table = ExportTable(name="x", headers=["A"], rows=[[1]])
    target = tmp_path / "nested" / "exports"
    path = write_csv(table, target)
    assert path.is_file()


def test_write_csv_falls_back_to_a_plain_name_if_nothing_is_left(tmp_path):
    table = ExportTable(name="***", headers=["A"], rows=[[1]])
    path = write_csv(table, tmp_path, clock=lambda: dt.datetime(2026, 1, 1))
    assert path.name.startswith("export_")

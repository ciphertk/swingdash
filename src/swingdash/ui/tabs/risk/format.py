"""Numbers the Indian way (₹10,00,000 rather than ₹1,000,000), and trade dates."""

from __future__ import annotations

import datetime as dt

# Accepted when typing a date: 09-09-2026, 9/9/26, 2026-09-09, 9 Sep 2026, 9-Sep-26.
_DATE_FORMATS = (
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%y",
    "%d/%m/%y",
    "%d %b %Y",
    "%d %b %y",
    "%d-%b-%Y",
    "%d-%b-%y",
)


def grouped(value: float, decimals: int = 0) -> str:
    """Lakh/crore digit grouping: 1234567.8 -> '12,34,567.8' (at `decimals`)."""
    text = f"{abs(value):.{decimals}f}"
    whole, _, fraction = text.partition(".")
    if len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups)
    sign = "-" if value < 0 and float(text) != 0 else ""
    return f"{sign}{whole}.{fraction}" if fraction else f"{sign}{whole}"


def inr(value: float, decimals: int = 0) -> str:
    text = grouped(value, decimals)
    return f"-₹{text[1:]}" if text.startswith("-") else f"₹{text}"


def signed_inr(value: float, decimals: int = 0) -> str:
    return inr(value, decimals) if value < 0 else f"+{inr(value, decimals)}"


def date_input(day: dt.date) -> str:
    """How a date is pre-filled in a form: 09-09-2026."""
    return f"{day:%d-%m-%Y}"


def short_date(day: dt.date) -> str:
    """How a date shows in a table: 09 Sep 26."""
    return f"{day:%d %b %y}"


def parse_date(text: str) -> dt.date:
    """Raises ValueError with a message fit to show the user."""
    cleaned = " ".join(text.strip().split())
    for fmt in _DATE_FORMATS:
        try:
            day = dt.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
        if day.year >= 1990:  # "%Y" also accepts "26" as the year 26
            return day
    raise ValueError(f"'{text.strip()}' isn't a date - use DD-MM-YYYY, e.g. 09-09-2026.")

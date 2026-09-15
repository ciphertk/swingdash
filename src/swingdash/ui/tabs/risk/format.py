"""Numbers the Indian way: ₹10,00,000 rather than ₹1,000,000."""

from __future__ import annotations


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

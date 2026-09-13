"""
Central metric registry - the single place mapping a metric's stable ID
to its human-readable name, category, and compute function.

Why this exists (see CLAUDE.md's "Engine registry, not hardcoded list"):
as more metrics get added - cap classification, distance-from-52w, EMA,
sector strength/rotation are all already planned - nothing downstream
(a TUI dashboard column, a stock detail view, a future backtest script)
should grow a hardcoded if/elif per metric. They iterate METRICS or look
up by id instead. Adding metric #12 means: write the engine function, add
one entry here. Nothing else changes.

Compute signatures are NOT uniform (rvol/burst_score take one `bars`
sequence; mswing also needs `index_bars`) - this registry is metadata for
listing/lookup (e.g. the stock detail page enumerating every metric by
name), not a single call-anything-the-same-way interface. Wiring the
right ingestion output into each function's specific args is
the caller's job - engines stay pure, services do the I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from swingdash.domain.metrics import burst_score, mswing
from swingdash.domain.metrics import rvol_daily as rvol


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    name: str
    category: str  # "volume" | "momentum" | "price" | "fundamental"
    compute: Callable[..., object]
    description: str


METRICS: dict[str, MetricDefinition] = {
    "rvol": MetricDefinition(
        id="rvol",
        name="RVOL",
        category="volume",
        compute=rvol.compute_rvol,
        description="Today's volume as a % of the average volume over the preceding N days.",
    ),
    "burst_score": MetricDefinition(
        id="burst_score",
        name="Burst Score",
        category="price",
        compute=burst_score.compute_burst_score,
        description="Weighted count of historical big-move-up closing days (5%/10%/19%+ buckets).",
    ),
    "mswing": MetricDefinition(
        id="mswing",
        name="Mswing",
        category="momentum",
        compute=mswing.compute_mswing,
        description="20+50 day momentum score for the stock vs. a benchmark index.",
    ),
}


def get_metric(metric_id: str) -> MetricDefinition:
    try:
        return METRICS[metric_id]
    except KeyError:
        raise KeyError(f"Unknown metric id: {metric_id!r}. Known: {sorted(METRICS)}") from None


def list_metrics() -> list[MetricDefinition]:
    return list(METRICS.values())

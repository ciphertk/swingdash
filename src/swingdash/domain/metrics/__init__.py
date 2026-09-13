# Each metric gets its own module here, e.g.:
#   rvol.py          -> compute_rvol(candles) -> float
#   burst_score.py    -> compute_burst_score(candles) -> float
#   mswing.py          -> compute_mswing(candles) -> float
#
# Keep each function pure: (normalized OHLCV data in) -> (score out).
# No I/O, no Upstox SDK calls inside these - that keeps them trivially
# unit-testable and reusable from both the live dashboard and any backtest
# script later.

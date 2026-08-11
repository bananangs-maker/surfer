"""SURFER - a once-daily resting-order system whose levels are derived from
60-minute structure.

NOT a 60-minute signal system. It does not react to intraday bar closes. The
engine decides once per session, before the open, and delegates execution to
resting orders. 60-minute data earns its place in FILL ADJUDICATION, not in
signal generation.
"""

__version__ = "0.1.0"

from .schema import Adjustment, Dataset, LevelSet, Side  # noqa: F401

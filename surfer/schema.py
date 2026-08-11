"""Canonical data contract for SURFER.

Everything downstream depends on this schema and nothing else. Loaders are
adapters that must produce exactly this. Swapping yfinance for FirstRate must
be a one-file change; if it ever isn't, this contract has been violated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

import pandas as pd

NY = "America/New_York"

# Required columns of a canonical bar frame, in order.
BAR_COLUMNS = [
    "ts",            # bar OPEN time, tz-aware UTC
    "open",
    "high",
    "low",
    "close",
    "volume",
    "session_date",  # NY calendar date of the session the bar belongs to
    "bar_minutes",   # actual duration of this bar (may be < nominal: stub bars)
    "is_stub",       # True when bar_minutes < nominal interval
]

REGULAR_CLOSE = (16, 0)
EARLY_CLOSE = (13, 0)
SESSION_OPEN = (9, 30)


class Adjustment(Enum):
    """How corporate actions were handled by the source.

    This is not decoration. A system that computes price levels from bars is
    silently broken by an unadjusted split, and the break looks like a
    profitable gap signal rather than an error.
    """

    UNKNOWN = "unknown"
    RAW = "raw"                    # no adjustment at all
    SPLIT_ONLY = "split_only"
    SPLIT_AND_DIVIDEND = "split_and_dividend"


@dataclass(frozen=True)
class Dataset:
    """A canonical bar frame plus the provenance needed to judge it."""

    bars: pd.DataFrame
    symbol: str
    source: str
    interval_minutes: int
    adjustment: Adjustment
    quarantined: bool
    quarantine_reason: str = ""

    def __post_init__(self) -> None:
        missing = [c for c in BAR_COLUMNS if c not in self.bars.columns]
        if missing:
            raise ValueError(f"{self.source}: missing required columns {missing}")

    @property
    def sessions(self) -> list[date]:
        return sorted(self.bars["session_date"].unique().tolist())

    def session(self, d: date) -> pd.DataFrame:
        out = self.bars[self.bars["session_date"] == d]
        return out.sort_values("ts").reset_index(drop=True)

    def describe(self) -> str:
        s = self.sessions
        span = f"{s[0]} -> {s[-1]}" if s else "empty"
        q = f"QUARANTINED ({self.quarantine_reason})" if self.quarantined else "clean"
        return (
            f"{self.symbol} | {self.source} | {self.interval_minutes}m | "
            f"{len(self.bars)} bars | {len(s)} sessions | {span} | "
            f"adjustment={self.adjustment.value} | {q}"
        )


class Side(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class LevelSet:
    """The output of a level generator: prices, not decisions.

    This is the whole point of the GTC design. The system emits resting order
    levels once per day; it does not react to intraday bar closes.
    """

    session_date: date          # the session these levels are ARMED for
    side: Side
    entry_trigger: float        # stop-limit trigger
    entry_limit: float          # worst acceptable entry fill
    initial_stop: float         # protective stop, armed only after entry
    target: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side is Side.LONG:
            if self.entry_limit < self.entry_trigger:
                raise ValueError("long: entry_limit must be >= entry_trigger")
            if self.initial_stop >= self.entry_trigger:
                raise ValueError("long: initial_stop must be < entry_trigger")
            if self.target is not None and self.target <= self.entry_trigger:
                raise ValueError("long: target must be > entry_trigger")
        else:
            raise NotImplementedError("SURFER v0 is long-only by design decision")

    def actionable_prices(self, in_position: bool) -> dict[str, float]:
        """Levels that could be touched given current state.

        Used by the ambiguity diagnostic: if two of these fall inside one bar's
        [low, high], the bar cannot tell us which came first.
        """
        if not in_position:
            out = {"entry_trigger": self.entry_trigger, "initial_stop": self.initial_stop}
        else:
            out = {"initial_stop": self.initial_stop}
            if self.target is not None:
                out["target"] = self.target
        return out

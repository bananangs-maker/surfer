"""FirstRate Data 1-hour CSV adapter - the intended production source.

FirstRate ships naive NY-local timestamps and includes out-of-hours bars. Both
need handling here rather than downstream: `regular_hours_only` defaults to True
because a level-based system that arms resting orders for the regular session
must be adjudicated against regular-session bars.

Not quarantined - but `adjustment` must be passed explicitly. The split-adjusted
and unadjusted files look identical apart from the filename, and mixing them is
the fake-gap trap.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pandas as pd

from ..integrity import annotate_sessions
from ..schema import BAR_COLUMNS, NY, Adjustment, Dataset


def load_firstrate_csv(
    path: str | Path,
    symbol: str,
    adjustment: Adjustment,
    regular_hours_only: bool = True,
) -> Dataset:
    if adjustment is Adjustment.UNKNOWN:
        raise ValueError(
            "Pass the real adjustment status. FirstRate provides both adjusted "
            "and unadjusted files; guessing here is how a split becomes a signal."
        )

    df = pd.read_csv(
        path,
        header=None,
        names=["ts", "open", "high", "low", "close", "volume"],
    )
    naive = pd.to_datetime(df["ts"])
    df["ts"] = naive.dt.tz_localize(NY, nonexistent="shift_forward",
                                    ambiguous=True).dt.tz_convert("UTC")

    if regular_hours_only:
        ny = df["ts"].dt.tz_convert(NY)
        df = df[(ny.dt.time >= time(9, 30)) & (ny.dt.time < time(16, 0))]

    df = df.sort_values("ts").reset_index(drop=True)
    df = annotate_sessions(df, interval_minutes=60)

    return Dataset(
        bars=df[BAR_COLUMNS],
        symbol=symbol,
        source=f"firstrate:{Path(path).name}",
        interval_minutes=60,
        adjustment=adjustment,
        quarantined=False,
    )

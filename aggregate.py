"""Collapse 60-minute bars into one bar per session.

Used only by the diagnostic, to answer the question that justifies (or does not
justify) paying for intraday history: how much of the intra-bar ordering
ambiguity does 60-minute resolution actually remove, relative to daily OHLC?
"""

from __future__ import annotations

import pandas as pd


def to_daily(bars: pd.DataFrame) -> pd.DataFrame:
    g = bars.sort_values("ts").groupby("session_date", sort=True)
    out = pd.DataFrame(
        {
            "ts": g["ts"].first(),
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "bar_minutes": g["bar_minutes"].sum(),
        }
    ).reset_index()
    out["is_stub"] = False
    return out[
        ["ts", "open", "high", "low", "close", "volume",
         "session_date", "bar_minutes", "is_stub"]
    ]

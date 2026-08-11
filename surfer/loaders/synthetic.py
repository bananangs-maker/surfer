"""Synthetic 3x-LETF-like bars. For unit tests and offline smoke runs only.

Deliberately includes the awkward features that break naive engines: the 15:30
stub bar, occasional 13:00 early closes, an intraday volatility U-shape, and fat
overnight gaps. It is a test fixture, not a market model, and is quarantined for
the same reason yfinance is - harder, in fact, since none of it happened.
"""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from ..integrity import annotate_sessions
from ..schema import BAR_COLUMNS, Adjustment, Dataset, NY

_U_SHAPE = np.array([1.55, 1.05, 0.85, 0.80, 0.85, 1.10, 1.45])


def load_synthetic(
    symbol: str = "SYNTH3X",
    n_sessions: int = 400,
    start: str = "2024-08-01",
    daily_vol: float = 0.045,
    drift: float = 0.0009,
    overnight_share: float = 0.45,
    early_close_every: int = 60,
    seed: int = 7,
) -> Dataset:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start=start, periods=n_sessions, tz=NY)

    rows = []
    px = 50.0
    for i, day in enumerate(days):
        early = early_close_every > 0 and i > 0 and i % early_close_every == 0
        starts = (
            [time(9, 30), time(10, 30), time(11, 30), time(12, 30)]
            if early
            else [time(9, 30), time(10, 30), time(11, 30), time(12, 30),
                  time(13, 30), time(14, 30), time(15, 30)]
        )
        n = len(starts)

        # overnight gap, fat-tailed
        gap = rng.standard_t(df=3) * daily_vol * overnight_share
        px *= float(np.exp(np.clip(gap, -0.30, 0.30)))

        weights = _U_SHAPE[:n] if not early else _U_SHAPE[[0, 1, 2, 6]]
        weights = weights / np.sqrt(np.sum(weights**2))
        intraday_vol = daily_vol * np.sqrt(1 - overnight_share**2)

        for j, t in enumerate(starts):
            sigma = intraday_vol * weights[j]
            o = px
            ret = rng.standard_normal() * sigma + drift / n
            c = o * float(np.exp(ret))
            wick = abs(rng.standard_normal()) * sigma * 0.7
            h = max(o, c) * float(np.exp(wick))
            l = min(o, c) * float(np.exp(-abs(rng.standard_normal()) * sigma * 0.7))
            ts_ny = day.normalize() + pd.Timedelta(hours=t.hour, minutes=t.minute)
            rows.append(
                {
                    "ts": ts_ny.tz_convert("UTC"),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": float(rng.integers(2_000_000, 20_000_000)),
                }
            )
            px = c

    df = annotate_sessions(pd.DataFrame(rows), interval_minutes=60)
    return Dataset(
        bars=df[BAR_COLUMNS],
        symbol=symbol,
        source="synthetic",
        interval_minutes=60,
        adjustment=Adjustment.SPLIT_AND_DIVIDEND,
        quarantined=True,
        quarantine_reason="synthetic fixture - no market content whatsoever",
    )

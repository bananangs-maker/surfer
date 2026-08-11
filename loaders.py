"""Data loaders - adapters producing schema.Dataset.

Merged into one flat module so the project needs no subfolders. The adapter
boundary is unchanged: swapping the free source for purchased history is still
a change to one function and nothing else.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

from integrity import annotate_sessions
from schema import BAR_COLUMNS, EARLY_CLOSE, NY, Adjustment, Dataset

MAX_LOOKBACK_DAYS = 730


# ============================================================
# from yfinance_loader
def load_yfinance(
    symbol: str,
    period: str = "730d",
    interval: str = "60m",
    prepost: bool = False,
) -> Dataset:
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise ImportError("pip install yfinance") from e

    raw = yf.Ticker(symbol).history(
        period=period, interval=interval, prepost=prepost, auto_adjust=True
    )
    if raw.empty:
        raise ValueError(
            f"no data for {symbol}. Yahoo caps {interval} history at "
            f"{MAX_LOOKBACK_DAYS} days; a longer period silently returns empty."
        )

    df = raw.reset_index()
    tscol = "Datetime" if "Datetime" in df.columns else df.columns[0]
    df = df.rename(
        columns={
            tscol: "ts",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df[["ts", "open", "high", "low", "close", "volume"]]
    df = annotate_sessions(df, interval_minutes=60)

    return Dataset(
        bars=df[BAR_COLUMNS],
        symbol=symbol,
        source="yfinance",
        interval_minutes=60,
        # auto_adjust=True adjusts for splits and dividends, but Yahoo's
        # intraday adjustment is not consistently applied across split
        # boundaries. integrity.check() flags candidates; do not trust silently.
        adjustment=Adjustment.SPLIT_AND_DIVIDEND,
        quarantined=True,
        quarantine_reason=(
            f"Yahoo 60m history is capped at {MAX_LOOKBACK_DAYS} days; window "
            "excludes 2018Q4 / 2020Q1 / 2022. Plumbing and structural "
            "diagnostics only - no performance claims."
        ),
    )


# ============================================================
# from firstrate_csv
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


# ============================================================
# from synthetic
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

"""Bar integrity checks. Runs before anything else touches the data.

Two checks here exist specifically because of documented traps:

1. STUB BARS. The US regular session is 6.5 hours, which does not divide by 60.
   With bars opening at 09:30, the final 15:30 bar is 30 minutes long. Early
   closes (13:00) truncate differently. Feeding stub bars into ATR or bar-range
   statistics biases volatility estimates downward, every single day.

2. SPLIT ARTEFACTS. Free intraday sources are frequently unadjusted or
   inconsistently adjusted at split boundaries. The result is one fake overnight
   gap, which a level-based system will happily register as its most profitable
   signal. We flag large overnight moves for eyeball review rather than trying
   to auto-correct them - auto-correction hides the problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

import numpy as np
import pandas as pd

from .schema import EARLY_CLOSE, NY, REGULAR_CLOSE, SESSION_OPEN


@dataclass
class IntegrityReport:
    n_bars: int = 0
    n_sessions: int = 0
    n_stub_bars: int = 0
    duplicate_timestamps: int = 0
    non_monotonic: bool = False
    zero_volume_bars: int = 0
    grid_gaps: list[tuple[date, str]] = field(default_factory=list)
    short_sessions: list[tuple[date, int]] = field(default_factory=list)
    early_closes: list[date] = field(default_factory=list)
    bad_ohlc: list[pd.Timestamp] = field(default_factory=list)
    suspect_overnight: list[tuple[date, float]] = field(default_factory=list)

    @property
    def fatal(self) -> bool:
        return (
            self.duplicate_timestamps > 0
            or self.non_monotonic
            or len(self.bad_ohlc) > 0
        )

    def render(self) -> str:
        lines = [
            "INTEGRITY REPORT",
            f"  bars                 : {self.n_bars}",
            f"  sessions             : {self.n_sessions}",
            f"  stub bars            : {self.n_stub_bars}",
            f"  early closes         : {len(self.early_closes)}",
            f"  duplicate timestamps : {self.duplicate_timestamps}",
            f"  non-monotonic        : {self.non_monotonic}",
            f"  invalid OHLC         : {len(self.bad_ohlc)}",
            f"  zero-volume bars     : {self.zero_volume_bars}",
            f"  grid gaps            : {len(self.grid_gaps)}",
            f"  short sessions       : {len(self.short_sessions)}",
            f"  suspect overnight    : {len(self.suspect_overnight)}  <- eyeball these",
        ]
        for d, r in self.suspect_overnight[:10]:
            lines.append(f"      {d}  overnight log-return {r:+.3f}")
        if self.fatal:
            lines.append("  STATUS: FATAL - fix before proceeding")
        return "\n".join(lines)


def _session_close(last_bar_open_ny: pd.Timestamp) -> time:
    """Infer the session close from where the final bar starts.

    A session whose last bar opens at or before 12:30 NY is an early close.
    """
    if last_bar_open_ny.time() <= time(12, 30):
        return time(*EARLY_CLOSE)
    return time(*REGULAR_CLOSE)


def annotate_sessions(
    bars: pd.DataFrame,
    interval_minutes: int = 60,
) -> pd.DataFrame:
    """Add session_date, bar_minutes, is_stub. Idempotent."""
    df = bars.copy()
    if not isinstance(df["ts"].dtype, pd.DatetimeTZDtype):
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)

    ny = df["ts"].dt.tz_convert(NY)
    df["session_date"] = ny.dt.date

    bar_minutes = np.full(len(df), float(interval_minutes))
    is_stub = np.zeros(len(df), dtype=bool)

    for _, idx in df.groupby("session_date").groups.items():
        pos = np.asarray(idx)
        last = pos[-1]
        close_t = _session_close(ny.iloc[last])
        close_dt = ny.iloc[last].normalize() + pd.Timedelta(
            hours=close_t.hour, minutes=close_t.minute
        )
        dur = (close_dt - ny.iloc[last]).total_seconds() / 60.0
        bar_minutes[last] = max(0.0, min(float(interval_minutes), dur))
        # Interior bars: measure against the next bar's open.
        # Uses Series.diff().dt.total_seconds() rather than subtracting
        # .to_numpy() arrays: on pandas 2.x a tz-aware Series converts to
        # object dtype, and object / timedelta64 raises UFuncTypeError. This
        # form behaves identically on pandas 2.x and 3.x.
        if len(pos) > 1:
            deltas = (
                ny.iloc[pos].diff().dt.total_seconds().to_numpy()[1:] / 60.0
            )
            bar_minutes[pos[:-1]] = np.minimum(deltas, float(interval_minutes))

    is_stub = bar_minutes < float(interval_minutes) - 1e-9
    df["bar_minutes"] = bar_minutes
    df["is_stub"] = is_stub
    return df


def check(
    bars: pd.DataFrame,
    interval_minutes: int = 60,
    overnight_flag_threshold: float = 0.15,
) -> IntegrityReport:
    """Structural checks. Does not mutate.

    overnight_flag_threshold is in log-return terms. 0.15 is deliberately loose
    for 3x LETFs, where a genuine 12% gap is unremarkable; the aim is to catch
    split-sized discontinuities, not volatility.
    """
    rep = IntegrityReport(n_bars=len(bars))
    if len(bars) == 0:
        return rep

    df = bars.sort_values("ts").reset_index(drop=True)
    rep.non_monotonic = not bars["ts"].is_monotonic_increasing
    rep.duplicate_timestamps = int(df["ts"].duplicated().sum())

    bad = df[
        (df["high"] < df["low"])
        | (df["open"] > df["high"])
        | (df["open"] < df["low"])
        | (df["close"] > df["high"])
        | (df["close"] < df["low"])
        | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    ]
    rep.bad_ohlc = bad["ts"].tolist()
    rep.zero_volume_bars = int((df["volume"] <= 0).sum())
    rep.n_stub_bars = int(df["is_stub"].sum())

    ny = df["ts"].dt.tz_convert(NY)
    open_t = time(*SESSION_OPEN)

    sessions = list(df.groupby("session_date").groups.items())
    rep.n_sessions = len(sessions)

    for sess, idx in sessions:
        pos = np.asarray(idx)
        sny = ny.iloc[pos]
        if sny.iloc[0].time() != open_t:
            rep.grid_gaps.append((sess, f"first bar at {sny.iloc[0].time()}"))
        close_t = _session_close(sny.iloc[-1])
        if close_t == time(*EARLY_CLOSE):
            rep.early_closes.append(sess)
        expected = int(
            np.ceil(
                (
                    (close_t.hour * 60 + close_t.minute)
                    - (open_t.hour * 60 + open_t.minute)
                )
                / interval_minutes
            )
        )
        if len(pos) != expected:
            rep.short_sessions.append((sess, len(pos)))
        # Same pandas 2.x/3.x hazard as in annotate_sessions - keep this form.
        gaps = sny.diff().dt.total_seconds().to_numpy()[1:] / 60.0
        for j, g in enumerate(gaps):
            if g > interval_minutes + 1e-9:
                rep.grid_gaps.append(
                    (sess, f"{int(g)}m hole after {sny.iloc[j].time()}")
                )

    # overnight discontinuities, measured session-close to next session-open
    closes = df.groupby("session_date")["close"].last()
    opens = df.groupby("session_date")["open"].first()
    prev_close = closes.shift(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.log(opens / prev_close)
    for sess, val in lr.items():
        if pd.notna(val) and abs(val) > overnight_flag_threshold:
            rep.suspect_overnight.append((sess, float(val)))

    return rep

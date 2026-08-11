from __future__ import annotations

import pandas as pd
import pytest

import integrity
from aggregate import to_daily
from diagnostics import QuarantineError, compute_performance
from levels import atr60, stub_range_ratio
from loaders import load_synthetic

NY = "America/New_York"


def session(day: str, times: list[str]) -> pd.DataFrame:
    rows = []
    for i, t in enumerate(times):
        ts = pd.Timestamp(f"{day} {t}", tz=NY).tz_convert("UTC")
        rows.append({"ts": ts, "open": 50 + i, "high": 51 + i,
                     "low": 49 + i, "close": 50.5 + i, "volume": 1e6})
    return pd.DataFrame(rows)


def test_regular_session_last_bar_is_a_30_minute_stub():
    df = integrity.annotate_sessions(
        session("2025-03-03", ["09:30", "10:30", "11:30", "12:30",
                               "13:30", "14:30", "15:30"])
    )
    assert len(df) == 7
    assert df["bar_minutes"].iloc[-1] == pytest.approx(30.0)
    assert df["is_stub"].sum() == 1
    assert bool(df["is_stub"].iloc[-1])


def test_early_close_session_is_detected_and_not_reported_as_short():
    df = integrity.annotate_sessions(
        session("2025-11-28", ["09:30", "10:30", "11:30", "12:30"])
    )
    rep = integrity.check(df)
    assert rep.early_closes == [df["session_date"].iloc[0]]
    assert rep.short_sessions == []
    assert df["bar_minutes"].iloc[-1] == pytest.approx(30.0)


def test_missing_interior_bar_is_reported_as_a_grid_gap():
    df = integrity.annotate_sessions(
        session("2025-03-03", ["09:30", "10:30", "12:30", "13:30",
                               "14:30", "15:30"])
    )
    rep = integrity.check(df)
    assert any("hole" in msg for _, msg in rep.grid_gaps)


def test_invalid_ohlc_is_fatal():
    df = integrity.annotate_sessions(session("2025-03-03", ["09:30", "10:30"]))
    df.loc[0, "high"] = df.loc[0, "low"] - 1
    rep = integrity.check(df)
    assert rep.fatal


def test_split_sized_overnight_move_is_flagged():
    a = integrity.annotate_sessions(session("2025-03-03", ["09:30", "10:30"]))
    b = integrity.annotate_sessions(session("2025-03-04", ["09:30", "10:30"]))
    b[["open", "high", "low", "close"]] /= 2.0  # unadjusted 2:1 split
    rep = integrity.check(pd.concat([a, b], ignore_index=True))
    assert len(rep.suspect_overnight) == 1


def test_stub_bar_range_is_not_assumed_smaller():
    """Pins a falsified assumption.

    The original version of this test asserted that excluding stub bars RAISES
    ATR, on the theory that a half-length bar must have a smaller range. It
    failed. The 15:30-16:00 stub covers the closing ramp and carries a larger
    range than an average full bar in this fixture, so the bias runs the other
    way. The test now pins the thing that is actually true and load-bearing:
    the exclude_stubs choice moves ATR materially, so it cannot be left as an
    unexamined default.
    """
    ds = load_synthetic(n_sessions=120)
    hist = [ds.session(s) for s in ds.sessions]

    stats = stub_range_ratio(hist)
    assert stats["n_stub"] > 0 and stats["n_full"] > 0
    # The fixture reports >1 (stub wider). Real TQQQ reports 0.62x (stub
    # narrower) - the fixture's intraday profile over-weights the closing bar.
    # This assertion pins FIXTURE behaviour only. It is not evidence about any
    # instrument, and the disagreement is the point: synthetic data cannot
    # settle an empirical question about intraday shape.
    assert stats["ratio"] > 1.0, "fixture: closing stub is the wider bar"

    with_stubs = atr60(hist, exclude_stubs=False)
    without = atr60(hist, exclude_stubs=True)
    assert with_stubs is not None and without is not None
    assert abs(without / with_stubs - 1.0) > 0.02, "the flag must matter"


def test_daily_aggregate_preserves_session_extremes():
    ds = load_synthetic(n_sessions=30)
    d = to_daily(ds.bars)
    assert len(d) == len(ds.sessions)
    s0 = ds.session(ds.sessions[0])
    assert d["high"].iloc[0] == pytest.approx(s0["high"].max())
    assert d["low"].iloc[0] == pytest.approx(s0["low"].min())
    assert d["open"].iloc[0] == pytest.approx(s0["open"].iloc[0])
    assert d["close"].iloc[0] == pytest.approx(s0["close"].iloc[-1])


def test_quarantined_dataset_blocks_performance():
    ds = load_synthetic(n_sessions=20)
    assert ds.quarantined
    with pytest.raises(QuarantineError):
        compute_performance(ds, outcomes=[])

"""Daily levels board tests.

The board's one dangerous failure is lookahead: if the feed's final session is
still in progress and gets used as history, today's levels are derived from bars
that have not closed. Nothing about the resulting numbers looks wrong.
"""

from __future__ import annotations

import pytest

import board as B
from loaders import load_synthetic
from schema import BAR_COLUMNS, Dataset


@pytest.fixture(scope="module")
def ds():
    return load_synthetic(n_sessions=300)


def _truncate_last_session(ds, keep_bars: int):
    last = ds.sessions[-1]
    mask = ~(
        (ds.bars["session_date"] == last)
        & (ds.bars.groupby("session_date").cumcount() >= keep_bars)
    )
    return Dataset(
        bars=ds.bars[mask].reset_index(drop=True)[BAR_COLUMNS],
        symbol=ds.symbol, source=ds.source, interval_minutes=60,
        adjustment=ds.adjustment, quarantined=ds.quarantined,
        quarantine_reason=ds.quarantine_reason,
    ), last


def test_board_returns_a_row_per_candidate(ds):
    bd = B.build(ds, "SYNTH3X")
    assert [r.key for r in bd.rows] == ["A", "B", "C", "D"]


def test_abc_disagree_on_where_to_put_levels(ds):
    """A, B and C are genuine alternatives, so their triggers must differ."""
    bd = B.build(ds, "SYNTH3X")
    triggers = {
        r.key: r.levels.entry_trigger
        for r in bd.rows if r.levels and r.key in ("A", "B", "C")
    }
    assert len(set(triggers.values())) == len(triggers) == 3


def test_candidate_d_is_a_gated_a_not_a_separate_rule(ds):
    """D is A plus a gate, so an OPEN gate must reproduce A's levels exactly.

    An earlier version of this test asserted all four differ, which was wrong:
    if D emitted its own levels when the gate was open, the comparison between
    A and D would no longer isolate the gate's effect.
    """
    bd = B.build(ds, "SYNTH3X")
    a = next(r for r in bd.rows if r.key == "A")
    d = next(r for r in bd.rows if r.key == "D")
    if d.levels is None:
        assert d.extra.get("percentile") is not None      # blocked, with a reason
    else:
        assert d.levels.entry_trigger == a.levels.entry_trigger
        assert d.levels.initial_stop == a.levels.initial_stop


def test_pullback_candidate_places_its_limit_below_the_trigger(ds):
    bd = B.build(ds, "SYNTH3X")
    b = next(r for r in bd.rows if r.key == "B")
    assert b.levels is not None
    assert b.levels.entry_limit < b.levels.entry_trigger
    assert b.style == "pullback"


def test_partial_final_session_is_excluded(ds):
    """Lookahead guard. Three bars means the session is still open."""
    partial, last = _truncate_last_session(ds, 3)
    bd = B.build(partial, "SYNTH3X")
    assert bd.dropped_partial == last
    assert bd.partial_bar_count == 3
    assert bd.last_complete < last


def test_early_close_session_counts_as_complete(ds):
    """A 13:00 close yields four bars and is finished, not partial."""
    early, last = _truncate_last_session(ds, 4)
    bd = B.build(early, "SYNTH3X")
    assert bd.dropped_partial is None
    assert bd.last_complete == last


def test_levels_never_derive_from_the_armed_session(ds):
    """The generators see only sessions strictly before last_complete + 1."""
    bd = B.build(ds, "SYNTH3X")
    assert bd.last_complete == bd.rows[0].levels.session_date - __import__(
        "datetime"
    ).timedelta(days=1)


def test_gate_blocking_is_reported_with_its_reason(ds):
    bd = B.build(ds, "SYNTH3X")
    d = next(r for r in bd.rows if r.key == "D")
    assert d.extra.get("percentile") is not None
    if d.levels is None:
        assert "백분위" in d.note


def test_worksheet_has_blank_columns_for_the_broker_to_fill(ds):
    ws = B.order_worksheet(B.build(ds, "SYNTH3X"))
    head, *rows = ws.strip().split("\n")
    assert head.startswith("symbol,candidate,style,trigger,limit,stop")
    assert "actual_fill" in head and "filled_yn" in head
    assert rows and all(r.endswith(",,,") for r in rows)


def test_empty_feed_raises_rather_than_inventing_levels():
    ds = load_synthetic(n_sessions=5)
    empty = Dataset(
        bars=ds.bars.iloc[:0][BAR_COLUMNS], symbol="X", source="t",
        interval_minutes=60, adjustment=ds.adjustment, quarantined=True,
        quarantine_reason="t",
    )
    with pytest.raises(ValueError):
        B.build(empty, "X")


def test_levels_page_renders(ds):
    from app import app

    with app.test_client() as c:
        html = c.get("/levels?symbol=SYNTH3X").data.decode()
    assert "실행 실패" not in html
    assert "어느 것도 선택된 신호가 아닙니다" in html
    assert "symbol,candidate,style" in html


def test_levels_page_is_cheap():
    import time

    from app import app

    with app.test_client() as c:
        c.get("/levels?symbol=SYNTH3X")          # warm the dataset cache
        t0 = time.perf_counter()
        c.get("/levels?symbol=SYNTH3X")
        assert time.perf_counter() - t0 < 2.0

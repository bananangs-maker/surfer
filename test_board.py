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
    """Single-engine signal page (§12.5): state badge, price rail, worksheet."""
    from app import app

    with app.test_client() as c:
        html = c.get("/levels?symbol=SYNTH3X").data.decode()
    assert "실행 실패" not in html
    assert 'class="badge' in html                 # the state at a glance
    assert "symbol,engine,state" in html          # execution worksheet
    assert "SURFER-ENGINE-" in html


def test_levels_page_is_minimal_and_folds_the_rest(ds):
    """The chart owns the page; auxiliary panels are collapsed.

    Pinned because the value of this screen is that one glance answers "what do I
    place today". Every panel added above the fold erodes that.
    """
    from app import app

    with app.test_client() as c:
        html = c.get("/levels?symbol=SYNTH3X").data.decode()
    assert html.count("<details>") >= 5
    # Nothing may be open by default.
    assert "<details open" not in html
    assert "svg viewBox" in html
    # Terminal layout: readout row plus two aligned sub-panels.
    assert "ATR PCTL" in html and "GATE STATE" in html


def test_page_is_dark_and_respects_reduced_motion(ds):
    from app import app

    with app.test_client() as c:
        html = c.get("/levels?symbol=SYNTH3X").data.decode()
    assert 'content="dark"' in html
    assert "--paper:#0E1014" in html
    # Animations must be defeasible; some people get sick from them.
    assert html.count("prefers-reduced-motion") >= 2


def test_levels_page_is_cheap():
    import time

    from app import app

    with app.test_client() as c:
        c.get("/levels?symbol=SYNTH3X")          # warm the dataset cache
        t0 = time.perf_counter()
        c.get("/levels?symbol=SYNTH3X")
        # Replaying the gate over the shown window costs real time; the budget is
        # generous because Render's free tier is ~6x slower than this machine.
        assert time.perf_counter() - t0 < 8.0


# --- unified engine (DES-002 §12.5) ---------------------------------------

def test_engine_signal_states_are_exhaustive(ds):
    from engine import State

    bd = B.build_engine(ds, "SYNTH3X")
    assert bd.signal.state in set(State)
    assert bd.engine_version.startswith("SURFER-ENGINE-")


def test_engine_reproduces_candidate_d(ds):
    """The engine IS candidate D. If it drifted, §11's measurement would no
    longer describe what is being operated."""
    import datetime as dt

    from engine import Engine
    from levels import VolatilityRegimeGate

    sessions = ds.sessions
    by = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    e = Engine()
    hist = [by[s] for s in sessions[-e.lookback_sessions:]]
    armed = sessions[-1] + dt.timedelta(days=1)
    sig = e.signal(hist, armed)
    direct = VolatilityRegimeGate()(hist, armed)
    if direct is None:
        assert sig.levels is None
    else:
        assert sig.levels.entry_trigger == direct.entry_trigger
        assert sig.levels.initial_stop == direct.initial_stop


def test_position_is_replayed_not_stored(ds):
    """Two independent replays must agree; a stored position could not be checked."""
    from engine import Engine, replay_position

    e = Engine()
    a = replay_position(ds, e)
    b = replay_position(ds, e)
    assert (a is None) == (b is None)
    if a is not None:
        assert a.entry_ts == b.entry_ts and a.stop == b.stop


def test_holding_signal_never_arms_a_new_entry(ds):
    """No pyramiding: while holding, the engine emits exits only."""
    import datetime as dt

    import pandas as pd

    from engine import Engine, State
    from fills import Position

    sessions = ds.sessions
    by = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    e = Engine()
    hist = [by[s] for s in sessions[-e.lookback_sessions:]]
    pos = Position(
        entry_ts=pd.Timestamp(sessions[-2]), entry_price=100.0, stop=95.0,
        target=None, entry_at_gap=False, entry_ambiguous=False,
    )
    sig = e.signal(hist, sessions[-1] + dt.timedelta(days=1), position=pos)
    assert sig.state is State.HOLDING
    assert sig.levels is None
    assert sig.effective_exit >= pos.stop


def test_page_says_the_engine_is_unvalidated(ds):
    from app import app

    with app.test_client() as c:
        html = c.get("/levels?symbol=SYNTH3X").data.decode()
    assert "실행 실패" not in html
    assert "검증되지 않은 규칙" in html
    assert "SURFER-ENGINE-" in html


# --- motion: rich, but always defeasible --------------------------------

def test_every_page_respects_reduced_motion():
    """Animation that cannot be turned off is an accessibility failure.

    Vestibular disorders make sustained motion genuinely sickening, and this
    engine's whole point is being usable every morning. Asserted per page rather
    than once, because the rule is easy to add to one template and forget in the
    next.
    """
    from app import app

    urls = [
        "/", "/?run=1&symbol=SYNTH3X", "/levels?symbol=SYNTH3X",
        "/compare?symbol=SYNTH3X", "/backtest?symbol=SYNTH3X",
        "/validate?symbol=SYNTH3X",
    ]
    with app.test_client() as c:
        for u in urls:
            html = c.get(u).data.decode()
            assert "prefers-reduced-motion" in html, u


def test_svg_animation_classes_are_attached_not_merely_defined():
    """A class defined in CSS but never applied looks identical in source review.

    This caught two real cases: sf-num and sf-lead were declared and then not
    attached, because the f-string being patched used single quotes inside the
    braces and the replacement searched for double.
    """
    from chart import annotated_session_svg, pick_example
    from levels import PlaceholderBreakout
    from loaders import load_synthetic

    svg = annotated_session_svg(
        *pick_example(load_synthetic(n_sessions=150), PlaceholderBreakout()),
        theme="dark",
    )
    for name in ("sf-wick", "sf-candle", "sf-level", "sf-zone", "sf-divide",
                 "sf-num", "sf-box", "sf-lead", "sf-scan"):
        # >1 means the CSS declaration plus at least one element using it.
        assert svg.count(name) > 1, f"{name} is declared but never applied"


def test_animation_can_be_switched_off_entirely():
    from chart import annotated_session_svg, pick_example
    from levels import PlaceholderBreakout
    from loaders import load_synthetic

    ex = pick_example(load_synthetic(n_sessions=150), PlaceholderBreakout())
    plain = annotated_session_svg(*ex, theme="dark", animate=False)
    assert "sf-candle" not in plain
    assert "@keyframes" not in plain
    assert "svg" in plain and "TRIGGER" in plain


def test_dark_and_light_themes_leave_no_hardcoded_colours():
    from chart import annotated_session_svg, pick_example
    from levels import PlaceholderBreakout
    from loaders import load_synthetic

    ex = pick_example(load_synthetic(n_sessions=150), PlaceholderBreakout())
    for theme, forbidden in (("dark", "#FFFFFF"), ("light", "#12141A")):
        svg = annotated_session_svg(*ex, theme=theme)
        assert forbidden not in svg, f"{theme} leaked {forbidden}"

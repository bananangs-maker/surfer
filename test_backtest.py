"""Backtest-layer tests.

The bug these exist for: the adjudicator reset to flat every session, so 64% of
entries had no recorded exit and no P&L could be computed at all. A swing system
was silently being measured as a day-trading system.
"""

from __future__ import annotations

import pytest

from backtest import Costs, QuarantineError, run_backtest
from fills import Kind
from levels import PlaceholderBreakout, StructuralExit
from loaders import load_synthetic


@pytest.fixture(scope="module")
def ds():
    return load_synthetic(n_sessions=300)


def test_quarantined_data_requires_acknowledgement(ds):
    with pytest.raises(QuarantineError):
        run_backtest(ds, PlaceholderBreakout())


def test_quarantine_label_travels_with_the_result(ds):
    res = run_backtest(ds, PlaceholderBreakout(), acknowledge_quarantine=True)
    assert res.quarantined
    assert res.quarantine_reason


def test_positions_carry_across_sessions_and_close(ds):
    res = run_backtest(ds, PlaceholderBreakout(), acknowledge_quarantine=True)
    assert res.closed_trades, "no trade ever closed"
    # At most one may remain open at the end of the data.
    assert all(t.closed for t in res.trades)
    assert any(t.sessions_held >= 1 for t in res.closed_trades), (
        "no trade held past its entry session - the carry path is not exercised"
    )


def test_without_an_exit_rule_every_exit_is_a_loss(ds):
    """Documents the structural gap that motivated StructuralExit.

    A fixed stop and no exit rule means the only way out is the stop, so the
    win rate is exactly zero by construction. That is a missing rule, not a bad
    strategy, and it must not be mistaken for one.
    """
    res = run_backtest(ds, PlaceholderBreakout(), acknowledge_quarantine=True)
    assert all(t.exit_kind is Kind.STOP for t in res.closed_trades)
    assert all((t.gross_return or 0) <= 0 for t in res.closed_trades)


def test_structural_exit_produces_winning_trades(ds):
    res = run_backtest(
        ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
        acknowledge_quarantine=True,
    )
    wins = [t for t in res.closed_trades if (t.gross_return or 0) > 0]
    assert wins, "structural exit produced no winners"
    assert any(t.exit_kind is Kind.STRUCTURAL for t in res.closed_trades)


def test_structural_exit_never_digs_below_the_fixed_stop(ds):
    """The fixed stop is a floor. effective_stop may rise above it, never below."""
    from dataclasses import replace

    from fills import Position
    import pandas as pd

    p = Position(
        entry_ts=pd.Timestamp("2025-03-03", tz="UTC"), entry_price=100.0,
        stop=97.0, target=None, entry_at_gap=False, entry_ambiguous=False,
    )
    assert p.effective_stop == pytest.approx(97.0)
    assert replace(p, structural_exit=95.0).effective_stop == pytest.approx(97.0)
    assert replace(p, structural_exit=98.5).effective_stop == pytest.approx(98.5)


def test_ratchet_never_lowers_the_structural_level(ds):
    rule = StructuralExit(ratchet=True)
    sessions = ds.sessions
    by = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    prev = None
    seen = 0
    for i in range(20, 120):
        hist = [by[s] for s in sessions[i - 12:i]]
        lvl = rule(hist, prev)
        if prev is not None and lvl is not None:
            assert lvl >= prev - 1e-9, f"ratchet lowered: {prev} -> {lvl}"
            seen += 1
        prev = lvl
    assert seen > 50


def test_costs_make_results_strictly_worse(ds):
    free = run_backtest(
        ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
        costs=Costs(spread_bps=0, gap_extra_bps=0, commission_per_share=0,
                    commission_min=0),
        acknowledge_quarantine=True,
    )
    charged = run_backtest(
        ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
        costs=Costs(), acknowledge_quarantine=True,
    )
    from backtest import _stats

    assert _stats(charged.closed_trades)["total_return"] < _stats(
        free.closed_trades
    )["total_return"]


def test_gap_fills_are_tagged_separately(ds):
    res = run_backtest(
        ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
        acknowledge_quarantine=True,
    )
    assert any(t.entry_at_gap or t.exit_at_gap for t in res.closed_trades)


def test_no_pyramiding_only_one_position_at_a_time(ds):
    res = run_backtest(
        ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
        acknowledge_quarantine=True,
    )
    closed = sorted(res.closed_trades, key=lambda t: t.entry_ts)
    for a, b in zip(closed, closed[1:]):
        assert a.exit_ts <= b.entry_ts, "overlapping positions"


# --- candidate D: pre-registered volatility gate --------------------------

def test_candidate_d_parameters_match_the_preregistration():
    """SURFER-DES-002 §4.1 fixed these. A silent change invalidates the study."""
    from levels import VolatilityRegimeGate

    assert VolatilityRegimeGate.PERCENTILE_WINDOW == 252
    assert VolatilityRegimeGate.BLOCK_AT_PERCENTILE == 80.0
    assert VolatilityRegimeGate.lookback_sessions >= 252


def test_gate_blocks_entries_and_so_trades_fewer_than_candidate_a(ds):
    from levels import PlaceholderBreakout, StructuralExit, VolatilityRegimeGate

    a = run_backtest(ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
                     acknowledge_quarantine=True)
    d = run_backtest(ds, VolatilityRegimeGate(), exit_rule=StructuralExit(),
                     acknowledge_quarantine=True)
    assert len(d.closed_trades) < len(a.closed_trades), (
        "the gate did not reduce trade count; it is not gating anything"
    )


def test_gate_does_not_interfere_with_open_positions(ds):
    """Entry-only by design. A gated exit could strand a position in exactly the
    conditions the filter exists to avoid."""
    from levels import StructuralExit, VolatilityRegimeGate

    res = run_backtest(ds, VolatilityRegimeGate(), exit_rule=StructuralExit(),
                       acknowledge_quarantine=True)
    assert all(t.closed for t in res.trades)
    assert any(t.sessions_held >= 1 for t in res.closed_trades)


# --- time-indexed equity: the axis DES-002 judges on ----------------------

def test_equity_curve_has_one_point_per_session(ds):
    from levels import StructuralExit

    res = run_backtest(ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
                       acknowledge_quarantine=True)
    assert len(res.equity) == len(ds.sessions)
    dates = [d for d, _ in res.equity]
    assert dates == sorted(dates)


def test_equity_moves_while_a_position_is_open(ds):
    """The bug this pins: compounding only at exit produced a TRADE-indexed
    curve, and a trade-indexed drawdown cannot be compared with a buy-and-hold
    drawdown measured over time. Equity must move on held sessions too.
    """
    from levels import StructuralExit

    res = run_backtest(ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
                       acknowledge_quarantine=True)
    exit_dates = {t.exit_date for t in res.closed_trades}
    moved_without_an_exit = 0
    prev = None
    for d, v in res.equity:
        if prev is not None and d not in exit_dates and abs(v - prev) > 1e-9:
            moved_without_an_exit += 1
        prev = v
    assert moved_without_an_exit > 0, "equity is flat except at exits"


def test_buy_and_hold_baseline_spans_the_same_sessions(ds):
    from backtest import buy_and_hold

    bh = buy_and_hold(ds)
    assert len(bh) == len(ds.sessions)
    assert [d for d, _ in bh] == ds.sessions


def test_curve_metrics_arithmetic():
    """Hand-checked: 10 -> 20 -> 10 -> 15 over 252 sessions."""
    import datetime as dt

        # value path with a known 50% drawdown and a known ending multiple
    vals = [10.0] * 63 + [20.0] * 63 + [10.0] * 63 + [15.0] * 63
    curve = [(dt.date(2020, 1, 1) + dt.timedelta(days=i), v)
             for i, v in enumerate(vals)]
    from backtest import curve_metrics

    m = curve_metrics(curve)
    assert m["n_sessions"] == 252
    assert m["total_return"] == pytest.approx(0.5)
    assert m["cagr"] == pytest.approx(0.5, abs=1e-6)      # exactly one year
    assert m["max_drawdown"] == pytest.approx(-0.5)       # 20 -> 10
    assert m["calmar"] == pytest.approx(1.0, abs=1e-6)
    assert m["longest_underwater_sessions"] == 126        # the 10s then the 15s


def test_verdict_thresholds_match_the_preregistration(ds):
    """SURFER-DES-002 §2.2 Calmar 1.5x, §2.3 MDD 50% of B&H. Both independent."""
    import inspect

    from backtest import verdict

    sig = inspect.signature(verdict)
    assert sig.parameters["calmar_multiple_required"].default == 1.5
    assert sig.parameters["mdd_share_allowed"].default == 0.50


def test_verdict_requires_both_conditions(ds):
    import datetime as dt

    from backtest import verdict

    def curve(vals):
        return [(dt.date(2020, 1, 1) + dt.timedelta(days=i), v)
                for i, v in enumerate(vals)]

    bh = curve([10.0] * 126 + [5.0] * 63 + [12.0] * 63)      # -50% MDD
    # Same shape but shallower: passes MDD, and Calmar scales with it.
    good = curve([10.0] * 126 + [8.0] * 63 + [14.0] * 63)    # -20% MDD
    v = verdict(good, bh)
    assert v["mdd_pass"] is True
    assert v["pass"] == (v["calmar_pass"] and v["mdd_pass"])

    # Deeper than the baseline allows -> must fail regardless of return.
    bad = curve([10.0] * 126 + [3.0] * 63 + [40.0] * 63)
    v2 = verdict(bad, bh)
    assert v2["mdd_pass"] is False
    assert v2["pass"] is False


def test_placeholder_does_not_pass_the_verdict(ds):
    """Sanity: an arbitrary shim on synthetic data must not clear the bar. If it
    ever does, the verdict is not measuring what it claims to."""
    from backtest import buy_and_hold, verdict
    from levels import StructuralExit

    res = run_backtest(ds, PlaceholderBreakout(), exit_rule=StructuralExit(),
                       acknowledge_quarantine=True)
    assert verdict(res.equity, buy_and_hold(ds))["pass"] is False


def test_negative_baseline_calmar_does_not_let_a_loser_pass():
    """A multiplicative threshold inverts on a negative baseline.

    With B&H Calmar at -0.83, "1.5x" asks for >= -1.25, which a losing system
    clears. DES-002 §3.3 puts 2022 inside the measured windows, so the case is
    real. The repaired rule requires SURFER's own Calmar to be positive.
    """
    import datetime as dt

    from backtest import verdict

    def curve(vals):
        return [(dt.date(2020, 1, 1) + dt.timedelta(days=i), v)
                for i, v in enumerate(vals)]

    bh_bear = curve([10.0] * 126 + [4.0] * 63 + [5.0] * 63)
    assert verdict(curve([10.0] * 126), bh_bear)["buy_and_hold"]["calmar"] <= 0

    loser = curve([10.0] * 126 + [7.0] * 63 + [8.0] * 63)
    v = verdict(loser, bh_bear)
    assert v["calmar_pass"] is False
    assert v["pass"] is False

    winner = curve([10.0] * 126 + [9.0] * 63 + [14.0] * 63)
    assert verdict(winner, bh_bear)["calmar_pass"] is True


def test_positive_calmar_is_necessary_even_against_a_weak_baseline():
    import datetime as dt

    from backtest import verdict

    def curve(vals):
        return [(dt.date(2020, 1, 1) + dt.timedelta(days=i), v)
                for i, v in enumerate(vals)]

    flat_bh = curve([10.0] * 252)
    declining = curve([10.0] * 126 + [9.5] * 126)
    assert verdict(declining, flat_bh)["calmar_pass"] is False

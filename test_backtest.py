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

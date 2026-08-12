"""Robustness sweep (SURFER-DES-002 §15).

Not an optimiser. These tests exist mainly to keep it from becoming one: the
temptation with 256 backtests in hand is to report the maximum, and the maximum of
a grid is not evidence when no out-of-sample data remains.
"""

from __future__ import annotations

import pytest

import sweep as SW
from backtest import Costs, buy_and_hold, curve_metrics
from loaders import load_synthetic


@pytest.fixture(scope="module")
def small():
    """A coarse sweep: enough cells for neighbourhood analysis, quick enough to run."""
    ds = load_synthetic(n_sessions=220)
    bh = buy_and_hold(ds, costs=Costs())
    bm = curve_metrics(bh)
    res = SW.SweepResult(bh_calmar=bm["calmar"], bh_mdd=bm["max_drawdown"],
                         total=len(SW.combos()))
    for p in SW.combos()[:48]:
        res.cells.append(SW.run_one(ds, p, bh, bm["max_drawdown"]))
    return res


def test_grid_is_the_preregistered_one():
    """§15.2 fixed these. Extending the grid after seeing results is forbidden, and
    a silent edit would make the pass rate incomparable with what was reported."""
    assert len(SW.combos()) == 256
    assert SW.GRID["gate_pct"][-1] > 100.0, "no-gate must be a point in the grid"
    assert SW.FIXED["pct_window"] == 252


def test_no_gate_is_reachable_as_a_grid_point():
    """The gate's contribution is only visible if its absence is in the grid."""
    g = SW.build_generator({"trigger": 0.1, "stop": 1.2, "gate_pct": 101.0,
                            "exit_buf": 0.25})
    assert not hasattr(g, "BLOCK_AT_PERCENTILE")


def test_selection_statistic_is_worst_of_neighbourhood(small):
    """Ranking must not be by the cell's own score.

    Picking the maximum picks noise. Picking worst-of-neighbourhood picks a region
    that survives a slightly wrong parameter, which is the property live use needs.
    """
    a = SW.analyse(small)
    ranked = a["ranked"]
    assert ranked
    scores = [s["worst_neighbour_calmar"] for s in ranked]
    assert scores == sorted(scores, reverse=True)
    for s in ranked:
        assert s["worst_neighbour_calmar"] <= s["cell"].calmar + 1e-12
        assert s["n_neighbours"] >= 2


def test_low_pass_rate_blocks_selection(small):
    """§15.4: below 5% the grid has no robust region and nothing may be selected."""
    a = SW.analyse(small)
    if a["pass_rate"] < SW.PASS_RATE_FLOOR:
        assert a["verdict"]["selectable"] is False
        assert "통과율" in a["verdict"]["reason"]


def test_distribution_is_reported_not_just_the_best(small):
    a = SW.analyse(small)
    assert len(a["calmar_q"]) == 5 and len(a["ratio_q"]) == 5
    assert a["calmar_q"] == sorted(a["calmar_q"])
    assert "axis_medians" in a and set(a["axis_medians"]) == set(SW.AXES)


def test_partial_sweep_reports_missing_axis_values_as_none(small):
    """A partial sweep leaves some axis values with no cells; nan or 0.0 there would
    read as a measurement that had not been taken."""
    a = SW.analyse(small)
    vals = [v for d in a["axis_medians"].values() for v in d.values()]
    assert any(v is None for v in vals) or len(small.cells) == 256


def test_neighbours_stay_inside_the_grid():
    corner = (0, 0, 0, 0)
    assert len(SW.neighbours(corner)) == 4          # one direction per axis
    mid = (1, 1, 1, 1)
    assert len(SW.neighbours(mid)) == 8
    for idx in SW.neighbours(corner) + SW.neighbours(mid):
        for ax, i in zip(SW.AXES, idx):
            assert 0 <= i < len(SW.GRID[ax])


def test_page_says_it_is_not_an_optimiser():
    from app import app

    with app.test_client() as c:
        html = c.get("/sweep?symbol=SYNTH3X&reset=1").data.decode()
    assert "최적화가 아닙니다" in html
    assert "이웃최솟값" in html
    assert "표본 내" in html
    assert "Traceback" not in html


def test_sweep_resumes_across_requests():
    """256 cells cannot finish in one request on a 0.1-CPU instance."""
    from app import app

    with app.test_client() as c:
        first = c.get("/sweep?symbol=SYNTH3X&reset=1").data.decode()
        second = c.get("/sweep?symbol=SYNTH3X").data.decode()
    import re

    def done(h):
        return int(re.search(r"(\d+) / 256 조합", h).group(1))

    assert done(second) > done(first)

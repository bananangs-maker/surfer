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
    """A sweep containing at least one COMPLETE neighbourhood.

    A prefix of the shuffled queue is representative of the axes but does not
    reliably contain any interior cell plus all eight of its neighbours, and without
    one there is nothing eligible to rank. So this fixture takes one interior cell
    and its full neighbourhood explicitly, plus a prefix for the distribution.
    """
    ds = load_synthetic(n_sessions=200)
    bh = buy_and_hold(ds, costs=Costs())
    bm = curve_metrics(bh)
    res = SW.SweepResult(bh_calmar=bm["calmar"], bh_mdd=bm["max_drawdown"],
                         total=len(SW.combos()))

    centre = {"trigger": 0.1, "stop": 1.2, "gate_pct": 80.0, "exit_buf": 0.25}
    idx = tuple(SW.GRID[a].index(centre[a]) for a in SW.AXES)
    wanted = [centre] + [
        dict(zip(SW.AXES, [SW.GRID[a][j] for a, j in zip(SW.AXES, n)]))
        for n in SW.neighbours(idx)
    ]
    for p in wanted + SW.combos()[:20]:
        if p not in [c.params for c in res.cells]:
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


def test_axis_medians_are_none_where_nothing_was_measured():
    """nan or 0.0 there would read as a measurement that had not been taken.

    Shuffled order means a normal partial sweep covers every axis value, so this
    exercises the case directly with a deliberately narrow set of cells.
    """
    from backtest import Costs, buy_and_hold, curve_metrics

    ds = load_synthetic(n_sessions=200)
    bh = buy_and_hold(ds, costs=Costs())
    bm = curve_metrics(bh)
    res = SW.SweepResult(bh_calmar=bm["calmar"], bh_mdd=bm["max_drawdown"],
                         total=len(SW.combos()))
    narrow = [p for p in SW.combos() if p["stop"] == SW.GRID["stop"][0]][:6]
    for p in narrow:
        res.cells.append(SW.run_one(ds, p, bh, bm["max_drawdown"]))
    a = SW.analyse(res)
    stop_row = a["axis_medians"]["stop"]
    assert stop_row[SW.GRID["stop"][0]] is not None
    assert all(stop_row[v] is None for v in SW.GRID["stop"][1:])
    assert a["coverage"]["stop"] == pytest.approx(0.25)


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


def test_any_prefix_of_the_queue_covers_every_axis_value():
    """The bug this fixes made a partial sweep measure one corner of the grid.

    itertools.product varies the last axis fastest, so the first 56 combinations all
    had trigger=0.05 — every top-ranked candidate shared that value, which read as a
    finding about the trigger and was an artefact of the loop order.
    """
    cs = SW.combos()
    for n in (14, 28, 56):
        prefix = cs[:n]
        for ax in SW.AXES:
            assert len({c[ax] for c in prefix}) == len(SW.GRID[ax]), (n, ax)


def test_shuffle_is_reproducible():
    """Two partial runs must cover the same cells, or progress is not comparable."""
    assert SW.combos()[:20] == SW.combos()[:20]
    assert SW.combos(shuffled=False)[:20] != SW.combos()[:20]


def test_analysis_reports_axis_coverage(small):
    a = SW.analyse(small)
    assert set(a["coverage"]) == set(SW.AXES)
    assert all(0.0 < v <= 1.0 for v in a["coverage"].values())
    assert a["partial"] is (len(small.cells) < small.total)


def test_only_interior_cells_are_selectable(small):
    """Worst-of-neighbourhood is a MINIMUM, so fewer neighbours scores higher free.

    On the first TQQQ run the top-ranked cell was a CORNER of the 4-D grid with 4
    neighbours against an interior cell's 8, and all ten leaders sat on at least one
    edge while none was fully interior. The ranking was measuring neighbour count.
    """
    a = SW.analyse(small)
    full = a["full_nb"]
    assert full == 2 * len(SW.AXES)
    for s in a["ranked"]:
        assert s["interior"] is True
        assert s["n_neighbours"] == full
    for s in a["boundary_ranked"]:
        assert s["n_neighbours"] < full


def test_boundary_cells_are_still_reported():
    """Not hidden: a parameter at the edge means the grid was drawn in the wrong
    place, which is worth knowing even though it cannot be selected."""
    from backtest import Costs, buy_and_hold, curve_metrics

    ds = load_synthetic(n_sessions=200)
    bh = buy_and_hold(ds, costs=Costs())
    bm = curve_metrics(bh)
    res = SW.SweepResult(bh_calmar=bm["calmar"], bh_mdd=bm["max_drawdown"],
                         total=len(SW.combos()))
    for p in SW.combos()[:60]:
        res.cells.append(SW.run_one(ds, p, bh, bm["max_drawdown"]))
    a = SW.analyse(res)
    assert a["boundary_ranked"], "boundary cells must be visible, just not eligible"


def test_no_interior_cells_blocks_selection():
    """A sweep too partial to contain a complete neighbourhood cannot select."""
    from backtest import Costs, buy_and_hold, curve_metrics

    ds = load_synthetic(n_sessions=180)
    bh = buy_and_hold(ds, costs=Costs())
    bm = curve_metrics(bh)
    res = SW.SweepResult(bh_calmar=bm["calmar"], bh_mdd=bm["max_drawdown"],
                         total=len(SW.combos()))
    for p in SW.combos()[:5]:
        res.cells.append(SW.run_one(ds, p, bh, bm["max_drawdown"]))
    a = SW.analyse(res)
    assert a["verdict"]["selectable"] is False
    assert "완전 내부" in a["verdict"]["reason"]


def test_sweep_page_continues_itself_until_done():
    """Twenty manual refreshes is a chore, not a tool."""
    from app import app

    with app.test_client() as c:
        partial = c.get("/sweep?symbol=SYNTH3X&reset=1").data.decode()
    assert 'http-equiv="refresh"' in partial

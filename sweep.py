"""Robustness sweep (SURFER-DES-002 §15).

NOT an optimiser. The maximum of a grid is not evidence when no out-of-sample data
remains: with 256 combinations something always passes, and whether it passed
because the rule is right or because the grid was large cannot be told apart from
the same sample that produced it.

What this measures is how STABLE results are across the parameter space. The
selection statistic is fixed in §15.3 before any result was seen:

    worst-of-neighbourhood Calmar

A combination scores as the WORST of itself and its immediate grid neighbours.
Picking the maximum picks noise; picking the worst-of-neighbourhood picks a region
that holds up when a parameter is slightly wrong - which is the property actually
needed, since nothing guarantees the parameters are right in live use.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from backtest import Costs, buy_and_hold, curve_metrics, run_backtest
from levels import PlaceholderBreakout, StructuralExit, VolatilityRegimeGate

# §15.2 grid. Fixed; extending it after seeing results is forbidden.
GRID = {
    "trigger": (0.05, 0.10, 0.20, 0.35),
    "stop": (0.8, 1.2, 1.8, 2.5),
    "gate_pct": (70.0, 80.0, 90.0, 101.0),   # 101 = no gate, as a grid point
    "exit_buf": (0.10, 0.25, 0.50, 1.00),
}
AXES = ("trigger", "stop", "gate_pct", "exit_buf")
FIXED = {"limit": 0.35, "exit_lookback": 1, "pct_window": 252}

# §15.4
PASS_RATE_FLOOR = 0.05
NEIGHBOUR_INSTABILITY_LIMIT = 0.50


@dataclass
class Cell:
    params: dict
    calmar: float
    cagr: float
    mdd: float
    mdd_ratio: float          # |surfer mdd| / |benchmark mdd|
    trades: int
    exposure: float
    ambiguous: float
    passes_mdd: bool


@dataclass
class SweepResult:
    cells: list = field(default_factory=list)
    bh_calmar: float = 0.0
    bh_mdd: float = 0.0
    total: int = 0

    @property
    def done(self) -> int:
        return len(self.cells)


def _gate(pct: float):
    """A gate at 101 never blocks: percentiles are capped at 100 by definition."""
    g = VolatilityRegimeGate()
    g.BLOCK_AT_PERCENTILE = pct
    return g


def build_generator(p: dict):
    base = PlaceholderBreakout(
        trigger_atr_mult=p["trigger"],
        limit_atr_mult=FIXED["limit"],
        stop_atr_mult=p["stop"],
    )
    if p["gate_pct"] > 100.0:
        return base
    g = _gate(p["gate_pct"])
    g.base = base
    return g


# Fixed seed: the ORDER is shuffled but reproducibly, so two runs of a partial
# sweep cover the same cells and can be compared.
SHUFFLE_SEED = 20260812


def combos(shuffled: bool = True) -> list[dict]:
    """Evaluation order is shuffled so that ANY PREFIX is a representative sample.

    itertools.product varies the last axis fastest, so the first N combinations all
    share one value of the first axis. A partial sweep then measured a single corner
    of the grid: with 56 of 256 cells done, every result had trigger=0.05, the
    top-ranked candidates all had trigger=0.05, and that looked like a finding about
    the trigger when it was an artefact of the loop order.

    Shuffling costs nothing and makes progress reports honest at every stage.
    """
    out = [
        dict(zip(AXES, vals))
        for vals in itertools.product(*(GRID[a] for a in AXES))
    ]
    if shuffled:
        import random

        random.Random(SHUFFLE_SEED).shuffle(out)
    return out


def run_one(ds, p: dict, bh, bh_mdd: float) -> Cell:
    from backtest import _stats

    res = run_backtest(
        ds, build_generator(p),
        exit_rule=StructuralExit(buffer_atr=p["exit_buf"]),
        costs=Costs(), acknowledge_quarantine=True,
    )
    m = curve_metrics(res.equity)
    st = _stats(res.closed_trades)
    n = st.get("n", 0)
    amb = (sum(1 for t in res.closed_trades if t.ambiguous) / n) if n else 0.0
    ratio = abs(m["max_drawdown"]) / abs(bh_mdd) if bh_mdd else float("inf")
    return Cell(
        params=dict(p), calmar=m["calmar"], cagr=m["cagr"],
        mdd=m["max_drawdown"], mdd_ratio=ratio, trades=n,
        exposure=res.exposure, ambiguous=amb, passes_mdd=ratio <= 0.50,
    )


def neighbours(idx: tuple[int, ...]) -> list[tuple[int, ...]]:
    """Immediate grid neighbours: one step along each axis."""
    out = []
    for ax in range(len(AXES)):
        for d in (-1, 1):
            j = list(idx)
            j[ax] += d
            if 0 <= j[ax] < len(GRID[AXES[ax]]):
                out.append(tuple(j))
    return out


def analyse(res: SweepResult) -> dict:
    """§15.3. Reports the distribution; names a region only via worst-of-
    neighbourhood, and only if §15.4 allows."""
    if not res.cells:
        return {"n": 0}

    index = {}
    for c in res.cells:
        idx = tuple(GRID[a].index(c.params[a]) for a in AXES)
        index[idx] = c

    calmars = np.array([c.calmar for c in res.cells], dtype=float)
    ratios = np.array([c.mdd_ratio for c in res.cells], dtype=float)
    pass_rate = float(np.mean([c.passes_mdd for c in res.cells]))

    # Worst-of-neighbourhood is a MINIMUM, so a cell with fewer neighbours scores
    # higher for free. On this grid the corner has 4 neighbours and an interior cell
    # has 8, which made the statistic systematically prefer the boundary: on the
    # first TQQQ run every one of the top ten sat on at least one edge and none was
    # fully interior. The ranking was measuring neighbour count, not robustness.
    #
    # Only cells with a COMPLETE neighbourhood are eligible. A boundary cell cannot
    # be shown robust - half its surroundings were never measured - and a parameter
    # sitting at the edge also means the grid was drawn in the wrong place.
    full_nb = 2 * len(AXES)
    scored, boundary = [], []
    for idx, c in index.items():
        nb = [index[j] for j in neighbours(idx) if j in index]
        vals = [c.calmar] + [x.calmar for x in nb]
        rec = {
            "cell": c,
            "worst_neighbour_calmar": float(min(vals)),
            "neighbour_range": float(max(vals) - min(vals)),
            "n_neighbours": len(nb),
            "interior": len(neighbours(idx)) == full_nb,
        }
        (scored if rec["interior"] and len(nb) == full_nb else boundary).append(rec)
    scored.sort(key=lambda s: s["worst_neighbour_calmar"], reverse=True)
    boundary.sort(key=lambda s: s["worst_neighbour_calmar"], reverse=True)

    best = scored[0] if scored else None
    verdict = {"selectable": False, "reason": ""}
    if not scored:
        verdict["reason"] = (
            f"완전 내부 조합이 아직 없습니다 — 이웃 {full_nb}개를 갖춘 조합만 "
            "견고성을 판정할 수 있습니다"
        )
    elif pass_rate < PASS_RATE_FLOOR:
        verdict["reason"] = (
            f"통과율 {pass_rate:.1%} < {PASS_RATE_FLOOR:.0%} — "
            "격자 안에 견고한 영역이 없습니다 (§15.4)"
        )
    elif best is None:
        verdict["reason"] = "이웃 정보가 부족합니다"
    elif best["neighbour_range"] > abs(best["worst_neighbour_calmar"]) * \
            (1.0 / NEIGHBOUR_INSTABILITY_LIMIT):
        verdict["reason"] = (
            f"최선 영역의 이웃 범위 {best['neighbour_range']:.3f}가 "
            f"자기 점수 {best['worst_neighbour_calmar']:.3f} 대비 과대 — "
            "그 영역도 취약합니다 (§15.4)"
        )
    else:
        verdict = {"selectable": True, "reason": "§15.4 통과 — 표본 내 후보로만"}

    # Coverage per axis: a partial sweep must say how much of each axis it has
    # actually seen, or a median over one value reads as a median over four.
    coverage = {
        a: len({c.params[a] for c in res.cells}) / len(GRID[a]) for a in AXES
    }
    return {
        "n": len(res.cells),
        "total": res.total,
        "coverage": coverage,
        "partial": len(res.cells) < res.total,
        "pass_rate": pass_rate,
        "bh_calmar": res.bh_calmar,
        "bh_mdd": res.bh_mdd,
        "calmar_q": [float(np.percentile(calmars, q)) for q in (0, 25, 50, 75, 100)],
        "ratio_q": [float(np.percentile(ratios, q)) for q in (0, 25, 50, 75, 100)],
        "ranked": scored[:12],
        "boundary_ranked": boundary[:6],
        "n_interior": len(scored),
        "full_nb": full_nb,
        "best": best,
        "verdict": verdict,
        # Partial sweeps leave some axis values with no cells yet; nan rather
        # than a spurious 0.0, and the page shows it as a dash.
        "axis_medians": {
            a: {
                v: (float(np.median(vals)) if (vals := [
                    c.calmar for c in res.cells if c.params[a] == v
                ]) else None)
                for v in GRID[a]
            }
            for a in AXES
        },
    }

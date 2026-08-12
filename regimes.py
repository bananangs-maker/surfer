"""Regime labels for SURFER-DES-002 §2.2R reporting.

A volatility gate's benefit and its cost live in different regimes: the benefit is
drawdown suppression during a decline, the cost is missed upside during the early,
high-volatility phase of a recovery. Averaged over a full sample the two offset
and neither is measured, which is exactly what happened on the 2024-2026 window
where the gate looked unambiguously good.

REPORTING ONLY — NEVER A TRADING INPUT
--------------------------------------
These labels use running peaks and troughs of the benchmark, so a label for a
given session depends on what happened afterwards. Feeding them to the engine
would be lookahead of the most flattering kind. `engine.py` cannot import this
module and nothing here is passed to a generator; the split is applied to
completed equity curves after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

import numpy as np


class Regime(Enum):
    ADVANCE = "advance"
    DECLINE = "decline"
    RECOVERY = "recovery"


# §2.2R thresholds. Fixed before the out-of-sample data was available.
NEAR_PEAK = 0.95            # within 5% of the running peak -> ADVANCE
DRAWDOWN_ENTER = -0.20      # benchmark 20% off peak -> not ADVANCE
NEW_LOW_WINDOW = 20         # sessions since the running trough -> DECLINE
REBOUND_OFF_LOW = 0.20      # 20% up from the trough -> RECOVERY


def label(bh_curve: list[tuple[date, float]]) -> list[Regime]:
    """Classify each session from the BENCHMARK curve alone.

    Using the benchmark rather than SURFER's own equity is deliberate: the labels
    must not depend on the system being evaluated, or a system that sidesteps a
    decline would relabel the decline out of existence.
    """
    vals = np.array([v for _, v in bh_curve], dtype=float)
    n = len(vals)
    out: list[Regime] = []
    peak = vals[0]
    trough = vals[0]
    trough_i = 0

    for i in range(n):
        v = vals[i]
        if v > peak:
            peak = v
            trough = v
            trough_i = i
        if v < trough:
            trough = v
            trough_i = i

        dd = v / peak - 1.0
        if dd > DRAWDOWN_ENTER or v >= peak * NEAR_PEAK:
            out.append(Regime.ADVANCE)
        elif v >= trough * (1.0 + REBOUND_OFF_LOW):
            out.append(Regime.RECOVERY)
        elif i - trough_i <= NEW_LOW_WINDOW:
            out.append(Regime.DECLINE)
        else:
            out.append(Regime.DECLINE)
    return out


@dataclass
class RegimeResult:
    regime: Regime
    sessions: int
    surfer_return: float
    bh_return: float
    surfer_mdd: float
    bh_mdd: float

    @property
    def participation(self) -> float | None:
        """Share of the benchmark's move that SURFER captured.

        Defined only when the benchmark rose: participation in a decline is not a
        meaningful ratio, and the decline test is a drawdown comparison instead.
        """
        if self.bh_return <= 0:
            return None
        return self.surfer_return / self.bh_return


def _segment(vals: np.ndarray) -> tuple[float, float]:
    """Return (total return, max drawdown) for one contiguous stretch."""
    if len(vals) < 2:
        return 0.0, 0.0
    r = float(vals[-1] / vals[0] - 1.0)
    peak = np.maximum.accumulate(vals)
    return r, float((vals / peak - 1.0).min())


def split(
    surfer_curve: list[tuple[date, float]],
    bh_curve: list[tuple[date, float]],
) -> dict[Regime, RegimeResult]:
    """Aggregate both curves by regime.

    Segments of the same regime are chained multiplicatively rather than treated
    as one span, because the stretches are not contiguous in time and a single
    first-to-last ratio across gaps would be meaningless.
    """
    labels = label(bh_curve)
    s = np.array([v for _, v in surfer_curve], dtype=float)
    b = np.array([v for _, v in bh_curve], dtype=float)

    out: dict[Regime, RegimeResult] = {}
    for reg in Regime:
        idx = [i for i, l in enumerate(labels) if l is reg]
        if not idx:
            out[reg] = RegimeResult(reg, 0, 0.0, 0.0, 0.0, 0.0)
            continue
        # Contiguous runs of this regime.
        runs: list[list[int]] = []
        cur = [idx[0]]
        for i in idx[1:]:
            if i == cur[-1] + 1:
                cur.append(i)
            else:
                runs.append(cur)
                cur = [i]
        runs.append(cur)

        sr = br = 1.0
        smdd = bmdd = 0.0
        for run in runs:
            a, dd = _segment(s[run])
            sr *= 1.0 + a
            smdd = min(smdd, dd)
            a2, dd2 = _segment(b[run])
            br *= 1.0 + a2
            bmdd = min(bmdd, dd2)
        out[reg] = RegimeResult(
            reg, len(idx), sr - 1.0, br - 1.0, smdd, bmdd
        )
    return out


# §2.2R verdict thresholds
DECLINE_MDD_SHARE = 0.50     # SURFER drawdown <= 50% of benchmark's, in DECLINE
RECOVERY_PARTICIPATION = 0.40  # SURFER captures >= 40% of benchmark's, in RECOVERY


def verdict_2_2R(
    surfer_curve: list[tuple[date, float]],
    bh_curve: list[tuple[date, float]],
) -> dict:
    """All three conditions must pass (§12.1)."""
    parts = split(surfer_curve, bh_curve)
    dec, rec = parts[Regime.DECLINE], parts[Regime.RECOVERY]

    decline_ok = (
        abs(dec.surfer_mdd) <= DECLINE_MDD_SHARE * abs(dec.bh_mdd)
        if dec.sessions and dec.bh_mdd < 0 else None
    )
    p = rec.participation
    recovery_ok = (p >= RECOVERY_PARTICIPATION) if p is not None else None

    final = float(surfer_curve[-1][1]) if surfer_curve else 0.0
    start = float(surfer_curve[0][1]) if surfer_curve else 0.0
    absolute_ok = final >= start

    measurable = decline_ok is not None and recovery_ok is not None
    return {
        "regimes": parts,
        "decline_pass": decline_ok,
        "decline_allowed_mdd": (
            -DECLINE_MDD_SHARE * abs(dec.bh_mdd) if dec.sessions else None
        ),
        "recovery_pass": recovery_ok,
        "recovery_participation": p,
        "absolute_pass": absolute_ok,
        "measurable": measurable,
        "pass": bool(measurable and decline_ok and recovery_ok and absolute_ok),
        "note": (
            "판정 가능" if measurable else
            "판정 불가 — 표본에 하락 또는 회복 국면이 없음"
        ),
    }


@dataclass
class Coverage:
    """Whether a window can answer §2.2R at all.

    Asked before a verdict is attempted, because a window with no decline returns
    "PASS" on two of three conditions by vacuity — and a page showing two PASSes
    reads as validation.
    """

    sessions: int
    counts: dict
    deepest_drawdown: float
    has_decline: bool
    has_recovery: bool
    engine_ready: bool
    min_sessions_needed: int

    @property
    def measurable(self) -> bool:
        return self.has_decline and self.has_recovery and self.engine_ready

    def render(self) -> str:
        lines = [
            "REGIME COVERAGE (§2.2R measurability)",
            f"  sessions                : {self.sessions}",
            f"  deepest benchmark DD    : {self.deepest_drawdown:.1%}",
            f"  ADVANCE / DECLINE / RECOVERY : "
            f"{self.counts.get(Regime.ADVANCE, 0)} / "
            f"{self.counts.get(Regime.DECLINE, 0)} / "
            f"{self.counts.get(Regime.RECOVERY, 0)}",
            f"  has DECLINE             : {self.has_decline}",
            f"  has RECOVERY            : {self.has_recovery}",
            f"  engine-spec history     : {self.engine_ready} "
            f"(needs {self.min_sessions_needed})",
            f"  MEASURABLE              : {self.measurable}",
        ]
        if not self.measurable:
            lines.append("  -> §2.2R cannot be judged on this window.")
        return "\n".join(lines)


def coverage(
    bh_curve: list[tuple[date, float]], min_sessions_needed: int = 284
) -> Coverage:
    from collections import Counter

    labs = label(bh_curve)
    vals = np.array([v for _, v in bh_curve], dtype=float)
    peak = np.maximum.accumulate(vals) if len(vals) else np.array([1.0])
    dd = float((vals / peak - 1.0).min()) if len(vals) else 0.0
    counts = dict(Counter(labs))
    return Coverage(
        sessions=len(bh_curve),
        counts=counts,
        deepest_drawdown=dd,
        has_decline=counts.get(Regime.DECLINE, 0) > 0,
        has_recovery=counts.get(Regime.RECOVERY, 0) > 0,
        engine_ready=len(bh_curve) >= min_sessions_needed,
        min_sessions_needed=min_sessions_needed,
    )

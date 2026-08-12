"""Regime-split tests (SURFER-DES-002 §2.2R).

The criterion these implement replaced §2.2, which was shown to be unsatisfiable
in a rising window by any rule that reduces exposure. The point of splitting is
that a volatility gate's benefit and its cost sit in different regimes and cancel
when averaged.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from regimes import Regime, label, split, verdict_2_2R


def curve(vals, start=dt.date(2018, 1, 1)):
    return [(start + dt.timedelta(days=i), float(v)) for i, v in enumerate(vals)]


@pytest.fixture
def benchmark():
    """Advance, crash, recovery, advance."""
    return curve(
        [100] * 40
        + list(np.linspace(100, 35, 60))
        + list(np.linspace(35, 80, 50))
        + [82, 85, 90, 95, 100, 105] * 4
    )


def test_all_three_regimes_are_found(benchmark):
    labs = label(benchmark)
    assert len(labs) == len(benchmark)
    assert set(labs) == set(Regime)


def test_regime_labels_come_from_the_benchmark_only(benchmark):
    """A system that sidestepped the decline must not relabel it away."""
    flat = curve([100.0] * len(benchmark))
    a = split(flat, benchmark)
    b = split(curve([100.0 + i for i in range(len(benchmark))]), benchmark)
    assert {r: v.sessions for r, v in a.items()} == {
        r: v.sessions for r, v in b.items()
    }


def test_a_system_that_halves_the_decline_passes_that_condition(benchmark):
    s = [100.0]
    b = [v for _, v in benchmark]
    for i in range(1, len(b)):
        r = b[i] / b[i - 1] - 1
        s.append(s[-1] * (1 + (r * 0.25 if r < 0 else r * 0.6)))
    v = verdict_2_2R(curve(s), benchmark)
    assert v["measurable"] is True
    assert v["decline_pass"] is True


def test_a_system_that_misses_the_recovery_fails(benchmark):
    """The gate's known failure mode, made explicit."""
    b = [v for _, v in benchmark]
    labs = label(benchmark)
    s = [100.0]
    for i in range(1, len(b)):
        r = b[i] / b[i - 1] - 1
        # Sidesteps the decline entirely but sits out the recovery.
        mult = 0.0 if labs[i] in (Regime.DECLINE, Regime.RECOVERY) else 1.0
        s.append(s[-1] * (1 + r * mult))
    v = verdict_2_2R(curve(s), benchmark)
    assert v["recovery_participation"] == pytest.approx(0.0, abs=1e-9)
    assert v["recovery_pass"] is False
    assert v["pass"] is False


def test_a_sample_without_a_decline_is_not_measurable():
    """The 2024-2026 window. §2.2R must say so rather than returning a verdict."""
    rising = curve([100 + i for i in range(300)])
    v = verdict_2_2R(rising, rising)
    assert v["measurable"] is False
    assert v["pass"] is False
    assert "판정 불가" in v["note"]


def test_losing_money_overall_fails_regardless_of_regimes(benchmark):
    s = curve([100.0] * (len(benchmark) - 1) + [80.0])
    v = verdict_2_2R(s, benchmark)
    assert v["absolute_pass"] is False
    assert v["pass"] is False


def test_thresholds_are_the_preregistered_ones():
    import regimes

    assert regimes.DECLINE_MDD_SHARE == 0.50
    assert regimes.RECOVERY_PARTICIPATION == 0.40


def test_engine_cannot_import_regimes():
    """§2.2R labels use running peaks and troughs, so they contain future
    information. Feeding them to the engine would be lookahead."""
    import pathlib

    src = pathlib.Path(__file__).with_name("engine.py").read_text()
    assert "regimes" not in src

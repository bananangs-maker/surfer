"""Window lock and loader guard tests.

Both cover failures that produce no error message. A contaminated validation
window and a mislabelled bar interval both yield plausible-looking numbers, which
is exactly why they need mechanical checks rather than care.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from loaders import load_synthetic
from windows import (LOCK_FILE, SELECTION_USED_FROM, WindowLocked,
                     describe_windows, selection_slice, unlock_validation,
                     validation_slice)


@pytest.fixture(autouse=True)
def clean_lock():
    """Never let a test leave an unlock record behind."""
    saved = LOCK_FILE.read_text() if LOCK_FILE.exists() else None
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()
    yield
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()
    if saved is not None:
        LOCK_FILE.write_text(saved)


@pytest.fixture(scope="module")
def spanning():
    """Data straddling the boundary, which §12.4 reversed: selection is
    2024-08-01 onward, validation is everything before it."""
    return load_synthetic(n_sessions=900, start="2022-01-03")


def test_selection_window_is_always_available(spanning):
    """Selection = sessions already spent choosing candidate D (2024-08 onward)."""
    sel = selection_slice(spanning)
    assert sel.sessions
    assert all(d >= SELECTION_USED_FROM for d in sel.sessions)


def test_lock_state_matches_the_repeal_flag(spanning):
    """§3.3 was repealed 2026-08-12, so the slice no longer raises.

    The test follows the flag rather than asserting one behaviour, so restoring
    the split by setting SPLIT_REPEALED=False re-arms the guard and this test
    checks it again instead of needing an edit.
    """
    from windows import SPLIT_REPEALED

    if SPLIT_REPEALED:
        val = validation_slice(spanning)
        assert all(d < SELECTION_USED_FROM for d in val.sessions)
    else:
        with pytest.raises(WindowLocked):
            validation_slice(spanning)


def test_unlocking_requires_a_named_candidate(spanning):
    with pytest.raises(ValueError):
        unlock_validation("best one", note="whatever")
    with pytest.raises(ValueError):
        unlock_validation("A", note="   ")


def test_unlock_then_validation_opens(spanning):
    """The unlock machinery still works, for when the split is restored."""
    unlock_validation("B", note="선택 구간에서 B가 두 조건 모두 통과")
    val = validation_slice(spanning)
    assert val.sessions
    assert all(d < SELECTION_USED_FROM for d in val.sessions)
    rec = json.loads(LOCK_FILE.read_text())
    assert rec["chosen_candidate"] == "B"
    assert rec["note"]


def test_cannot_unlock_twice(spanning):
    unlock_validation("A", note="첫 선택")
    with pytest.raises(WindowLocked):
        unlock_validation("C", note="다시 골라보기")


def test_windows_together_cover_every_session(spanning):
    unlock_validation("A", note="합집합 확인")
    sel = selection_slice(spanning)
    val = validation_slice(spanning)
    assert len(sel.sessions) + len(val.sessions) == len(spanning.sessions)
    assert not (set(sel.sessions) & set(val.sessions))


def test_describe_flags_a_feed_with_no_out_of_sample_sessions():
    """The free yfinance window is entirely selection data (2024-08 onward).

    §2.2R cannot run on it at all, and the status line has to say so — a signal
    page reads as authoritative whether or not anything was held back.
    """
    ds = load_synthetic(n_sessions=200, start="2024-08-01")
    text = describe_windows(ds)
    assert "LOCKED" in text
    assert "no out-of-sample sessions" in text


# --- loader interval guard ------------------------------------------------

def _firstrate_file(tmp_path, spacing_minutes: int):
    import random

    random.seed(5)
    rows, px = [], 40.0
    d = dt.date(2019, 11, 1)
    days = []
    while len(days) < 20:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    for day in days:
        t = dt.datetime.combine(day, dt.time(9, 30))
        end = dt.datetime.combine(day, dt.time(16, 0))
        while t < end:
            o = px
            c = o * (1 + random.gauss(0, 0.005))
            rows.append(
                f"{t:%Y-%m-%d %H:%M:%S},{o:.4f},{max(o,c)*1.001:.4f},"
                f"{min(o,c)*0.999:.4f},{c:.4f},{random.randint(1000,9999)}"
            )
            px = c
            t += dt.timedelta(minutes=spacing_minutes)
    f = tmp_path / f"TQQQ_{spacing_minutes}min.csv"
    f.write_text("\n".join(rows) + "\n")
    return f


def test_sixty_minute_file_loads(tmp_path):
    from loaders import load_firstrate_csv
    from schema import Adjustment

    ds = load_firstrate_csv(_firstrate_file(tmp_path, 60), "TQQQ",
                            adjustment=Adjustment.SPLIT_AND_DIVIDEND)
    assert len(ds.sessions) == 20
    assert not ds.quarantined


def test_thirty_minute_file_is_rejected_not_relabelled(tmp_path):
    """The silent-corruption case: a 30-minute file loaded as 60-minute marks
    every bar a stub and quietly breaks ATR, levels and the ambiguity rate."""
    from loaders import IntervalMismatch, load_firstrate_csv
    from schema import Adjustment

    with pytest.raises(IntervalMismatch):
        load_firstrate_csv(_firstrate_file(tmp_path, 30), "TQQQ",
                           adjustment=Adjustment.SPLIT_AND_DIVIDEND)


def test_unknown_adjustment_is_rejected(tmp_path):
    from loaders import load_firstrate_csv
    from schema import Adjustment

    with pytest.raises(ValueError):
        load_firstrate_csv(_firstrate_file(tmp_path, 60), "TQQQ",
                           adjustment=Adjustment.UNKNOWN)

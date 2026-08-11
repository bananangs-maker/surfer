"""These tests pin the three fill rules. If one starts failing, the engine has
silently become optimistic - which is the failure mode that matters here, since
an optimistic engine still produces confident-looking output.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from surfer.fills import Kind, NoFill, adjudicate_session
from surfer.schema import LevelSet, Side

SD = date(2025, 3, 3)


def bar(o, h, l, c, i=0):
    return {
        "ts": pd.Timestamp("2025-03-03 14:30", tz="UTC") + pd.Timedelta(hours=i),
        "open": o, "high": h, "low": l, "close": c,
        "volume": 1e6, "session_date": SD, "bar_minutes": 60.0, "is_stub": False,
    }


def frame(*bars):
    return pd.DataFrame(list(bars))


def levels(trigger=100.0, limit=101.0, stop=97.0, target=None):
    return LevelSet(SD, Side.LONG, trigger, limit, stop, target)


# --- Rule 2: entry is a stop-LIMIT ---------------------------------------

def test_no_trigger_no_fill():
    out = adjudicate_session(levels(), frame(bar(98, 99.5, 97.5, 99)))
    assert out.entry is None
    assert out.no_fill_reason is NoFill.NO_TRIGGER


def test_clean_trigger_fills_at_trigger():
    out = adjudicate_session(levels(), frame(bar(99, 100.5, 98.8, 100.4)))
    assert out.entry.price == pytest.approx(100.0)
    assert out.entry.at_gap is False


def test_gap_inside_band_fills_at_open():
    out = adjudicate_session(levels(), frame(bar(100.6, 102, 100.4, 101.5)))
    assert out.entry.price == pytest.approx(100.6)
    assert out.entry.at_gap is True


def test_gap_through_limit_is_a_missed_trade_not_a_loss():
    # Opens above the limit and never trades back into the band.
    out = adjudicate_session(levels(), frame(bar(103, 104, 102, 103.5)))
    assert out.entry is None
    assert out.no_fill_reason is NoFill.GAP_THROUGH_LIMIT


def test_gap_past_limit_then_back_fills_at_limit():
    out = adjudicate_session(levels(), frame(bar(103, 104, 100.5, 101)))
    assert out.entry.price == pytest.approx(101.0)  # the limit, not the open
    assert out.entry.at_gap is True


# --- Rule 2: the protective stop is a PLAIN stop -------------------------

def test_stop_fills_at_stop_when_traded_through():
    out = adjudicate_session(
        levels(), frame(bar(99, 100.5, 100.0, 100.4, 0), bar(100.2, 100.3, 96.0, 96.5, 1))
    )
    assert out.exit.kind is Kind.STOP
    assert out.exit.price == pytest.approx(97.0)
    assert out.exit.at_gap is False


def test_stop_gaps_through_and_fills_worse_than_the_stop():
    out = adjudicate_session(
        levels(), frame(bar(99, 100.5, 100.0, 100.4, 0), bar(93.0, 94.0, 92.0, 93.5, 1))
    )
    assert out.exit.price == pytest.approx(93.0)  # NOT 97.0
    assert out.exit.at_gap is True


# --- Rule 1: adverse intra-bar ordering ---------------------------------

def test_entry_and_stop_in_one_bar_resolves_adversely_and_flags():
    out = adjudicate_session(levels(), frame(bar(99, 100.6, 96.5, 98)))
    assert out.entry is not None and out.exit is not None
    assert out.exit.kind is Kind.STOP
    assert out.ambiguous is True
    assert out.exit.price < out.entry.price  # a loss, by assumption


def test_stop_and_target_in_one_bar_assumes_stop():
    out = adjudicate_session(
        levels(target=105.0),
        frame(bar(99, 100.5, 100.0, 100.4, 0), bar(100.2, 106.0, 96.0, 105.5, 1)),
    )
    assert out.exit.kind is Kind.STOP
    assert out.ambiguous is True


def test_unambiguous_target_is_taken():
    out = adjudicate_session(
        levels(target=105.0),
        frame(bar(99, 100.5, 100.0, 100.4, 0), bar(100.4, 106.0, 100.2, 105.5, 1)),
    )
    assert out.exit.kind is Kind.TARGET
    assert out.ambiguous is False


# --- swing behaviour ----------------------------------------------------

def test_position_left_open_is_a_swing_hold_not_a_close():
    out = adjudicate_session(levels(), frame(bar(99, 100.5, 99.5, 100.4)))
    assert out.entry is not None
    assert out.exit is None
    assert out.open_at_session_end is True


def test_no_levels_means_no_outcome():
    out = adjudicate_session(None, frame(bar(99, 101, 98, 100)))
    assert out.entry is None and out.ambiguous_bars == 0


# --- schema guards ------------------------------------------------------

def test_stop_above_trigger_is_rejected():
    with pytest.raises(ValueError):
        LevelSet(SD, Side.LONG, 100.0, 101.0, 100.5)


def test_limit_below_trigger_is_rejected():
    with pytest.raises(ValueError):
        LevelSet(SD, Side.LONG, 100.0, 99.0, 97.0)

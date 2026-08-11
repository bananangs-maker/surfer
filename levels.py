"""Level generation: the plug that is deliberately left empty.

CONTRACT
--------
A level generator is a PURE FUNCTION of prior sessions. It receives only bars
that closed strictly before the session it is arming, and it holds no state.

    generator(history: list[pd.DataFrame], armed_for: date) -> LevelSet | None

Purity is not stylistic. It is what makes the ambiguity diagnostic in
diagnostics.py interpretable: if the generator could see the session it is
arming, every measurement downstream is contaminated and no amount of careful
fill modelling recovers it.

The generator answers "where", never "when". Timing is fixed by the operating
loop - once per day, before the session - because the whole reason SURFER exists
in this shape is that nobody is awake at 03:00 KST to act on a bar close.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

import numpy as np
import pandas as pd

from schema import LevelSet, Side


class LevelGenerator(Protocol):
    name: str
    # How many prior sessions the generator actually reads. Declared, not
    # guessed: the diagnostic slices history to this window, which is what keeps
    # a full run linear in session count instead of quadratic.
    lookback_sessions: int

    def __call__(
        self, history: list[pd.DataFrame], armed_for: date
    ) -> LevelSet | None: ...


def true_range_60m(session: pd.DataFrame, prev_close: float | None) -> np.ndarray:
    """Per-bar true range. Stub bars are EXCLUDED by the caller, not here."""
    high = session["high"].to_numpy()
    low = session["low"].to_numpy()
    close = np.concatenate(
        [[prev_close if prev_close is not None else session["close"].iloc[0]],
         session["close"].to_numpy()[:-1]]
    )
    return np.maximum.reduce([high - low, np.abs(high - close), np.abs(low - close)])


def stub_range_ratio(history: list[pd.DataFrame]) -> dict[str, float]:
    """Measure, don't assume: how does the 30-minute stub bar's range compare?

    The obvious guess is that a half-length bar has a smaller range, so leaving
    stubs in biases ATR downward. That guess is WRONG, at least on the fixture
    used here and plausibly on real data: the 15:30-16:00 window contains the
    closing auction and the last-hour volume ramp, so a 30-minute stub bar can
    carry a LARGER true range than an average full midday bar.

    The direction of the bias is therefore an empirical question about the
    intraday volatility profile of the specific instrument, not something to be
    settled by reasoning. Run this on real TQQQ/SOXL bars before fixing the
    exclude_stubs decision in the pre-registration document. Ratios are
    price-normalised so they compare bar shape rather than drift.
    """
    stub, full = [], []
    prev_close: float | None = None
    for sess in history:
        if len(sess) == 0:
            continue
        tr = true_range_60m(sess, prev_close)
        mask = sess["is_stub"].to_numpy()
        px = sess["close"].to_numpy()
        stub.extend((tr[mask] / px[mask]).tolist())
        full.extend((tr[~mask] / px[~mask]).tolist())
        prev_close = float(sess["close"].iloc[-1])
    ms = float(np.mean(stub)) if stub else float("nan")
    mf = float(np.mean(full)) if full else float("nan")
    return {
        "stub_mean_tr_pct": ms,
        "full_mean_tr_pct": mf,
        "ratio": ms / mf if mf else float("nan"),
        "n_stub": len(stub),
        "n_full": len(full),
    }


def atr60(
    history: list[pd.DataFrame],
    lookback_bars: int = 42,
    exclude_stubs: bool = True,
) -> float | None:
    """ATR over 60-minute bars.

    exclude_stubs is an UNRESOLVED design decision, not a settled default. It
    is True here only so the placeholder generator runs; see stub_range_ratio()
    for why the direction of the resulting bias cannot be assumed. Flipping this
    flag moves every level this module emits, so it belongs in the
    pre-registration document with a measurement attached.

    Note also that excluding stubs shortens the bar sequence, so a fixed
    lookback_bars window then spans a different number of calendar sessions
    between the two settings. That is a second, separate confound.
    """
    trs: list[float] = []
    prev_close: float | None = None
    for sess in history:
        if len(sess) == 0:
            continue
        tr = true_range_60m(sess, prev_close)
        keep = ~sess["is_stub"].to_numpy() if exclude_stubs else np.ones(len(sess), bool)
        trs.extend(tr[keep].tolist())
        prev_close = float(sess["close"].iloc[-1])
    if len(trs) < lookback_bars:
        return None
    return float(np.mean(trs[-lookback_bars:]))


class PlaceholderBreakout:
    """NOT A STRATEGY. A shim so the engine can be exercised end to end.

    It is a prior-session-high breakout with an ATR-scaled limit and a stop at
    the prior session low. It is here to make the pipeline runnable and to give
    the ambiguity diagnostic something to measure. It has not been reasoned
    about, argued for, or tested, and its parameters were chosen to be roughly
    plausible rather than good.

    Replace it before any performance number is computed. If a performance
    number is ever quoted while this class is still in the loop, that number is
    an artefact of an arbitrary shim.
    """

    name = "PLACEHOLDER_prior_high_breakout"

    # Reads only the previous session plus enough bars for a 42-bar ATR.
    # A regular session yields 7 bars, an early close 4, so 12 sessions supply
    # 48-84 bars - comfortably above 42 while keeping the window tight. Every
    # extra session here is re-read on every step of a run, so slack is costly.
    lookback_sessions = 12

    def __init__(
        self,
        trigger_atr_mult: float = 0.10,
        limit_atr_mult: float = 0.35,
        stop_atr_mult: float = 1.20,
        min_history_sessions: int = 10,
    ) -> None:
        self.trigger_atr_mult = trigger_atr_mult
        self.limit_atr_mult = limit_atr_mult
        self.stop_atr_mult = stop_atr_mult
        self.min_history_sessions = min_history_sessions

    def __call__(
        self, history: list[pd.DataFrame], armed_for: date
    ) -> LevelSet | None:
        if len(history) < self.min_history_sessions:
            return None
        a = atr60(history)
        if a is None or a <= 0:
            return None

        prior = history[-1]
        prior_high = float(prior["high"].max())
        prior_close = float(prior["close"].iloc[-1])

        trigger = prior_high + self.trigger_atr_mult * a
        limit = trigger + self.limit_atr_mult * a
        stop = trigger - self.stop_atr_mult * a

        if stop >= trigger or limit < trigger:
            return None

        return LevelSet(
            session_date=armed_for,
            side=Side.LONG,
            entry_trigger=round(trigger, 4),
            entry_limit=round(limit, 4),
            initial_stop=round(stop, 4),
            target=None,
            meta={
                "generator": self.name,
                "atr60": round(a, 4),
                "prior_high": prior_high,
                "prior_close": prior_close,
                "is_placeholder": True,
            },
        )

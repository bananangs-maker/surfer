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

from schema import EntryStyle, LevelSet, Side


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

    MEASURED, TQQQ 60m, 730 sessions (Aug 2024 - Aug 2026):
        stub 1.064% vs full 1.705% of price  ->  ratio 0.62x
    The stub bar is NARROWER. Excluding stubs therefore RAISES the ATR estimate,
    and leaving them in biases it downward.

    The synthetic fixture reports 1.30x - the opposite direction - because its
    intraday volatility profile weights the closing bar most heavily. That is a
    property of the fixture, not of TQQQ. Treat it as a warning: this question
    cannot be settled on synthetic data, and could not have been settled by
    reasoning either. Only the real series answers it.

    Still unsettled for SOXL, which has a different intraday profile. Measure it
    before fixing exclude_stubs in the pre-registration document.

    Ratios are price-normalised so they compare bar shape rather than drift.
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

    exclude_stubs=True is now supported by measurement on TQQQ, where the stub
    bar is 0.62x the width of a full bar, so including it drags ATR down. This
    is an empirical result, not the a-priori reasoning that first justified the
    default - that reasoning happened to reach the right answer for the wrong
    reasons, and the synthetic fixture points the other way. Confirm on SOXL
    before fixing it in the pre-registration document.

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


class StructuralExit:
    """Exit on a break of recent structure, recomputed each held session.

    CONTRACT
    --------
        rule(history, prior_value) -> float | None

    Pure function of completed sessions, like the level generator. It returns a
    price, not a decision - the same discipline, for the same reason: the
    operating loop places resting orders once a day, so an exit must be
    expressible as a level.

    RELATIONSHIP TO THE FIXED STOP
    ------------------------------
    This does NOT replace the fixed stop and does not contradict the decision to
    keep it fixed. Position.effective_stop takes the higher of the two, so:

        early in a trade    -> fixed stop dominates (structure is still below it)
        as price advances   -> structure rises through it and becomes the exit

    The fixed stop is the disaster floor. This is the normal exit path. Without
    it the system could only ever leave a trade at a loss - measured on the
    fixture, 48 of 48 closed trades exited on the stop, win rate 0%, because a
    fixed stop with no exit rule holds winners indefinitely.

    RATCHET
    -------
    `ratchet=True` forbids the level from falling. A structure low can retreat
    while still sitting above the fixed stop, which would loosen protection
    mid-trade - the dangerous direction on a 3x instrument. Ratcheting is a
    recorded design decision, not a tuning knob: turning it off changes the risk
    profile of every trade and belongs in the pre-registration document.

    ENTRY SESSION
    -------------
    Deliberately NOT armed on the entry session; only the fixed stop is live
    there. This keeps entry-session adjudication identical between the backtest
    and the ambiguity diagnostic, so the two remain comparable. It also means a
    trade cannot be structurally stopped out before it has held a full session.
    """

    name = "structural_prior_low_break"
    lookback_sessions = 12

    def __init__(
        self,
        low_lookback_sessions: int = 1,
        buffer_atr: float = 0.25,
        ratchet: bool = True,
    ) -> None:
        self.low_lookback_sessions = low_lookback_sessions
        self.buffer_atr = buffer_atr
        self.ratchet = ratchet

    def __call__(
        self, history: list[pd.DataFrame], prior_value: float | None = None
    ) -> float | None:
        if len(history) < max(1, self.low_lookback_sessions):
            return prior_value
        recent = history[-self.low_lookback_sessions:]
        low = float(min(float(s["low"].min()) for s in recent if len(s)))

        # A bare structure low triggers on ordinary noise, so back it off by a
        # fraction of ATR. The size of that buffer is a parameter to measure,
        # not a claim - it trades exit frequency against give-back.
        a = atr60(history)
        level = low - (self.buffer_atr * a if a else 0.0)

        if self.ratchet and prior_value is not None:
            level = max(level, prior_value)
        return round(level, 4)


# ============================================================
# CANDIDATE ENTRY RULES
#
# These are CANDIDATES, not recommendations. Each is a different hypothesis
# about what SURFER is for, and they are mutually exclusive in practice.
#
# HOW TO CHOOSE - and how not to.
# Running all of them and keeping the best number is selection on the sample.
# With three candidates and a few parameters each, something will look good on
# any two-year window, and that appearance carries no information. The criterion
# has to be written down BEFORE the comparison is run: which metric decides,
# what margin counts as a real difference, and on which window. Otherwise the
# comparison manufactures a winner.
#
# All three place resting orders derived only from completed sessions, so all
# three are executable by the operating loop.
# ============================================================


class PullbackToPriorLow:
    """CANDIDATE: buy weakness into the prior session's low.

    Hypothesis: SURFER's job is to get filled at a better price than a late
    market order would, which means buying dips inside a regime MATILDA has
    already approved. This is the rule that matches "catch up on a missed
    signal" most directly.

    The floor matters more here than in a breakout. A gap far below the prior low
    is not a discount, it is a different market, so the order is abandoned rather
    than filled at any price.
    """

    name = "CANDIDATE_pullback_to_prior_low"
    lookback_sessions = 12

    def __init__(
        self,
        entry_atr_mult: float = 0.20,
        floor_atr_mult: float = 0.80,
        stop_atr_mult: float = 1.50,
        min_history_sessions: int = 10,
    ) -> None:
        self.entry_atr_mult = entry_atr_mult
        self.floor_atr_mult = floor_atr_mult
        self.stop_atr_mult = stop_atr_mult
        self.min_history_sessions = min_history_sessions

    def __call__(self, history, armed_for):
        if len(history) < self.min_history_sessions:
            return None
        a = atr60(history)
        if a is None or a <= 0:
            return None
        prior = history[-1]
        prior_low = float(prior["low"].min())

        trigger = prior_low + self.entry_atr_mult * a   # buy limit just above the low
        floor = trigger - self.floor_atr_mult * a
        stop = floor - self.stop_atr_mult * a
        if not (stop < floor <= trigger):
            return None
        return LevelSet(
            session_date=armed_for, side=Side.LONG,
            entry_trigger=round(trigger, 4), entry_limit=round(floor, 4),
            initial_stop=round(stop, 4), target=None,
            style=EntryStyle.PULLBACK,
            meta={"generator": self.name, "atr60": round(a, 4),
                  "prior_low": prior_low, "is_candidate": True},
        )


class PriorCloseVolatilityBreakout:
    """CANDIDATE: buy a move of k*ATR beyond the prior close.

    Hypothesis: SURFER trades intraday expansion rather than structure, so the
    reference is the last traded price and the threshold is volatility-scaled.
    Differs from the prior-high rule in that it can fire on a day that never
    exceeds the prior session's high - it is a move-size rule, not a level rule.
    """

    name = "CANDIDATE_prior_close_vol_breakout"
    lookback_sessions = 12

    def __init__(
        self,
        trigger_atr_mult: float = 0.60,
        limit_atr_mult: float = 0.40,
        stop_atr_mult: float = 1.20,
        min_history_sessions: int = 10,
    ) -> None:
        self.trigger_atr_mult = trigger_atr_mult
        self.limit_atr_mult = limit_atr_mult
        self.stop_atr_mult = stop_atr_mult
        self.min_history_sessions = min_history_sessions

    def __call__(self, history, armed_for):
        if len(history) < self.min_history_sessions:
            return None
        a = atr60(history)
        if a is None or a <= 0:
            return None
        prior_close = float(history[-1]["close"].iloc[-1])

        trigger = prior_close + self.trigger_atr_mult * a
        limit = trigger + self.limit_atr_mult * a
        stop = trigger - self.stop_atr_mult * a
        if stop >= trigger or limit < trigger:
            return None
        return LevelSet(
            session_date=armed_for, side=Side.LONG,
            entry_trigger=round(trigger, 4), entry_limit=round(limit, 4),
            initial_stop=round(stop, 4), target=None,
            style=EntryStyle.BREAKOUT,
            meta={"generator": self.name, "atr60": round(a, 4),
                  "prior_close": prior_close, "is_candidate": True},
        )

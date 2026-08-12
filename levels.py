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
from dataclasses import replace
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


_TR_CACHE: dict = {}
_ATR_CACHE: dict = {}


def true_range_60m(session: pd.DataFrame, prev_close: float | None) -> np.ndarray:
    """Per-bar true range. Stub bars are EXCLUDED by the caller, not here.

    Memoised. atr60 is called once per session with a sliding 12-session window,
    so without a cache each session's true range is recomputed about twelve times
    - and the recomputation is pure pandas column extraction, which dominated the
    profile. The key includes prev_close because the first bar's true range
    depends on the preceding session's close.
    """
    if len(session) == 0:
        return np.empty(0)
    key = (session["ts"].iloc[-1], len(session),
           None if prev_close is None else round(prev_close, 6))
    hit = _TR_CACHE.get(key)
    if hit is not None:
        return hit
    high = session["high"].to_numpy()
    low = session["low"].to_numpy()
    close = np.concatenate(
        [[prev_close if prev_close is not None else session["close"].iloc[0]],
         session["close"].to_numpy()[:-1]]
    )
    tr = np.maximum.reduce([high - low, np.abs(high - close), np.abs(low - close)])
    if len(_TR_CACHE) > 40_000:      # bounded; this is a per-process scratch cache
        _TR_CACHE.clear()
    _TR_CACHE[key] = tr
    return tr


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
    # Only the tail can affect the result: the mean is taken over the last
    # `lookback_bars` true ranges. Iterating the whole history to then discard
    # all but 42 values is what made candidate D ten times slower than the rule
    # it gates - it passes a 284-session window, and every session of it was
    # being walked on every call.
    #
    # 20 sessions is a safe floor. With stubs excluded a regular session
    # contributes 6 bars and an early close 3, so 20 sessions yield at least 60
    # usable bars against a 42-bar requirement. Bars beyond the tail cannot enter
    # trs[-lookback_bars:], so the result is unchanged.
    needed = max(20, lookback_bars // 3 + 4)
    if len(history) > needed:
        history = history[-needed:]

    if history:
        _tail = history[-1]
        _k = (
            _tail["ts"].iloc[-1] if len(_tail) else None,
            len(history), lookback_bars, exclude_stubs,
        )
        _hit = _ATR_CACHE.get(_k)
        if _hit is not None:
            return _hit
    else:
        _k = None

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
    out = float(np.mean(trs[-lookback_bars:]))
    if _k is not None:
        if len(_ATR_CACHE) > 40_000:
            _ATR_CACHE.clear()
        _ATR_CACHE[_k] = out
    return out


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


class VolatilityRegimeGate:
    """CANDIDATE D: candidate A, gated off when realised volatility is extreme.

    PRE-REGISTERED, SURFER-DES-002 §4.1, 2026-08-12. The parameters below are
    FIXED and may not be tuned. They were chosen before any performance was
    measured, and the reason for fixing a weakly-justified value in advance is
    that choosing one afterwards is not a choice, it is a fit.

        measure   : atr60() with stubs excluded
        window    : trailing 252 sessions
        block      : percentile >= 80 -> emit no entry levels

    The gate governs ENTRY ONLY. An open position is untouched: exits stay with
    the structural level and the fixed stop. Gating exits as well would mean the
    filter could strand a position in exactly the conditions it was built to
    avoid.

    KNOWN RISK - same failure mode as MATILDA's removed VIX hard gate. A
    volatility gate blocks entry during the high-volatility early phase of a
    recovery, which is where much of the return lives. If D underperforms A,
    that is consistent with the earlier finding and should be recorded as such
    rather than explained away.

    KNOWN RISK - SOXL. In MATILDA's signal history SOXL's volatility percentile
    sits mostly in the 90s, so an 80 threshold may block nearly all SOXL
    trading. Per the pre-registration that outcome is a RESULT ("this filter is
    unsuited to SOXL"), not a reason to move the threshold.
    """

    name = "CANDIDATE_D_vol_regime_gated_breakout"
    # 252 percentile readings + 12 sessions for the ATR window + 20 warm-up.
    # An earlier value of 264 was too small: the 20-session warm-up consumed part
    # of it, so the rolling window was about 244 sessions, not the 252 that
    # SURFER-DES-002 §4.1 specifies. The gate was quietly running to a different
    # spec than the document. Found before any real measurement.
    lookback_sessions = 284

    PERCENTILE_WINDOW = 252
    BLOCK_AT_PERCENTILE = 80.0

    def __init__(self, base: "PlaceholderBreakout | None" = None) -> None:
        self.base = base or PlaceholderBreakout()
        # Memo of one ATR reading per session, keyed by that session's final bar
        # timestamp. Without it the percentile recomputed the whole trailing
        # series on every step, which is quadratic in session count - the same
        # mistake already made once in diagnostics.py.
        # Insertion-ordered store of one ATR reading per session, in session
        # order. Python dicts preserve insertion order, which is what makes the
        # trailing-252 slice below correct.
        self._atr_series: dict = {}
        self._ordered: list = []       # (session ts, atr), kept sorted by ts

    def _remember(self, key, value: float) -> None:
        import bisect

        if key in self._atr_series:
            return
        self._atr_series[key] = value
        bisect.insort(self._ordered, (key, value))

    def percentile(self, history: list[pd.DataFrame]) -> float | None:
        """Where today's ATR sits against its own trailing distribution.

        Incremental: only the newest session's ATR is computed, then the trailing
        252 readings are taken from an insertion-ordered store. The previous
        version rebuilt the whole series on every call, which made this candidate
        19s against 1.5s for the others - twelve times the cost of the rule it
        gates, all of it recomputation.

        Readings are keyed by the session's final bar timestamp, so a session
        appearing in overlapping windows is computed exactly once.
        """
        if len(history) < 30:
            return None

        # Ensure a reading exists for EVERY session in the trailing window, not
        # merely for the newest one.
        #
        # BUG THIS FIXES (found 2026-08-12, after §11 was measured): callers only
        # ask the gate for levels when FLAT - run_backtest and replay_position both
        # skip the generator while a position is held. The store therefore
        # developed holes wherever the engine happened to be holding, and
        # "trailing 252 sessions" silently became "the last 252 sessions in which
        # this engine was flat", reaching much further back and mixing in older
        # volatility.
        #
        # That made the gate's input depend on the engine's own trading history -
        # self-referential, and invisible in the output. Backfilling here makes
        # the gate correct regardless of how the caller drives it, which is the
        # right place for the guarantee: a rule should not depend on being invoked
        # on a particular schedule.
        # Walk backwards and stop at the first session already known. Sessions
        # arrive in order, so everything older than that is present too - scanning
        # the whole window every call cost 5x for nothing.
        missing: list[int] = []
        for k in range(len(history), 0, -1):
            t = history[k - 1]
            if len(t) == 0:
                continue
            if t["ts"].iloc[-1] in self._atr_series:
                break
            missing.append(k)
        for k in reversed(missing):
            a = atr60(history[max(0, k - 12):k])
            if a is not None:
                self._remember(history[k - 1]["ts"].iloc[-1], a)

        tail = history[-1]
        if len(tail) == 0:
            return None
        key = tail["ts"].iloc[-1]
        if key not in self._atr_series or len(self._atr_series) < 30:
            return None

        vals = [v for _, v in self._ordered]
        if len(vals) < 30:
            return None
        window = vals[-self.PERCENTILE_WINDOW:]
        current = self._atr_series[key]
        return 100.0 * float(np.mean([v <= current for v in window]))

    def __call__(self, history, armed_for):
        pct = self.percentile(history)
        if pct is None:
            return None
        if pct >= self.BLOCK_AT_PERCENTILE:
            return None          # gated: no levels emitted, so no entry armed
        lv = self.base(history, armed_for)
        if lv is None:
            return None
        return replace(
            lv,
            meta={**lv.meta, "generator": self.name, "atr_percentile": round(pct, 1),
                  "gate": f"blocked at >= {self.BLOCK_AT_PERCENTILE}"},
        )

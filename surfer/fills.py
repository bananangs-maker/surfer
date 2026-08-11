"""Fill adjudication: given levels and the bars that followed, what filled?

This module, not the level generator, is where 60-minute data is actually
required, and it is where bias hides. Three rules are hard-coded so they cannot
drift:

RULE 1 - ADVERSE INTRA-BAR ORDERING.
    When two actionable levels both fall inside one bar's [low, high], the bar
    cannot say which was touched first. We always resolve to the ordering that
    produces the worst outcome, and we FLAG the bar. The flag matters more than
    the resolution: a run with a high ambiguity rate is uninterpretable
    regardless of what it reports.

RULE 2 - ASYMMETRIC ORDER TYPES.
    Entry is a stop-LIMIT: if price gaps above entry_limit and never trades back
    down into the band, there is no fill. A missed trade is not a loss.
    The protective stop is a plain STOP: it fills on the gap, at the gap price,
    however bad. An unfilled stop is just an unhedged 3x position.

RULE 3 - GAP FILLS ARE TAGGED, NOT AVERAGED.
    Every fill records whether it happened at a gap. Averaging gap slippage into
    an overall mean hides the tail, and the tail is the thing that ends accounts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

import pandas as pd

from .schema import LevelSet, Side


class Kind(Enum):
    ENTRY = "entry"
    STOP = "stop"
    TARGET = "target"


class NoFill(Enum):
    NO_TRIGGER = "no_trigger"                # trigger never touched
    GAP_THROUGH_LIMIT = "gap_through_limit"  # gapped past the limit band


@dataclass(frozen=True)
class Fill:
    kind: Kind
    ts: pd.Timestamp
    price: float
    at_gap: bool
    ambiguous: bool


@dataclass
class SessionOutcome:
    session_date: date
    levels: LevelSet | None = None
    entry: Fill | None = None
    exit: Fill | None = None
    no_fill_reason: NoFill | None = None
    open_at_session_end: bool = False
    ambiguous_bars: int = 0
    bars_examined: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return self.ambiguous_bars > 0

    @property
    def touched_gap(self) -> bool:
        return bool((self.entry and self.entry.at_gap) or (self.exit and self.exit.at_gap))


def _count_ambiguity(bar: pd.Series, levels: LevelSet, in_position: bool) -> int:
    """How many actionable levels lie inside this bar's range."""
    lo, hi = float(bar["low"]), float(bar["high"])
    prices = levels.actionable_prices(in_position=in_position)
    return sum(1 for p in prices.values() if lo <= p <= hi)


def adjudicate_session(
    levels: LevelSet | None,
    bars: pd.DataFrame,
) -> SessionOutcome:
    """Walk one session's bars against one armed LevelSet.

    `bars` may be 60-minute bars or a single aggregated daily bar. That is the
    point: the same adjudicator is run at both resolutions and the ambiguity
    counts are compared. See diagnostics.py.
    """
    sd = levels.session_date if levels is not None else (
        bars["session_date"].iloc[0] if len(bars) else None
    )
    out = SessionOutcome(session_date=sd, levels=levels, bars_examined=len(bars))
    if levels is None or len(bars) == 0:
        return out
    if levels.side is not Side.LONG:
        raise NotImplementedError("long-only")

    in_position = False
    triggered_ever = False

    for _, bar in bars.iterrows():
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        n_amb = _count_ambiguity(bar, levels, in_position)
        bar_ambiguous = n_amb >= 2
        if bar_ambiguous:
            out.ambiguous_bars += 1

        if not in_position:
            if h < levels.entry_trigger:
                continue  # trigger untouched this bar
            triggered_ever = True

            # --- entry: stop-limit semantics -----------------------------
            if o >= levels.entry_trigger:
                # already through the trigger at the open: gap entry
                if o <= levels.entry_limit:
                    fill_px, at_gap = o, True
                elif l <= levels.entry_limit:
                    # gapped past the band, traded back into it
                    fill_px, at_gap = levels.entry_limit, True
                else:
                    out.notes.append(
                        f"{bar['ts']}: gapped through limit "
                        f"(open {o:.4f} > limit {levels.entry_limit:.4f}), no fill"
                    )
                    continue
            else:
                fill_px, at_gap = levels.entry_trigger, False

            out.entry = Fill(Kind.ENTRY, bar["ts"], fill_px, at_gap, bar_ambiguous)
            in_position = True

            # --- RULE 1: same-bar stop after entry -----------------------
            # Adverse ordering: assume we were filled and then stopped out.
            if l <= levels.initial_stop:
                exit_px = min(o, levels.initial_stop) if o <= levels.initial_stop else levels.initial_stop
                out.exit = Fill(
                    Kind.STOP, bar["ts"], exit_px,
                    at_gap=(o <= levels.initial_stop), ambiguous=True,
                )
                out.notes.append(
                    f"{bar['ts']}: entry and stop both inside bar -> adverse "
                    "ordering applied (filled, then stopped)"
                )
                if not bar_ambiguous:
                    out.ambiguous_bars += 1
                return out
            continue

        # --- in position ------------------------------------------------
        stop_hit = l <= levels.initial_stop
        tgt_hit = levels.target is not None and h >= levels.target

        if stop_hit and tgt_hit:
            # RULE 1 again: adverse ordering is stop first.
            exit_px = min(o, levels.initial_stop) if o <= levels.initial_stop else levels.initial_stop
            out.exit = Fill(Kind.STOP, bar["ts"], exit_px, o <= levels.initial_stop, True)
            out.notes.append(f"{bar['ts']}: stop and target both inside bar -> stop assumed")
            return out
        if stop_hit:
            # RULE 2: plain stop fills through the gap, at the gap price.
            at_gap = o <= levels.initial_stop
            exit_px = o if at_gap else levels.initial_stop
            out.exit = Fill(Kind.STOP, bar["ts"], exit_px, at_gap, bar_ambiguous)
            if at_gap:
                out.notes.append(
                    f"{bar['ts']}: stop gapped through "
                    f"({o:.4f} vs stop {levels.initial_stop:.4f})"
                )
            return out
        if tgt_hit:
            at_gap = o >= levels.target
            exit_px = o if at_gap else levels.target
            out.exit = Fill(Kind.TARGET, bar["ts"], exit_px, at_gap, bar_ambiguous)
            return out

    if in_position:
        out.open_at_session_end = True
        out.notes.append("position still open at session end (swing hold)")
    elif not triggered_ever:
        out.no_fill_reason = NoFill.NO_TRIGGER
    else:
        out.no_fill_reason = NoFill.GAP_THROUGH_LIMIT
    return out

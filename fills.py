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

from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum

import pandas as pd

from schema import EntryStyle, LevelSet, Side


class Kind(Enum):
    ENTRY = "entry"
    STOP = "stop"                # fixed disaster stop, set at entry
    STRUCTURAL = "structural"    # recomputed structure break, normal exit path
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


@dataclass(frozen=True)
class Position:
    """An open holding, carried between sessions.

    The stop is FIXED at entry and never recomputed - the decision recorded for
    SURFER v0. Any trailing logic must therefore live in a new field, not in a
    mutation of this one, so that a change of policy is visible in the type
    rather than hidden in an update path.
    """

    entry_ts: pd.Timestamp
    entry_price: float
    stop: float
    target: float | None
    entry_at_gap: bool
    entry_ambiguous: bool
    sessions_held: int = 0
    # Recomputed each held session by an exit rule. NEVER lowers the protection
    # floor: effective_stop takes the higher of the two, so the fixed stop is a
    # bottom the structural layer can rise above but never dig below.
    structural_exit: float | None = None

    @property
    def effective_stop(self) -> float:
        if self.structural_exit is None:
            return self.stop
        return max(self.stop, self.structural_exit)

    @property
    def exit_is_structural(self) -> bool:
        return (
            self.structural_exit is not None
            and self.structural_exit > self.stop
        )


@dataclass
class SessionOutcome:
    session_date: date
    levels: LevelSet | None = None
    entry: Fill | None = None
    exit: Fill | None = None
    no_fill_reason: NoFill | None = None
    open_at_session_end: bool = False
    carried_in: bool = False
    position_out: "Position | None" = None
    ambiguous_bars: int = 0
    bars_examined: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return self.ambiguous_bars > 0

    @property
    def touched_gap(self) -> bool:
        return bool((self.entry and self.entry.at_gap) or (self.exit and self.exit.at_gap))



def _walk_hold(
    out: SessionOutcome, pos: Position, bars: pd.DataFrame
) -> SessionOutcome:
    """A session spent holding. Only the fixed stop (and target) are live.

    No entry is armed: the position is already on, and re-arming would be
    pyramiding, which SURFER v0 forbids. RULE 2 still governs the exit - a plain
    stop fills through the gap at the gap price, however bad.
    """
    level = pos.effective_stop
    kind = Kind.STRUCTURAL if pos.exit_is_structural else Kind.STOP

    O, Hi, Lo, TS = _arrays(bars)
    for i in range(len(bars)):
        # float()/pd.Timestamp() keep numpy scalars out of Fill. Without this,
        # at_gap became np.False_ - equal to False but not identical to it, which
        # silently breaks `is False` checks downstream.
        o, h, l = float(O[i]), float(Hi[i]), float(Lo[i])
        ts = pd.Timestamp(TS[i])

        stop_hit = l <= level
        tgt_hit = pos.target is not None and h >= pos.target
        if stop_hit and tgt_hit:
            out.ambiguous_bars += 1
            at_gap = o <= level
            out.exit = Fill(kind, ts, o if at_gap else level, at_gap, True)
            out.notes.append(
                f"{ts}: exit level and target both inside bar -> exit assumed"
            )
            return out
        if stop_hit:
            at_gap = o <= level
            out.exit = Fill(kind, ts, o if at_gap else level, at_gap, False)
            if at_gap:
                out.notes.append(
                    f"{ts}: held position gapped through {kind.value} "
                    f"({o:.4f} vs {level:.4f})"
                )
            return out
        if tgt_hit:
            at_gap = o >= pos.target
            out.exit = Fill(
                Kind.TARGET, ts, o if at_gap else pos.target, at_gap, False
            )
            return out

    out.open_at_session_end = True
    out.position_out = replace(pos, sessions_held=pos.sessions_held + 1)
    out.notes.append(f"held through session {out.session_date} (no exit touched)")
    return out


def _count_ambiguity(lo: float, hi: float, levels: LevelSet, in_position: bool) -> int:
    """How many actionable levels lie inside this bar's range."""
    prices = levels.actionable_prices(in_position=in_position)
    return sum(1 for p in prices.values() if lo <= p <= hi)


def _arrays(bars: pd.DataFrame):
    """Column arrays once per session instead of a Series per bar.

    iterrows() builds a fresh pandas Series for every bar, and with four
    candidates over 730 sessions that construction cost dominated the run. The
    loop bodies below are unchanged in logic - only the access pattern differs.
    """
    return (
        bars["open"].to_numpy(dtype=float),
        bars["high"].to_numpy(dtype=float),
        bars["low"].to_numpy(dtype=float),
        bars["ts"].to_numpy(),
    )


def adjudicate_session(
    levels: LevelSet | None,
    bars: pd.DataFrame,
    carried: Position | None = None,
) -> SessionOutcome:
    """Walk one session's bars against one armed LevelSet.

    `bars` may be 60-minute bars or a single aggregated daily bar. That is the
    point: the same adjudicator is run at both resolutions and the ambiguity
    counts are compared. See diagnostics.py.

    `carried` is an open holding from a previous session. When present the
    session is a HOLD: no entry is armed, only the position's own fixed stop is
    watched. Without this the adjudicator reset to flat every session, leaving
    the majority of entries with no recorded exit - a swing system measured as
    if it were a day-trading system.
    """
    sd = levels.session_date if levels is not None else (
        bars["session_date"].iloc[0] if len(bars) else None
    )
    out = SessionOutcome(
        session_date=sd, levels=levels, bars_examined=len(bars),
        carried_in=carried is not None,
    )
    if len(bars) == 0:
        out.position_out = carried
        return out

    if carried is not None:
        return _walk_hold(out, carried, bars)

    if levels is None:
        return out
    if levels.side is not Side.LONG:
        raise NotImplementedError("long-only")

    in_position = False
    triggered_ever = False

    O, Hi, Lo, TS = _arrays(bars)
    for i in range(len(bars)):
        # See _walk_hold: numpy scalars must not escape into Fill.
        o, h, l = float(O[i]), float(Hi[i]), float(Lo[i])
        ts = pd.Timestamp(TS[i])
        n_amb = _count_ambiguity(l, h, levels, in_position)
        bar_ambiguous = n_amb >= 2
        if bar_ambiguous:
            out.ambiguous_bars += 1

        if not in_position:
            if levels.style is EntryStyle.BREAKOUT:
                if h < levels.entry_trigger:
                    continue                      # trigger untouched this bar
                triggered_ever = True
                if o >= levels.entry_trigger:
                    # already through the trigger at the open: gap entry
                    if o <= levels.entry_limit:
                        fill_px, at_gap = o, True
                    elif l <= levels.entry_limit:
                        fill_px, at_gap = levels.entry_limit, True   # gapped past, came back
                    else:
                        out.notes.append(
                            f"{ts}: gapped through ceiling "
                            f"(open {o:.4f} > limit {levels.entry_limit:.4f}), no fill"
                        )
                        continue
                else:
                    fill_px, at_gap = levels.entry_trigger, False
            else:
                # PULLBACK: a buy limit below the market. entry_limit is a FLOOR.
                if l > levels.entry_trigger:
                    continue                      # price never came down to us
                triggered_ever = True
                if o <= levels.entry_trigger:
                    # opened at or below the limit: filled at the open, which is
                    # nominally a better price but a worse situation
                    if o >= levels.entry_limit:
                        fill_px, at_gap = o, True
                    else:
                        out.notes.append(
                            f"{ts}: gapped through floor "
                            f"(open {o:.4f} < limit {levels.entry_limit:.4f}), "
                            "structure broken, no fill"
                        )
                        continue
                else:
                    fill_px, at_gap = levels.entry_trigger, False

            out.entry = Fill(Kind.ENTRY, ts, fill_px, at_gap, bar_ambiguous)
            in_position = True
            live = Position(
                entry_ts=ts, entry_price=fill_px, stop=levels.initial_stop,
                target=levels.target, entry_at_gap=at_gap,
                entry_ambiguous=bar_ambiguous,
            )

            # --- RULE 1: same-bar stop after entry -----------------------
            # Adverse ordering: assume we were filled and then stopped out.
            if l <= levels.initial_stop:
                exit_px = (
                    min(o, levels.initial_stop)
                    if o <= levels.initial_stop else levels.initial_stop
                )
                out.exit = Fill(
                    Kind.STOP, ts, exit_px,
                    at_gap=(o <= levels.initial_stop), ambiguous=True,
                )
                out.notes.append(
                    f"{ts}: entry and stop both inside bar -> adverse "
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
            out.exit = Fill(Kind.STOP, ts, exit_px, o <= levels.initial_stop, True)
            out.notes.append(f"{ts}: stop and target both inside bar -> stop assumed")
            return out
        if stop_hit:
            # RULE 2: plain stop fills through the gap, at the gap price.
            at_gap = o <= levels.initial_stop
            exit_px = o if at_gap else levels.initial_stop
            out.exit = Fill(Kind.STOP, ts, exit_px, at_gap, bar_ambiguous)
            if at_gap:
                out.notes.append(
                    f"{ts}: stop gapped through "
                    f"({o:.4f} vs stop {levels.initial_stop:.4f})"
                )
            return out
        if tgt_hit:
            at_gap = o >= levels.target
            exit_px = o if at_gap else levels.target
            out.exit = Fill(Kind.TARGET, ts, exit_px, at_gap, bar_ambiguous)
            return out

    if in_position:
        out.open_at_session_end = True
        out.position_out = live
        out.notes.append("position still open at session end (swing hold)")
    elif not triggered_ever:
        out.no_fill_reason = NoFill.NO_TRIGGER
    else:
        out.no_fill_reason = NoFill.GAP_THROUGH_LIMIT
    return out

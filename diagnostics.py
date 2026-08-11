"""The engine's first deliverable: a diagnostic, not a performance number.

WHY THIS IS THE FIRST OUTPUT
----------------------------
The ambiguity rate is a STRUCTURAL property of bar width versus level spacing.
It does not depend on the sample being a representative market regime, so a
rising-market window can answer it honestly - unlike any return statistic
measured on the same window.

It is also the decision the money hangs on. If moving from daily OHLC to
60-minute bars barely reduces the share of sessions whose outcome is
order-dependent, then paid intraday history buys nothing and should not be
bought. If it reduces it a lot, the purchase has a written, numeric
justification instead of a hunch.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from aggregate import to_daily
from fills import SessionOutcome, adjudicate_session
from levels import LevelGenerator
from schema import Dataset


@dataclass
class ResolutionResult:
    label: str
    sessions_armed: int
    sessions_entered: int
    ambiguous_sessions: int
    ambiguous_bars: int
    gap_fills: int
    no_fill_no_trigger: int
    no_fill_gap_through: int
    open_at_session_end: int

    @property
    def ambiguity_rate(self) -> float:
        return self.ambiguous_sessions / self.sessions_armed if self.sessions_armed else 0.0

    @property
    def entry_rate(self) -> float:
        return self.sessions_entered / self.sessions_armed if self.sessions_armed else 0.0


def _run(label: str, per_session: dict, generator: LevelGenerator,
         history_source: dict, sessions: list) -> tuple[ResolutionResult, list[SessionOutcome]]:
    outcomes: list[SessionOutcome] = []
    # Slice to the generator's declared window. Passing the whole prefix made a
    # run quadratic in session count - 400 sessions took ~23s here and minutes
    # on a 0.1-CPU instance, which read as a hung page. Results are unchanged:
    # the generator never looked past this window in the first place.
    window = getattr(generator, "lookback_sessions", 30)
    for i, sd in enumerate(sessions):
        lo = max(0, i - window)
        history = [history_source[s] for s in sessions[lo:i]]
        levels = generator(history, sd)
        if levels is None:
            continue
        outcomes.append(adjudicate_session(levels, per_session[sd]))

    res = ResolutionResult(
        label=label,
        sessions_armed=len(outcomes),
        sessions_entered=sum(1 for o in outcomes if o.entry is not None),
        ambiguous_sessions=sum(1 for o in outcomes if o.ambiguous),
        ambiguous_bars=sum(o.ambiguous_bars for o in outcomes),
        gap_fills=sum(1 for o in outcomes if o.touched_gap),
        no_fill_no_trigger=sum(
            1 for o in outcomes
            if o.no_fill_reason is not None and o.no_fill_reason.value == "no_trigger"
        ),
        no_fill_gap_through=sum(
            1 for o in outcomes
            if o.no_fill_reason is not None and o.no_fill_reason.value == "gap_through_limit"
        ),
        open_at_session_end=sum(1 for o in outcomes if o.open_at_session_end),
    )
    return res, outcomes


def resolution_comparison(
    ds: Dataset,
    generator: LevelGenerator,
) -> tuple[ResolutionResult, ResolutionResult]:
    """Run the identical generator and adjudicator at 60m and daily resolution.

    The level generator always sees 60-minute history in both runs. Only the
    bars used for FILL ADJUDICATION change. That isolates the variable: this
    measures resolution's effect on execution modelling, not on signal quality.
    """
    sessions = ds.sessions
    # One groupby, not one full-frame mask per session. The dict-comprehension
    # form rescanned every row once per session, which is quadratic again.
    intraday = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    daily_all = to_daily(ds.bars)
    daily = {
        d: g.reset_index(drop=True)
        for d, g in daily_all.groupby("session_date", sort=True)
    }

    r60, _ = _run("60-minute", intraday, generator, intraday, sessions)
    r1d, _ = _run("daily OHLC", daily, generator, intraday, sessions)
    return r60, r1d


def render_comparison(r60: ResolutionResult, r1d: ResolutionResult, ds: Dataset) -> str:
    def row(name: str, a, b, fmt="{}") -> str:
        return f"  {name:<26} {fmt.format(a):>14} {fmt.format(b):>14}"

    lines = [
        "AMBIGUITY DIAGNOSTIC (SURFER-DIAG-001)",
        f"  dataset: {ds.describe()}",
        "",
        f"  {'':<26} {r1d.label:>14} {r60.label:>14}",
        row("sessions armed", r1d.sessions_armed, r60.sessions_armed),
        row("sessions entered", r1d.sessions_entered, r60.sessions_entered),
        row("entry rate", r1d.entry_rate, r60.entry_rate, "{:.1%}"),
        row("AMBIGUOUS sessions", r1d.ambiguous_sessions, r60.ambiguous_sessions),
        row("ambiguity rate", r1d.ambiguity_rate, r60.ambiguity_rate, "{:.1%}"),
        row("gap-touched fills", r1d.gap_fills, r60.gap_fills),
        row("no fill: no trigger", r1d.no_fill_no_trigger, r60.no_fill_no_trigger),
        row("no fill: gap thru limit", r1d.no_fill_gap_through, r60.no_fill_gap_through),
        row("open at session end", r1d.open_at_session_end, r60.open_at_session_end),
    ]
    if r1d.ambiguity_rate > 0:
        red = 1 - (r60.ambiguity_rate / r1d.ambiguity_rate)
        lines += [
            "",
            f"  ambiguity reduction from 60m: {red:.1%}",
            "",
            "  READ THIS AS: the share of sessions whose outcome depends on",
            "  unobservable intra-bar ordering. It is not a performance number",
            "  and must not be reported as one.",
        ]
    return "\n".join(lines)


class QuarantineError(RuntimeError):
    pass


def compute_performance(ds: Dataset, outcomes, acknowledge_quarantine: bool = False):
    """Deliberately obstructed.

    A quarantined dataset (see loaders) covers a window that cannot answer
    whether this system survives a downtrend. Seeing a return figure from it
    once is enough to anchor expectations, after which honest numbers from 2022
    data will feel like a regression rather than a first measurement. Same
    reason MATILDA pre-registers.
    """
    if ds.quarantined and not acknowledge_quarantine:
        raise QuarantineError(
            f"{ds.symbol}/{ds.source} is quarantined: {ds.quarantine_reason}\n"
            "Performance statistics are blocked. Use the ambiguity diagnostic.\n"
            "Override with acknowledge_quarantine=True only if you are prepared "
            "to record in writing that the figure is not evidence."
        )
    raise NotImplementedError(
        "Performance accounting is intentionally unimplemented until a real "
        "level generator replaces PlaceholderBreakout."
    )

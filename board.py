"""Daily signal board: what the engine arms for the next session.

Unified on SURFER-ENGINE-v1.0.0 (candidate D) per SURFER-DES-002 §12.5. The
four-candidate view it replaced existed only because no rule had been chosen; with
one engine there is a single signal, which is what can actually be operated,
paper traded and falsified.

The engine is NOT validated - §2.2R has not run, because the out-of-sample window
(pre-2024) is not in the free feed. "SURFER says X" means "an unvalidated rule
says X" until then, and the page says so.

Once a day, before the open — the cadence SURFER was designed around. No live
quoting: the system does not react to intraday bars, and a screen that updates
during the session would put the decision back in front of a person at 03:00 KST,
which is the thing the resting-order design exists to avoid.

All four candidates are shown side by side and none is marked as chosen.
Selection belongs to SURFER-DES-002 §3.3 and cannot happen here, because the
selection window is 2019 and earlier. Displaying one candidate's levels daily
would let an unvalidated rule leak into live use by familiarity alone.

The one correctness trap this module exists to handle: a partially completed
final session. If the US market is open right now, the feed's last session is a
stub, and a generator handed it as history would derive today's levels from bars
that have not closed. That is lookahead, and it is silent - the numbers look
entirely ordinary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from engine import Engine, Signal, State
from levels import (PlaceholderBreakout, PriorCloseVolatilityBreakout,
                    PullbackToPriorLow, VolatilityRegimeGate)
from schema import NY, EntryStyle, LevelSet

CANDIDATES: dict[str, tuple[str, type]] = {
    "A": ("전 세션 고가 돌파", PlaceholderBreakout),
    "B": ("전 세션 저가 되돌림", PullbackToPriorLow),
    "C": ("전 종가 변동성 돌파", PriorCloseVolatilityBreakout),
    "D": ("A + 변동성 게이트", VolatilityRegimeGate),
}

# A regular session yields 7 sixty-minute bars; an early close yields 4.
FULL_SESSION_BARS = 7
EARLY_SESSION_BARS = 4


@dataclass
class Row:
    key: str
    label: str
    style: str | None
    levels: LevelSet | None
    note: str
    extra: dict


@dataclass
class EngineBoard:
    """Single-engine view. Rows are kept for the candidate comparison page."""

    signal: Signal
    engine_version: str
    engine_basis: str
    last_complete: date
    dropped_partial: date | None
    partial_bar_count: int | None
    sessions_available: int
    staleness_hours: float
    stale: bool
    symbol: str
    position_replayed: bool


@dataclass
class Board:
    armed_for: str
    last_complete: date
    dropped_partial: date | None
    partial_bar_count: int | None
    sessions_available: int
    staleness_hours: float
    stale: bool
    rows: list[Row]
    symbol: str


def _complete_sessions(ds) -> tuple[list[date], date | None, int | None]:
    """Trim a partial final session.

    Bar count is the test rather than wall-clock time: it is what the data
    actually shows, and it does not depend on the server's clock or on knowing
    the exchange holiday calendar.
    """
    sessions = ds.sessions
    if not sessions:
        return [], None, None
    counts = ds.bars.groupby("session_date").size()
    last = sessions[-1]
    n = int(counts.loc[last])
    if n not in (FULL_SESSION_BARS, EARLY_SESSION_BARS):
        return sessions[:-1], last, n
    return sessions, None, None


def build(ds, symbol: str) -> Board:
    sessions, dropped, dropped_n = _complete_sessions(ds)
    if not sessions:
        raise ValueError("no complete sessions in the feed")

    by = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    last = sessions[-1]

    # Hours since the final bar of the last complete session.
    last_ts = by[last]["ts"].iloc[-1]
    now = datetime.now(timezone.utc)
    hours = (now - last_ts.to_pydatetime()).total_seconds() / 3600.0

    rows: list[Row] = []
    for key, (label, G) in CANDIDATES.items():
        gen = G()
        window = getattr(gen, "lookback_sessions", 30)
        history = [by[s] for s in sessions[max(0, len(sessions) - window):]]
        extra: dict = {}
        note = ""

        if key == "D":
            pct = gen.percentile(history)
            extra["percentile"] = pct
            if pct is None:
                note = "백분위 계산에 필요한 과거가 부족합니다"
            elif pct >= gen.BLOCK_AT_PERCENTILE:
                note = (
                    f"게이트 차단 — ATR 백분위 {pct:.0f} "
                    f"(≥{gen.BLOCK_AT_PERCENTILE:.0f})"
                )

        lv = gen(history, armed_for=last + timedelta(days=1))
        if lv is None and not note:
            note = "레벨 산출 조건 미충족 (과거 부족 또는 레벨 관계 부적합)"
        if lv is not None:
            extra["atr60"] = lv.meta.get("atr60")
            extra["r_multiple"] = round(
                abs(lv.entry_trigger - lv.initial_stop), 4
            )
        rows.append(Row(
            key=key, label=label,
            style=(lv.style.value if lv else None),
            levels=lv, note=note, extra=extra,
        ))

    return Board(
        armed_for="다음 정규장 세션",
        last_complete=last,
        dropped_partial=dropped,
        partial_bar_count=dropped_n,
        sessions_available=len(sessions),
        staleness_hours=hours,
        stale=hours > 30,
        rows=rows,
        symbol=symbol,
    )


def order_worksheet(board: Board) -> str:
    """A paste-able record for paper execution.

    SURFER-DES-002 §8 needs the gap between the specified level and the realised
    fill, plus the stop-limit fill rate. Those are measurable now, without any
    purchased history, and a failure there stops the data purchase. The columns
    left blank are the ones only a broker can fill in.
    """
    head = ("symbol,candidate,style,trigger,limit,stop,"
            "armed_for_session,actual_fill,fill_time,filled_yn,notes")
    lines = [head]
    for r in board.rows:
        if r.levels is None:
            continue
        lines.append(
            f"{board.symbol},{r.key},{r.levels.style.value},"
            f"{r.levels.entry_trigger},{r.levels.entry_limit},"
            f"{r.levels.initial_stop},{board.last_complete}+1,,,,"
        )
    return "\n".join(lines)


def build_engine(ds, symbol: str) -> EngineBoard:
    """Today's single signal, with the open position reconstructed by replay.

    The position is replayed from bars rather than stored. A stored position can
    disagree with the price data after a missed run or a restart; a replayed one
    cannot.
    """
    from engine import replay_position

    sessions, dropped, dropped_n = _complete_sessions(ds)
    if not sessions:
        raise ValueError("no complete sessions in the feed")

    by = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    last = sessions[-1]
    last_ts = by[last]["ts"].iloc[-1]
    now = datetime.now(timezone.utc)
    hours = (now - last_ts.to_pydatetime()).total_seconds() / 3600.0

    eng = Engine()
    pos = replay_position(ds, eng, upto=last)
    history = [by[s] for s in sessions[max(0, len(sessions) - eng.lookback_sessions):]]
    sig = eng.signal(history, armed_for=last + timedelta(days=1), position=pos)

    return EngineBoard(
        signal=sig, engine_version=eng.version, engine_basis=eng.basis,
        last_complete=last, dropped_partial=dropped, partial_bar_count=dropped_n,
        sessions_available=len(sessions), staleness_hours=hours,
        stale=hours > 30, symbol=symbol, position_replayed=pos is not None,
    )


def engine_worksheet(bd: EngineBoard) -> str:
    """Paper-execution record for the DES-002 §8 execution falsifier."""
    head = ("symbol,engine,state,trigger,limit,stop,effective_exit,"
            "armed_for_session,actual_fill,fill_time,filled_yn,notes")
    sig = bd.signal
    lv = sig.levels
    return "\n".join([
        head,
        f"{bd.symbol},{bd.engine_version},{sig.state.value},"
        f"{lv.entry_trigger if lv else ''},{lv.entry_limit if lv else ''},"
        f"{lv.initial_stop if lv else (sig.position.stop if sig.position else '')},"
        f"{sig.effective_exit or ''},{bd.last_complete}+1,,,,",
    ])

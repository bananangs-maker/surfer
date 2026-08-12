"""SURFER engine v1 — one rule, one signal.

CONSTITUTION
------------
    entry     candidate D: prior-session-high breakout, gated off when the
              instrument's own 60-minute ATR sits at or above its 252-session
              80th percentile
    exit      structural: prior session low - 0.25 ATR, ratcheted (never lowers)
    stop      fixed at entry, never moves; the effective exit is the higher of
              the two, so the fixed stop is a floor and structure is the normal
              path
    cadence   once daily, before the open, as resting orders

WHY D AND NOT A, B OR C
-----------------------
Measured on TQQQ, 730 sessions (2024-08 to 2026-08), SURFER-DES-002 §11:
A, B and C failed both criteria; D was the only candidate to pass the drawdown
condition, halving A's drawdown (-58.4% to -28.7%) and turning its CAGR positive
(-13.3% to +4.0%) on an identical entry rule. It failed the Calmar condition, but
that condition has since been shown to be unsatisfiable in a rising window by any
rule that reduces exposure (§11.1) — the criterion was faulty, not the candidate.

PROVISIONAL
-----------
This engine is NOT validated. The window that selected it contains no decline and
no recovery, and a volatility gate's known failure mode is blocking entry during
the high-volatility early phase of a recovery. That cost cannot appear in a
sample without one. Until the regime-split measurement in SURFER-DES-002 §2.2R
runs on 2018Q4 / 2020 / 2022 data, "SURFER says X" means "an unvalidated rule
says X".

The engine is declared anyway, because a single rule can be operated, paper
traded and falsified, and four parallel candidates cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

import pandas as pd

from fills import Position, adjudicate_session
from levels import StructuralExit, VolatilityRegimeGate
from schema import LevelSet

ENGINE_VERSION = "SURFER-ENGINE-v1.0.0"
ENGINE_BASIS = "SURFER-DES-002 §11 · candidate D · provisional, not validated"


class State(Enum):
    GATED = "gated"          # volatility gate closed; no order to place
    ARMED = "armed"          # flat, entry order resting
    HOLDING = "holding"      # in position, exit order resting
    NO_LEVELS = "no_levels"  # insufficient history or degenerate level set


@dataclass
class Signal:
    """What to do today. A statement about resting orders, not a forecast."""

    state: State
    session: date                 # the session these orders are for
    derived_from: date            # last completed session
    levels: LevelSet | None
    position: Position | None
    effective_exit: float | None
    atr_percentile: float | None
    atr60: float | None
    note: str

    @property
    def action(self) -> str:
        return {
            State.GATED: "주문 없음 — 변동성 게이트 차단",
            State.ARMED: "진입 스톱리밋 + 보호 손절 제출",
            State.HOLDING: "청산 주문만 갱신 (신규 진입 없음)",
            State.NO_LEVELS: "주문 없음 — 레벨 산출 불가",
        }[self.state]


class Engine:
    """Stateless with respect to market data; carries only the open position.

    The position is passed in and returned rather than stored, so a caller that
    reloads (a web request, a cron run) cannot silently lose or invent one.
    """

    version = ENGINE_VERSION
    basis = ENGINE_BASIS

    def __init__(self) -> None:
        self.entry = VolatilityRegimeGate()
        self.exit_rule = StructuralExit()

    @property
    def lookback_sessions(self) -> int:
        return max(self.entry.lookback_sessions, self.exit_rule.lookback_sessions)

    def signal(
        self,
        history: list[pd.DataFrame],
        armed_for: date,
        position: Position | None = None,
    ) -> Signal:
        """History must contain COMPLETED sessions only.

        A partially finished session here would derive today's orders from bars
        that have not closed — lookahead, and invisible in the output. Callers
        trim it; see board.py.
        """
        derived = (
            history[-1]["session_date"].iloc[0] if history and len(history[-1])
            else armed_for - timedelta(days=1)
        )
        pct = self.entry.percentile(history)
        atr = None

        if position is not None:
            # Holding: recompute the structural exit, never below the fixed stop.
            new_level = self.exit_rule(history, position.structural_exit)
            pos = position.__class__(
                **{**position.__dict__, "structural_exit": new_level}
            )
            return Signal(
                state=State.HOLDING, session=armed_for, derived_from=derived,
                levels=None, position=pos, effective_exit=pos.effective_stop,
                atr_percentile=pct, atr60=atr,
                note=(
                    f"구조 청산 {new_level} · 고정 손절 {position.stop} → "
                    f"유효 청산 {pos.effective_stop}"
                    if new_level is not None else
                    f"구조 청산 미산출 · 고정 손절 {position.stop} 유효"
                ),
            )

        lv = self.entry(history, armed_for)
        if lv is None:
            gated = (
                pct is not None
                and pct >= self.entry.BLOCK_AT_PERCENTILE
            )
            return Signal(
                state=State.GATED if gated else State.NO_LEVELS,
                session=armed_for, derived_from=derived, levels=None,
                position=None, effective_exit=None, atr_percentile=pct, atr60=None,
                note=(
                    f"ATR 백분위 {pct:.0f} ≥ {self.entry.BLOCK_AT_PERCENTILE:.0f} — "
                    "진입 레벨을 산출하지 않음"
                    if gated else "과거 부족 또는 레벨 관계 부적합"
                ),
            )
        return Signal(
            state=State.ARMED, session=armed_for, derived_from=derived, levels=lv,
            position=None, effective_exit=None, atr_percentile=pct,
            atr60=lv.meta.get("atr60"),
            note=f"ATR 백분위 {pct:.0f} < {self.entry.BLOCK_AT_PERCENTILE:.0f} — 게이트 통과",
        )


def replay_position(ds, engine: Engine, upto: date | None = None) -> Position | None:
    """Reconstruct the open position, if any, by replaying sessions in order.

    Necessary because the position is not persisted anywhere: a stateless web
    request cannot otherwise know whether a holding is open. Replaying is slower
    than a database but has one decisive property — it cannot disagree with the
    price data, which a stale stored position can.
    """
    sessions = [d for d in ds.sessions if upto is None or d <= upto]
    by = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    window = engine.lookback_sessions
    carried: Position | None = None

    for i, sd in enumerate(sessions):
        hist = [by[s] for s in sessions[max(0, i - window):i]]
        if carried is None:
            lv = engine.entry(hist, sd)
            out = adjudicate_session(lv, by[sd])
        else:
            lvl = engine.exit_rule(hist, carried.structural_exit)
            carried = carried.__class__(
                **{**carried.__dict__, "structural_exit": lvl}
            )
            out = adjudicate_session(None, by[sd], carried=carried)
        carried = None if out.exit is not None else out.position_out
    return carried

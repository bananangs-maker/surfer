"""Backtest: walk sessions in order, carrying positions across them.

This is the layer that was missing. The diagnostic asks a per-session question,
so a stateless adjudicator served it fine. A swing system cannot be measured
that way: 64% of entries were still open when their session ended, and their
outcome was simply never determined.

Three things are deliberate here.

COSTS ARE NOT OPTIONAL. Every fill pays spread, and gap fills pay more. A
backtest without a cost model on a 3x LETF is not optimistic, it is fictional.

GAP FILLS ARE TAGGED, NOT AVERAGED. Reported separately, because averaging tail
slippage into a mean is how the tail disappears from view.

AMBIGUITY IS SPLIT OUT. Every statistic is reported twice: over all trades, and
over only those trades whose entry and exit were both unambiguous. If the two
disagree materially, the result rests on intra-bar ordering that the data cannot
observe, and it should not be believed regardless of how good it looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from dataclasses import replace as _replace

from fills import Kind, Position, adjudicate_session
from levels import LevelGenerator
from schema import Dataset


@dataclass(frozen=True)
class Costs:
    """Execution costs. Defaults are deliberately unkind.

    spread_bps is charged on every fill. gap_extra_bps is charged ON TOP for
    fills that happened at a gap, because a resting order meeting a 3x LETF
    opening gap does not get the quiet mid-session spread.
    """

    spread_bps: float = 5.0
    gap_extra_bps: float = 20.0
    commission_per_share: float = 0.005
    commission_min: float = 1.0

    def buy_price(self, px: float, at_gap: bool) -> float:
        bps = self.spread_bps + (self.gap_extra_bps if at_gap else 0.0)
        return px * (1 + bps / 1e4)

    def sell_price(self, px: float, at_gap: bool) -> float:
        bps = self.spread_bps + (self.gap_extra_bps if at_gap else 0.0)
        return px * (1 - bps / 1e4)

    def commission(self, shares: float) -> float:
        return max(self.commission_min, self.commission_per_share * shares)


@dataclass
class Trade:
    entry_date: date
    entry_ts: pd.Timestamp
    entry_price: float          # net of costs
    entry_price_raw: float
    stop: float
    exit_date: date | None = None
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None      # net of costs
    exit_price_raw: float | None = None
    exit_kind: Kind | None = None
    sessions_held: int = 0
    entry_at_gap: bool = False
    exit_at_gap: bool = False
    ambiguous: bool = False
    commission: float = 0.0
    shares: float = 0.0

    @property
    def closed(self) -> bool:
        return self.exit_price is not None

    @property
    def gross_return(self) -> float | None:
        if not self.closed:
            return None
        return self.exit_price / self.entry_price - 1.0

    def net_return(self, equity_at_entry: float) -> float | None:
        """Return on the sleeve, with commission charged against equity."""
        if not self.closed:
            return None
        gross = (self.exit_price - self.entry_price) * self.shares
        return (gross - self.commission) / equity_at_entry


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: list[tuple[date, float]]
    sessions: int
    sessions_in_position: int
    open_trade: Trade | None
    dataset_label: str
    quarantined: bool
    quarantine_reason: str
    generator_name: str
    exit_rule_name: str
    costs: Costs
    notes: list[str] = field(default_factory=list)

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.closed]

    @property
    def unambiguous_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if not t.ambiguous]

    @property
    def exposure(self) -> float:
        return self.sessions_in_position / self.sessions if self.sessions else 0.0


def _stats(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    rets = np.array([t.gross_return for t in trades], dtype=float)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    held = np.array([t.sessions_held for t in trades], dtype=float)
    # Sequential compounding is valid because at most one position is open.
    curve = np.cumprod(1.0 + rets)
    peak = np.maximum.accumulate(curve)
    dd = curve / peak - 1.0
    return {
        "n": len(trades),
        "total_return": float(curve[-1] - 1.0),
        "win_rate": float(len(wins) / len(rets)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "best": float(rets.max()),
        "worst": float(rets.min()),
        "max_drawdown": float(dd.min()),
        "avg_sessions_held": float(held.mean()),
        "max_sessions_held": int(held.max()),
        "gap_entries": sum(1 for t in trades if t.entry_at_gap),
        "gap_exits": sum(1 for t in trades if t.exit_at_gap),
        "stopped": sum(1 for t in trades if t.exit_kind is Kind.STOP),
        "structural_exits": sum(1 for t in trades if t.exit_kind is Kind.STRUCTURAL),
        "target_exits": sum(1 for t in trades if t.exit_kind is Kind.TARGET),
    }


class QuarantineError(RuntimeError):
    pass


def run_backtest(
    ds: Dataset,
    generator: LevelGenerator,
    exit_rule=None,
    costs: Costs | None = None,
    starting_equity: float = 10_000.0,
    fraction: float = 1.0,
    acknowledge_quarantine: bool = False,
) -> BacktestResult:
    """Sequential walk. One position at a time; no pyramiding, no same-session
    re-entry. Both are forbidden by default because each adds a degree of
    freedom that improves a backtest without improving a system.

    A quarantined dataset does not block the run, but it must be acknowledged in
    the call. The label travels with the result so a figure cannot be quoted
    later without the caveat that produced it.
    """
    if ds.quarantined and not acknowledge_quarantine:
        raise QuarantineError(
            f"{ds.symbol}/{ds.source} is quarantined: {ds.quarantine_reason}\n"
            "Pass acknowledge_quarantine=True to run anyway. The result will "
            "carry the quarantine label."
        )

    costs = costs or Costs()
    sessions = ds.sessions
    by_session = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    window = getattr(generator, "lookback_sessions", 30)

    trades: list[Trade] = []
    equity_curve: list[tuple[date, float]] = []
    equity = starting_equity
    carried: Position | None = None
    live: Trade | None = None
    in_position_sessions = 0
    notes: list[str] = []

    for i, sd in enumerate(sessions):
        bars = by_session[sd]

        if carried is None:
            history = [by_session[s] for s in sessions[max(0, i - window):i]]
            levels = generator(history, sd)
            out = adjudicate_session(levels, bars)
        else:
            in_position_sessions += 1
            # Recompute the structural exit from completed sessions only, then
            # hand the position in with it attached. effective_stop keeps the
            # fixed stop as a floor, so this can raise protection, never lower it.
            if exit_rule is not None:
                history = [by_session[s] for s in sessions[max(0, i - window):i]]
                new_level = exit_rule(history, carried.structural_exit)
                carried = _replace(carried, structural_exit=new_level)
            out = adjudicate_session(None, bars, carried=carried)

        # --- new entry -------------------------------------------------
        if out.entry is not None and live is None:
            in_position_sessions += 1
            raw = out.entry.price
            net = costs.buy_price(raw, out.entry.at_gap)
            shares = (equity * fraction) / net
            live = Trade(
                entry_date=sd,
                entry_ts=out.entry.ts,
                entry_price=net,
                entry_price_raw=raw,
                stop=out.levels.initial_stop,
                entry_at_gap=out.entry.at_gap,
                ambiguous=out.entry.ambiguous,
                shares=shares,
                commission=costs.commission(shares),
            )

        # --- exit ------------------------------------------------------
        if out.exit is not None and live is not None:
            raw = out.exit.price
            net = costs.sell_price(raw, out.exit.at_gap)
            live.exit_date = sd
            live.exit_ts = out.exit.ts
            live.exit_price = net
            live.exit_price_raw = raw
            live.exit_kind = out.exit.kind
            live.exit_at_gap = out.exit.at_gap
            live.ambiguous = live.ambiguous or out.exit.ambiguous
            live.sessions_held = (
                carried.sessions_held + 1 if carried is not None else 0
            )
            live.commission += costs.commission(live.shares)
            r = live.net_return(equity)
            equity *= 1.0 + (r or 0.0)
            trades.append(live)
            live = None
            carried = None
        else:
            carried = out.position_out
            if carried is not None and live is None:
                # Defensive: a carry with no live trade means the two views of
                # state have diverged. Loud, because silent drift here would
                # corrupt every number downstream.
                notes.append(f"{sd}: carried position without a live trade")

        equity_curve.append((sd, equity))

    open_trade = live
    if open_trade is not None:
        notes.append(
            f"one position still open at end of data "
            f"(entered {open_trade.entry_date}); excluded from closed-trade stats"
        )

    return BacktestResult(
        trades=trades,
        equity=equity_curve,
        sessions=len(sessions),
        sessions_in_position=in_position_sessions,
        open_trade=open_trade,
        dataset_label=ds.describe(),
        quarantined=ds.quarantined,
        quarantine_reason=ds.quarantine_reason,
        generator_name=getattr(generator, "name", type(generator).__name__),
        exit_rule_name=(
            getattr(exit_rule, "name", type(exit_rule).__name__)
            if exit_rule is not None else "NONE (stop only)"
        ),
        costs=costs,
        notes=notes,
    )


def render(res: BacktestResult) -> str:
    all_s = _stats(res.closed_trades)
    un_s = _stats(res.unambiguous_trades)

    lines = [
        "SURFER BACKTEST",
        f"  dataset   : {res.dataset_label}",
        f"  generator : {res.generator_name}",
        f"  exit rule : {res.exit_rule_name}",
        f"  costs     : spread {res.costs.spread_bps}bp "
        f"(+{res.costs.gap_extra_bps}bp on gaps), "
        f"comm ${res.costs.commission_per_share}/sh min ${res.costs.commission_min}",
    ]
    if res.quarantined:
        lines += [
            "",
            "  *** QUARANTINED DATA - NOT EVIDENCE ***",
            f"  {res.quarantine_reason}",
        ]
    lines += [
        "",
        f"  sessions {res.sessions}   in position {res.sessions_in_position} "
        f"({res.exposure:.1%} exposure)",
        f"  closed trades {all_s['n']}   open at end "
        f"{1 if res.open_trade else 0}",
        "",
        f"  {'':<22}{'all trades':>14}{'unambiguous':>14}",
    ]

    def row(label, key, fmt="{:.2%}"):
        a = all_s.get(key)
        b = un_s.get(key)
        fa = fmt.format(a) if isinstance(a, float) else str(a)
        fb = fmt.format(b) if isinstance(b, float) else str(b)
        return f"  {label:<22}{fa:>14}{fb:>14}"

    lines += [
        row("trades", "n", "{}"),
        row("total return", "total_return"),
        row("max drawdown", "max_drawdown"),
        row("win rate", "win_rate"),
        row("avg win", "avg_win"),
        row("avg loss", "avg_loss"),
        row("worst trade", "worst"),
        row("avg sessions held", "avg_sessions_held", "{:.1f}"),
        row("structural exits", "structural_exits", "{}"),
        row("fixed-stop exits", "stopped", "{}"),
        row("target exits", "target_exits", "{}"),
        row("gap entries", "gap_entries", "{}"),
        row("gap exits", "gap_exits", "{}"),
    ]

    if all_s["n"] and un_s["n"]:
        drift = all_s["total_return"] - un_s["total_return"]
        lines += [
            "",
            f"  ambiguous trades excluded: {all_s['n'] - un_s['n']}",
            f"  return attributable to assumed ordering: {drift:+.2%}",
            "  If that figure is large relative to the total, the result rests",
            "  on intra-bar ordering the data cannot observe.",
        ]
    for n in res.notes[:6]:
        lines.append(f"  note: {n}")
    return "\n".join(lines)

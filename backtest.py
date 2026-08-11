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


SESSIONS_PER_YEAR = 252


def curve_metrics(curve: list[tuple[date, float]]) -> dict:
    """Time-indexed metrics from a session-by-session equity curve.

    Every figure here is measured against sessions elapsed, which is what makes
    it comparable with a buy-and-hold series. Calmar is CAGR / |MDD| per
    SURFER-DES-002 §2.2; Sharpe is deliberately absent, because on a 3x
    instrument the binding risk is the depth of the drawdown, not the standard
    deviation of returns.
    """
    if len(curve) < 2:
        return {"n_sessions": len(curve)}
    vals = np.array([v for _, v in curve], dtype=float)
    peak = np.maximum.accumulate(vals)
    dd = vals / peak - 1.0
    mdd = float(dd.min())
    total = float(vals[-1] / vals[0] - 1.0)
    years = len(vals) / SESSIONS_PER_YEAR
    cagr = float((vals[-1] / vals[0]) ** (1.0 / years) - 1.0) if years > 0 else 0.0

    # Longest stretch spent below a previous peak, in sessions.
    under = 0
    longest = 0
    for x in dd:
        under = under + 1 if x < -1e-12 else 0
        longest = max(longest, under)

    return {
        "n_sessions": len(vals),
        "total_return": total,
        "cagr": cagr,
        "max_drawdown": mdd,
        "calmar": cagr / abs(mdd) if mdd < -1e-12 else float("inf"),
        "longest_underwater_sessions": longest,
        "final_equity": float(vals[-1]),
    }


def buy_and_hold(
    ds: Dataset, costs: Costs | None = None, starting_equity: float = 10_000.0
) -> list[tuple[date, float]]:
    """The SURFER-DES-002 baseline: buy at the first session's open, hold.

    Costs are charged once, on the single entry. Charging the baseline nothing at
    all would flatter SURFER by comparison, and the point of a baseline is that
    it be beatable only on merit.
    """
    costs = costs or Costs()
    by = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    sessions = ds.sessions
    first = by[sessions[0]]
    entry = costs.buy_price(float(first["open"].iloc[0]), at_gap=False)
    shares = (starting_equity - costs.commission(starting_equity / entry)) / entry
    return [
        (d, shares * float(by[d]["close"].iloc[-1])) for d in sessions
    ]


def verdict(
    surfer_curve: list[tuple[date, float]],
    bh_curve: list[tuple[date, float]],
    calmar_multiple_required: float = 1.5,
    mdd_share_allowed: float = 0.50,
) -> dict:
    """Apply SURFER-DES-002 §2.2 and §2.3. Both must pass; they are independent.

    Thresholds are the pre-registered ones and are arguments only so the test
    suite can pin them - not so they can be tuned after seeing a result.
    """
    s = curve_metrics(surfer_curve)
    b = curve_metrics(bh_curve)

    # A multiplicative threshold inverts when the baseline is negative: with B&H
    # Calmar at -0.50, "1.5x" asks for >= -0.75, which a money-losing system
    # clears. SURFER-DES-002 §3.3 puts 2022 inside the measured windows, so this
    # is not hypothetical. When the baseline is not positive the multiple is
    # meaningless and the requirement becomes an absolute one: SURFER's own
    # Calmar must be positive. That is strictly harder than the broken form, so
    # repairing it cannot flatter the result.
    if b["calmar"] > 0:
        required = calmar_multiple_required * b["calmar"]
        calmar_ok = s["calmar"] >= required
    else:
        required = 0.0
        calmar_ok = s["calmar"] > 0.0
    # Positive Calmar is necessary in every case: a negative one means the CAGR
    # itself is negative.
    calmar_ok = calmar_ok and s["calmar"] > 0.0

    mdd_ok = abs(s["max_drawdown"]) <= mdd_share_allowed * abs(b["max_drawdown"])
    return {
        "surfer": s,
        "buy_and_hold": b,
        "calmar_required": required,
        "baseline_calmar_positive": bool(b["calmar"] > 0),
        "calmar_pass": bool(calmar_ok),
        "mdd_allowed": -mdd_share_allowed * abs(b["max_drawdown"]),
        "mdd_pass": bool(mdd_ok),
        "pass": bool(calmar_ok and mdd_ok),
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
    # Cash and shares tracked separately so an open position can be marked to
    # the session close. Compounding realised returns at exit only produces a
    # TRADE-INDEXED curve, whose drawdown cannot be compared with a buy-and-hold
    # drawdown measured over TIME. That mismatch, not the size of the intra-trade
    # excursion, is what made the previous method unusable for SURFER-DES-002.
    cash = starting_equity
    shares_held = 0.0
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
            shares = (cash * fraction) / net
            cash -= shares * net + costs.commission(shares)
            shares_held = shares
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
            exit_commission = costs.commission(live.shares)
            live.commission += exit_commission
            cash += live.shares * net - exit_commission
            shares_held = 0.0
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

        # Mark to the session close, every session, in position or not.
        close_px = float(bars["close"].iloc[-1])
        equity_curve.append((sd, cash + shares_held * close_px))

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


def render(res: BacktestResult, bh_curve=None) -> str:
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
    m = curve_metrics(res.equity)
    lines += [
        "",
        "  TIME-INDEXED (session mark-to-market) - the axis DES-002 judges on",
        f"  {'CAGR':<26}{m['cagr']:>13.2%}",
        f"  {'max drawdown':<26}{m['max_drawdown']:>13.2%}",
        f"  {'Calmar (CAGR/|MDD|)':<26}{m['calmar']:>13.2f}",
        f"  {'longest underwater':<26}{m['longest_underwater_sessions']:>10} sessions",
    ]
    if bh_curve is not None:
        v = verdict(res.equity, bh_curve)
        b = v["buy_and_hold"]
        lines += [
            "",
            "  vs BUY AND HOLD (DES-002 baseline)",
            f"  {'B&H CAGR':<26}{b['cagr']:>13.2%}",
            f"  {'B&H max drawdown':<26}{b['max_drawdown']:>13.2%}",
            f"  {'B&H Calmar':<26}{b['calmar']:>13.2f}",
            "",
            f"  §2.2 Calmar >= {v['calmar_required']:.2f} (1.5x B&H) "
            f"-> {'PASS' if v['calmar_pass'] else 'FAIL'}",
            f"  §2.3 MDD >= {v['mdd_allowed']:.2%} (50% of B&H) "
            f"-> {'PASS' if v['mdd_pass'] else 'FAIL'}",
            f"  VERDICT: {'PASS' if v['pass'] else 'FAIL'}",
        ]
    for n in res.notes[:6]:
        lines.append(f"  note: {n}")
    return "\n".join(lines)

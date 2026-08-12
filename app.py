"""SURFER diagnostic viewer.

A report viewer, nothing more. It reads market data, runs the ambiguity
diagnostic, and renders it. It places no orders, stores no credentials, and has
no connection to a broker. Do not grow it into an execution service - if orders
are ever automated, that belongs in a separate deployment with separate secrets.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import asdict

import pandas as pd
from flask import Flask, render_template, request

import integrity
from diagnostics import purchase_case, resolution_comparison
from backtest import Costs, buy_and_hold, curve_metrics, run_backtest, verdict
from backtest import _stats as _bt_stats
import board as board_mod
from chart import annotated_session_svg, pick_example
from levels import (PlaceholderBreakout, PriorCloseVolatilityBreakout,
                    PullbackToPriorLow, StructuralExit, VolatilityRegimeGate,
                    stub_range_ratio)
from windows import (REPEAL_NOTE, SELECTION_END, SPLIT_REPEALED, Unlock,
                     describe_windows, selection_slice)
from schema import Dataset

# template_folder="." keeps the HTML at the repo root - no subfolders anywhere.
app = Flask(__name__, template_folder=".")

SYMBOLS = {
    "TQQQ": "ProShares UltraPro QQQ (3x Nasdaq-100)",
    "SOXL": "Direxion Semiconductor Bull (3x)",
    "SPY": "S&P 500 ETF (1x control)",
    "SYNTH3X": "Synthetic fixture - no market content",
}

_CACHE: dict[str, tuple[float, Dataset]] = {}
CACHE_TTL = 60 * 30


def get_dataset(symbol: str) -> Dataset:
    hit = _CACHE.get(symbol)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    if symbol == "SYNTH3X":
        from loaders import load_synthetic
        ds = load_synthetic(n_sessions=400)
    else:
        from loaders import load_yfinance
        ds = load_yfinance(symbol)

    _CACHE[symbol] = (time.time(), ds)
    return ds


@app.route("/")
def index():
    symbol = request.args.get("symbol", "SYNTH3X").upper()
    if symbol not in SYMBOLS:
        symbol = "SYNTH3X"

    trigger = float(request.args.get("trigger", 0.10))
    limit = float(request.args.get("limit", 0.35))
    stop = float(request.args.get("stop", 1.20))

    ctx = {
        "symbols": SYMBOLS,
        "symbol": symbol,
        "params": {"trigger": trigger, "limit": limit, "stop": stop},
        "error": None,
        "ran": False,
        "chart_svg": None,
        "has_ambiguous": False,
        # Present on every render so the idle and error branches cannot blow up
        # in the template. Two tests failed exactly this way.
        "case": None,
    }

    # The landing page must answer instantly. Render's port scanner probes "/"
    # with a short timeout, and an eager diagnostic here made every probe time
    # out - the service was declared to have no open HTTP ports while it was
    # busy computing a report nobody had asked for. Compute only on request.
    if request.args.get("run") != "1":
        return render_template("report_template.html", **ctx)

    ctx["ran"] = True
    try:
        t0 = time.time()
        ds = get_dataset(symbol)
        rep = integrity.check(ds.bars, ds.interval_minutes)
        hist = [ds.session(s) for s in ds.sessions]
        stub = stub_range_ratio(hist)

        gen = PlaceholderBreakout(
            trigger_atr_mult=trigger, limit_atr_mult=limit, stop_atr_mult=stop
        )
        # Drawn from the same data the diagnostic counts, so the illustration
        # cannot drift from what is reported below it.
        ex = pick_example(ds, gen)
        if ex is not None:
            hist, sess, lv, amb = ex
            ctx["chart_svg"] = annotated_session_svg(
                hist, sess, lv, amb, title=f"SURFER | {symbol}"
            )
            ctx["has_ambiguous"] = amb is not None

        r60, r1d = resolution_comparison(ds, gen)

        reduction = (
            1 - (r60.ambiguity_rate / r1d.ambiguity_rate)
            if r1d.ambiguity_rate > 0
            else None
        )

        ctx.update(
            {
                "ds": ds,
                "rep": rep,
                "stub": stub,
                "r60": r60,
                "r1d": r1d,
                "reduction": reduction,
                "case": purchase_case(r60, r1d),
                "elapsed": time.time() - t0,
                "generator": gen.name,
            }
        )
    except Exception as e:  # surfaced in the page, not swallowed
        ctx["error"] = f"{type(e).__name__}: {e}"
        ctx["trace"] = traceback.format_exc()

    return render_template("report_template.html", **ctx)


CANDIDATES = {
    "A": ("고가 돌파", PlaceholderBreakout),
    "B": ("저가 되돌림", PullbackToPriorLow),
    "C": ("종가 변동성 돌파", PriorCloseVolatilityBreakout),
    "D": ("A + 변동성 게이트", VolatilityRegimeGate),
}


@app.route("/backtest")
def backtest_view():
    """Backtest, gated by the SURFER-DES-002 §3.3 window lock.

    The available price source (yfinance, 730 days) lies ENTIRELY inside the
    validation window. Running a performance backtest on it here would open that
    window through the back door - the one place where it is easiest to do by
    accident, since it is a button on a web page. So real symbols are refused
    until the lock record exists; the synthetic fixture stays available because
    it contains no market information to leak.
    """
    symbol = request.args.get("symbol", "SYNTH3X").upper()
    cand = request.args.get("candidate", "A").upper()
    if cand not in CANDIDATES:
        cand = "A"

    ctx = {"symbols": SYMBOLS, "symbol": symbol, "candidates": CANDIDATES,
           "candidate": cand, "blocked": None, "unlock": Unlock.load()}
    try:
        ds = get_dataset(symbol)
        ctx["windows"] = describe_windows(ds)

        # The lock restricts the WINDOW, not the symbol. While locked, a real
        # symbol is run on its selection slice; only post-2019 sessions are
        # withheld. Blocking the symbol outright (an earlier version of this)
        # made the selection window unreachable too, which is not what §3.3 says.
        ds_used = ds
        if (not SPLIT_REPEALED) and symbol != "SYNTH3X" and Unlock.load() is None:
            ds_used = selection_slice(ds)
            n_withheld = len(ds.sessions) - len(ds_used.sessions)
            if len(ds_used.sessions) < 60:
                ctx["blocked"] = (
                    f"{symbol}의 선택 구간 데이터가 {len(ds_used.sessions)}세션뿐입니다 "
                    f"(검증 구간 {n_withheld}세션은 잠김). "
                    f"DES-002 §3.3에 따라 후보 선택 확정 전에는 {SELECTION_END} 이후를 "
                    f"열 수 없습니다. 선택 구간 데이터를 확보한 뒤 진행하세요."
                )
                return render_template("backtest.html", **ctx)
            ctx["window_note"] = (
                f"선택 구간만 사용 중 — {len(ds_used.sessions)}세션. "
                f"{SELECTION_END} 이후 {n_withheld}세션은 잠겨 있습니다."
            )

        _, G = CANDIDATES[cand]
        res = run_backtest(ds_used, G(), exit_rule=StructuralExit(),
                           costs=Costs(), acknowledge_quarantine=True)
        bh = buy_and_hold(ds_used, costs=Costs())
        ctx.update({
            "res": res,
            "v": verdict(res.equity, bh),
            "trade_stats": _bt_stats(res.closed_trades),
            "amb_share": (
                sum(1 for t in res.closed_trades if t.ambiguous)
                / len(res.closed_trades) if res.closed_trades else 0.0
            ),
        })
    except Exception as e:
        import traceback
        ctx["error"] = f"{type(e).__name__}: {e}"
        ctx["trace"] = traceback.format_exc()
    return render_template("backtest.html", **ctx)


@app.route("/compare")
def compare_view():
    """All four candidates, one run, DES-002 §6 applied to each.

    §3.3 was repealed, so this runs on whatever window the feed provides. That
    makes the comparison an IN-SAMPLE one: the winner is the rule that best fitted
    this window, and no data is being held back to test whether the fit holds.
    The page says so, because a table of verdicts reads like validation and this
    one is not.
    """
    symbol = request.args.get("symbol", "SYNTH3X").upper()
    if symbol not in SYMBOLS:
        symbol = "SYNTH3X"
    ctx = {"symbols": SYMBOLS, "symbol": symbol, "rows": [], "error": None,
           "repeal_note": REPEAL_NOTE if SPLIT_REPEALED else None, "bh": None}
    try:
        ds = get_dataset(symbol)
        bh = buy_and_hold(ds, costs=Costs())
        ctx["bh"] = curve_metrics(bh)
        ctx["windows"] = describe_windows(ds)
        for key, (label, G) in CANDIDATES.items():
            res = run_backtest(ds, G(), exit_rule=StructuralExit(),
                               costs=Costs(), acknowledge_quarantine=True)
            st = _bt_stats(res.closed_trades)
            v = verdict(res.equity, bh)
            n = st.get("n", 0)
            ctx["rows"].append({
                "key": key, "label": label, "v": v, "st": st,
                "n": n,
                "amb": (sum(1 for t in res.closed_trades if t.ambiguous) / n) if n else 0.0,
                "exposure": res.exposure,
                # DES-002 §6: an ambiguity share above 40% voids the verdict,
                # whichever way it went.
                "amb_void": (
                    (sum(1 for t in res.closed_trades if t.ambiguous) / n) > 0.40
                    if n else False
                ),
            })
    except Exception as e:
        import traceback
        ctx["error"] = f"{type(e).__name__}: {e}"
        ctx["trace"] = traceback.format_exc()
    return render_template("compare.html", **ctx)


@app.route("/levels")
def levels_view():
    """Today's levels for every candidate, side by side.

    Cheap by construction: it reads the cached dataset and evaluates four
    generators once. No performance figures appear here, so it does not touch the
    DES-002 §3.3 window lock - levels are a statement about where orders would
    rest, not about what they earned.
    """
    symbol = request.args.get("symbol", "SYNTH3X").upper()
    if symbol not in SYMBOLS:
        symbol = "SYNTH3X"
    ctx = {"symbols": SYMBOLS, "symbol": symbol, "board": None, "sig": None,
           "worksheet": "", "error": None, "chart_svg": None}
    try:
        ds = get_dataset(symbol)
        bd = board_mod.build_engine(ds, symbol)
        ctx["board"] = bd
        ctx["sig"] = bd.signal
        ctx["worksheet"] = board_mod.engine_worksheet(bd)
        # Draw the engine's own levels on real bars when it has any to draw.
        if bd.signal.levels is not None:
            sessions = ds.sessions
            by = {
                d: g.sort_values("ts").reset_index(drop=True)
                for d, g in ds.bars.groupby("session_date", sort=True)
            }
            tail = sessions[-3:-1] if len(sessions) >= 3 else sessions[:-1]
            hist = (pd.concat([by[s] for s in tail], ignore_index=True)
                    if tail else by[sessions[-1]].iloc[:0])
            ctx["chart_svg"] = annotated_session_svg(
                hist, by[sessions[-1]], bd.signal.levels, None,
                title=f"SURFER | {symbol}",
            )
    except Exception as e:
        import traceback
        ctx["error"] = f"{type(e).__name__}: {e}"
        ctx["trace"] = traceback.format_exc()
    return render_template("levels_board.html", **ctx)


@app.route("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

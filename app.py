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
from chart import annotated_session_svg, pick_example, terminal_svg
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

# Six hours, not thirty minutes. A 30-minute TTL was incoherent with a system
# that decides ONCE PER DAY: the bars do not change between two loads on the same
# morning, but every expiry sent another request to Yahoo. Opening four pages a
# few times was enough to hit YFRateLimitError.
CACHE_TTL = 60 * 60 * 6

# A stale dataset beats no dataset. When a refetch is rate-limited, serve the
# cached copy however old it is and say so, rather than showing a traceback:
# yesterday's levels are wrong but legible, an error page is neither.
_STALE_KEEP = 60 * 60 * 72


class RateLimited(RuntimeError):
    pass


def _fetch(symbol: str) -> Dataset:
    if symbol == "SYNTH3X":
        from loaders import load_synthetic
        return load_synthetic(n_sessions=400)
    from loaders import load_yfinance
    return load_yfinance(symbol)


def get_dataset(symbol: str) -> tuple[Dataset, float]:
    """Return (dataset, age_seconds). Raises RateLimited only if nothing cached.

    Retries with a short backoff: Yahoo's limiter is per-window, so a second
    attempt a moment later often succeeds where an immediate one does not.
    """
    hit = _CACHE.get(symbol)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1], now - hit[0]

    last_err: Exception | None = None
    was_rate_limit = False
    for pause in (0.0, 1.5, 4.0):
        if pause:
            time.sleep(pause)
        try:
            ds = _fetch(symbol)
            _CACHE[symbol] = (time.time(), ds)
            return ds, 0.0
        except Exception as e:                      # noqa: BLE001
            last_err = e
            was_rate_limit = (
                "ratelimit" in type(e).__name__.lower()
                or "too many" in str(e).lower()
            )
            if not was_rate_limit:
                break

    if hit and now - hit[0] < _STALE_KEEP:
        return hit[1], now - hit[0]
    if not was_rate_limit:
        # Do not relabel an unrelated failure as a rate limit: a delisted ticker
        # or a schema change would then be shown as "wait a few minutes", and the
        # real cause would never surface.
        raise last_err
    raise RateLimited(
        f"{symbol} 데이터를 받아올 수 없고 캐시도 없습니다.\n"
        f"야후가 호출 빈도를 제한했습니다 ({type(last_err).__name__}). "
        "몇 분 뒤 다시 시도해 주세요.\n"
        "이 화면은 하루 한 번만 열면 충분한 시스템이며, 반복 새로고침이 "
        "제한을 부릅니다."
    ) from last_err


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
        ds, ds_age = get_dataset(symbol)
        ctx["ds_age_h"] = ds_age / 3600.0
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
                hist, sess, lv, amb, title=f"SURFER | {symbol}",
                theme="dark", animate=True,
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
        ds, ds_age = get_dataset(symbol)
        ctx["ds_age_h"] = ds_age / 3600.0
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


@app.route("/validate")
def validate_view():
    """§2.2R on out-of-sample data — or an explanation of why it cannot run.

    Deliberately reports MEASURABILITY before any verdict. A window with no
    decline passes two of three conditions by vacuity, and two PASSes on a page
    read as validation whether or not anything was tested.
    """
    symbol = request.args.get("symbol", "SYNTH3X").upper()
    if symbol not in SYMBOLS:
        symbol = "SYNTH3X"
    ctx = {"symbols": SYMBOLS, "symbol": symbol, "cov": None, "v": None,
           "blocked": None, "error": None, "windows": None}
    try:
        from engine import Engine
        from regimes import coverage, verdict_2_2R
        from windows import (MIN_SESSIONS_FOR_ENGINE, WindowLocked,
                             describe_windows, validation_slice)

        ds, ds_age = get_dataset(symbol)
        ctx["ds_age_h"] = ds_age / 3600.0
        ctx["windows"] = describe_windows(ds)
        from regimes import Regime
        ctx["REG"] = Regime
        try:
            oos = validation_slice(ds)
        except WindowLocked as e:
            ctx["blocked"] = str(e)
            return render_template("validate.html", **ctx)

        bh = buy_and_hold(oos, costs=Costs())
        cov = coverage(bh, min_sessions_needed=MIN_SESSIONS_FOR_ENGINE)
        ctx["cov"] = cov
        if cov.measurable:
            res = run_backtest(oos, Engine().entry, exit_rule=Engine().exit_rule,
                               costs=Costs(), acknowledge_quarantine=True)
            ctx["v"] = verdict_2_2R(res.equity, bh)
            ctx["res"] = res
    except Exception as e:
        import traceback
        ctx["error"] = f"{type(e).__name__}: {e}"
        ctx["trace"] = traceback.format_exc()
    return render_template("validate.html", **ctx)


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
        ds, ds_age = get_dataset(symbol)
        ctx["ds_age_h"] = ds_age / 3600.0
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
           "worksheet": "", "error": None, "chart_svg": None,
           "rate_limited": None, "ds_age_h": 0.0, "bars_json": None}
    try:
        ds, ds_age = get_dataset(symbol)
        ctx["ds_age_h"] = ds_age / 3600.0
        bd = board_mod.build_engine(ds, symbol)
        ctx["board"] = bd
        ctx["sig"] = bd.signal
        ctx["worksheet"] = board_mod.engine_worksheet(bd)
        from windows import describe_windows as _dw
        ctx["windows"] = _dw(ds)
        # Terminal view: all three panels share one x-axis, so the price frame
        # and the percentile series must cover the same sessions.
        SESSIONS_SHOWN = 20
        series = board_mod.history_series(ds, sessions_back=SESSIONS_SHOWN)
        if series.dates:
            by = {
                d: g.sort_values("ts").reset_index(drop=True)
                for d, g in ds.bars.groupby("session_date", sort=True)
            }
            frame = pd.concat([by[d] for d in series.dates], ignore_index=True)
            ctx["chart_svg"] = terminal_svg(
                frame, len(by[series.dates[-1]]), bd.signal.levels, series,
                symbol, bd.signal.state.value, bd.signal.atr60,
                bd.signal.atr_percentile, theme="dark", animate=True,
            )
            ctx["series"] = series
            # Bars for the pointer readout. Emitted as JSON rather than as data
            # attributes on 140 rects: one parse beats 140 DOM lookups per move.
            import json as _json
            ny = frame["ts"].dt.tz_convert("America/New_York")
            ctx["bars_json"] = _json.dumps([
                {"o": round(float(r.open), 6), "h": round(float(r.high), 6),
                 "l": round(float(r.low), 6), "c": round(float(r.close), 6),
                 "t": t.strftime("%m-%d %H:%M")}
                for r, t in zip(frame.itertuples(), ny)
            ], separators=(",", ":"))
    except RateLimited as e:
        ctx["rate_limited"] = str(e)
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

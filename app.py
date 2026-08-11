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

from flask import Flask, render_template, request

import integrity
from diagnostics import resolution_comparison
from levels import PlaceholderBreakout, stub_range_ratio
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
    }

    try:
        t0 = time.time()
        ds = get_dataset(symbol)
        rep = integrity.check(ds.bars, ds.interval_minutes)
        hist = [ds.session(s) for s in ds.sessions]
        stub = stub_range_ratio(hist)

        gen = PlaceholderBreakout(
            trigger_atr_mult=trigger, limit_atr_mult=limit, stop_atr_mult=stop
        )
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
                "elapsed": time.time() - t0,
                "generator": gen.name,
            }
        )
    except Exception as e:  # surfaced in the page, not swallowed
        ctx["error"] = f"{type(e).__name__}: {e}"
        ctx["trace"] = traceback.format_exc()

    return render_template("report_template.html", **ctx)


@app.route("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

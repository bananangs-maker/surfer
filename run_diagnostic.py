#!/usr/bin/env python3
"""SURFER-DIAG-001 runner.

    python run_diagnostic.py --source synthetic
    python run_diagnostic.py --source yfinance --symbol TQQQ
    python run_diagnostic.py --source firstrate --path TQQQ_1hour.csv --symbol TQQQ

The only output is a diagnostic. There is no --performance flag, on purpose:
the engine has a placeholder level generator, and the only reachable data is
quarantined. A performance number produced from either would measure the shim,
not the idea.
"""

from __future__ import annotations

import argparse
import sys

import integrity
from diagnostics import render_comparison, resolution_comparison
from levels import PlaceholderBreakout, stub_range_ratio
from schema import Adjustment


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["synthetic", "yfinance", "firstrate"],
                   default="synthetic")
    p.add_argument("--symbol", default="SYNTH3X")
    p.add_argument("--path", default=None, help="CSV path for --source firstrate")
    p.add_argument("--sessions", type=int, default=400, help="synthetic only")
    p.add_argument("--trigger-atr", type=float, default=0.10)
    p.add_argument("--limit-atr", type=float, default=0.35)
    p.add_argument("--stop-atr", type=float, default=1.20)
    args = p.parse_args()

    if args.source == "synthetic":
        from loaders import load_synthetic
        ds = load_synthetic(symbol=args.symbol, n_sessions=args.sessions)
    elif args.source == "yfinance":
        from loaders import load_yfinance
        ds = load_yfinance(args.symbol)
    else:
        if not args.path:
            print("--path required for firstrate", file=sys.stderr)
            return 2
        from loaders import load_firstrate_csv
        ds = load_firstrate_csv(args.path, args.symbol,
                                adjustment=Adjustment.SPLIT_AND_DIVIDEND)

    rep = integrity.check(ds.bars, ds.interval_minutes)
    print(rep.render())
    if rep.fatal:
        print("\nAborting: integrity failures must be resolved first.")
        return 1
    print()

    hist = [ds.session(s) for s in ds.sessions]
    sr = stub_range_ratio(hist)
    print("STUB BAR RANGE CHECK (settles the exclude_stubs decision)")
    print(f"  30m stub bars : mean TR {sr['stub_mean_tr_pct']*100:.3f}% of price"
          f"  (n={sr['n_stub']})")
    print(f"  60m full bars : mean TR {sr['full_mean_tr_pct']*100:.3f}% of price"
          f"  (n={sr['n_full']})")
    print(f"  ratio         : {sr['ratio']:.2f}x  "
          f"({'stub is WIDER' if sr['ratio'] > 1 else 'stub is narrower'})")
    print()

    gen = PlaceholderBreakout(
        trigger_atr_mult=args.trigger_atr,
        limit_atr_mult=args.limit_atr,
        stop_atr_mult=args.stop_atr,
    )
    r60, r1d = resolution_comparison(ds, gen)
    print(render_comparison(r60, r1d, ds))
    print(f"\n  generator: {gen.name}  (PLACEHOLDER - replace before any claim)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

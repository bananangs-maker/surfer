# SURFER — engine v0.2.0 (flat layout)

A once-daily resting-order system whose price levels are derived from
60-minute structure.

**It is not a 60-minute signal system.** It does not react to intraday bar
closes. It decides once per session, before the open, and delegates execution to
resting orders. Nobody is awake at 03:00 KST to act on a bar close, and a system
that requires that has moved discipline back into human willpower — which is
what the rules were supposed to remove.

60-minute data earns its place in **fill adjudication**, not signal generation.

Separate project from MATILDA. The V5 freeze does not apply, and neither does
MATILDA's pre-registration — SURFER needs its own.

## Layout: deliberately flat

Every file sits at the repository root. No subfolders, anywhere.

This is not tidiness; it removes a failure mode. Three deploys failed because
browser and drag-and-drop uploads silently flattened a nested package, leaving
imports pointing at directories that did not exist. A flat repo cannot be
flattened.

The architectural boundary that matters is preserved — `levels.py` and
`fills.py` are still separate modules with the same contract — it is just
expressed as separate files rather than a package. `app.py` sets
`template_folder="."` so the HTML lives at the root too.

## Run

```bash
python -m pytest -q                                          # 28 passed
python run_diagnostic.py --source synthetic --sessions 400    # terminal
python app.py                                                # browser :8000
```

## Deploy (Render)

`render.yaml` is a Blueprint for a **free** web service. Render's servers are in
the US, so the yfinance path works there even when it does not locally.

New → Blueprint → pick this repo → Apply. No environment variables needed; the
service holds no credentials.

Free plan: sleeps after ~15 min idle, so the first load after a gap takes about a
minute, and the first fetch of 730 days of 60-minute bars is slow (hence
`--timeout 180`). Results cache in memory for 30 minutes.

**The viewer reads and renders. It places no orders and stores no credentials.**
If execution is ever automated it belongs in a separate deployment with separate
secrets — do not grow this service into that one.

## What it produces

A diagnostic. There is no performance flag.
`diagnostics.compute_performance()` raises on a quarantined dataset and is
otherwise unimplemented, because the level generator is a shim.

### The diagnostic that matters

`SURFER-DIAG-001` runs the identical generator and adjudicator at two
resolutions — daily OHLC and 60-minute — and reports the share of sessions whose
outcome depends on **unobservable intra-bar ordering**.

That share is a structural property of bar width versus level spacing. It does
not require the sample to be a representative market regime, so the free 730-day
window can answer it honestly, unlike any return statistic from the same window.

It is also the question the money hangs on. If 60-minute bars barely reduce the
ambiguous share, paid intraday history buys nothing. If they reduce it a lot, the
purchase has a written numeric justification. On the synthetic fixture the
reduction is ~42% (33.3% → 19.2%) — that demonstrates the mechanism works and
says nothing about TQQQ, which is the point of running it on real bars.

**Caveat, and it is not small.** The ambiguity rate depends on level *spacing*,
not only bar width. Widening the stop moves levels apart and the rate falls on
its own: at `stop=2.0, trigger=0.30` the fixture reports 2.1% instead of 19.2%.
The rate is a joint property of the data and the level rule and cannot be quoted
without its parameters. Compare resolutions at *fixed* parameters; never across
parameter sets.

## Files

```
schema.py         the only contract the rest of the code knows
integrity.py      sessions, stub bars, grid gaps, split-artefact flags
loaders.py        yfinance (quarantined) / FirstRate CSV / synthetic fixture
levels.py         PURE function of prior sessions -> LevelSet   <- THE OPEN PLUG
fills.py          LevelSet + following bars -> fills, with ambiguity flags
aggregate.py      60m -> daily, for the resolution comparison
diagnostics.py    the comparison; performance deliberately obstructed
app.py            web viewer
report_template.html
run_diagnostic.py CLI
test_*.py         28 tests
```

## Hard-coded rules (test_fills.py pins all three)

1. **Adverse intra-bar ordering.** Two actionable levels inside one bar's
   `[low, high]` → resolve to the worst outcome and flag the bar. The flag
   matters more than the resolution: a high ambiguity rate makes a run
   uninterpretable regardless of what it reports.
2. **Asymmetric order types.** Entry is a stop-*limit* — gap past the limit and
   there is no fill, because a missed trade is not a loss. The protective stop is
   a plain stop — it fills on the gap, at the gap price, however bad, because an
   unfilled stop is just an unhedged 3x position.
3. **Gap fills are tagged, not averaged.** Averaging gap slippage into an overall
   mean hides the tail, and the tail is what ends accounts.

`test_app.py` adds a fourth, learned from a failed deploy: **HTTP 200 is not
success.** A page can return 200 while displaying a traceback, and a health check
will call that healthy. The tests assert the error branch is absent.

## Data status

| source | 60m depth | verdict |
|---|---|---|
| yfinance | 730 days | quarantined by construction — plumbing and structural diagnostics only |
| FirstRate | 2000– | intended production source; `adjustment` must be passed explicitly |
| synthetic | n/a | test fixture, no market content |

`Dataset.quarantined` has no parameter to disable it on the yfinance path. That
window excludes 2018Q4, 2020Q1 and 2022 — exactly the regimes that decide whether
a leveraged-ETF swing system is viable.

## Open items

- **Level generation rule.** `PlaceholderBreakout` is a shim: prior-session-high
  breakout, ATR-scaled band, ATR-multiple stop. Not reasoned about, not argued
  for, parameters picked to be roughly plausible. Replace before any claim.
- **`exclude_stubs`.** Unresolved — see below.
- **Broker capability.** Whether the broker supports stop / stop-limit orders on
  US equities, and what order durations exist, is unverified. If stop orders are
  unavailable the design collapses. This gates everything downstream.
- **Execution falsifier.** SURFER's characteristic failure is execution, not
  signal. Pre-register something like: *mean deviation between realised fill and
  specified level exceeds X bp, or entry stop-limit fill rate falls below Y% →
  system void regardless of P&L.* Measurable in a few weeks of paper trading,
  before buying data.

## One assumption already falsified

The first `atr60()` excluded stub bars on the stated grounds that a half-length
bar has a smaller range, so leaving them in biases ATR downward. The test failed.

The 15:30–16:00 stub covers the closing auction and the last-hour volume ramp. In
the fixture its mean true range is **1.30× an average full 60-minute bar** — the
*widest* bar of the session, not the narrowest. Excluding it *lowers* ATR.

So the direction of the bias is an empirical question about the instrument, not
something reasoning settles. `exclude_stubs` stays `True` only so the placeholder
runs; `levels.stub_range_ratio()` measures it and both the CLI and the web viewer
print the measurement. Settle it on real TQQQ and SOXL bars before fixing it in
the pre-registration document.

A second, separate confound: excluding stubs shortens the bar sequence, so a
fixed `lookback_bars` window spans a different number of calendar sessions
between the two settings.

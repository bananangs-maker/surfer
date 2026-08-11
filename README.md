# SURFER — engine v0.1.0

A once-daily resting-order system whose price levels are derived from
60-minute structure.

**It is not a 60-minute signal system.** It does not react to intraday bar
closes. It decides once per session, before the open, and delegates execution to
resting orders. This is the reason the project exists in this shape: nobody is
awake at 03:00 KST to act on a bar close, and a system that requires that has
moved discipline back into human willpower — which is what the rules were
supposed to remove.

60-minute data earns its place in **fill adjudication**, not signal generation.

Separate project from MATILDA. The V5 freeze does not apply, and neither does
MATILDA's pre-registration — SURFER needs its own.

## Run

```bash
python run_diagnostic.py --source synthetic --sessions 400
python -m pytest -q tests/
```

`--source yfinance --symbol TQQQ` works once `pip install yfinance` is done and
the machine has network access to Yahoo.

## What it does and does not produce

The only output is a diagnostic. There is no performance flag.
`diagnostics.compute_performance()` raises on a quarantined dataset and is
otherwise unimplemented, because the level generator is a shim.

### The diagnostic that matters

`SURFER-DIAG-001` runs the identical generator and adjudicator at two
resolutions — daily OHLC and 60-minute — and reports the share of sessions whose
outcome depends on **unobservable intra-bar ordering**.

This is a structural property of bar width versus level spacing. It does not
depend on the sample being a representative market regime, so the free
730-day window can answer it honestly, unlike any return statistic measured on
the same window.

It is also the question the money hangs on. If 60-minute bars barely reduce the
ambiguous share, paid intraday history buys nothing. If they reduce it a lot,
the purchase has a written numeric justification. On the synthetic fixture the
reduction is ~42% (33.3% → 19.2%) — that number demonstrates the mechanism
works and says nothing about TQQQ, which is the point of running it on real
bars.

## Architecture

```
loaders/          adapters → schema.Dataset. Swapping source = one file.
schema.py         the only contract the engine knows
integrity.py      sessions, stub bars, grid gaps, split-artefact flags
levels.py         PURE function of prior sessions → LevelSet    ← THE OPEN PLUG
fills.py          LevelSet + following bars → fills, with ambiguity flags
aggregate.py      60m → daily, for the resolution comparison
diagnostics.py    the comparison; performance deliberately obstructed
```

**Level generation and fill adjudication are separate on purpose.** 60-minute
data is required by the adjudicator, and bias hides in the adjudicator. Fused
together, a bad result cannot be attributed to either.

## Hard-coded rules (tests/test_fills.py pins all three)

1. **Adverse intra-bar ordering.** Two actionable levels inside one bar's
   `[low, high]` → resolve to the worst outcome and flag the bar. The flag
   matters more than the resolution: a high ambiguity rate makes a run
   uninterpretable regardless of what it reports.
2. **Asymmetric order types.** Entry is a stop-*limit* — gap past the limit and
   there is no fill, because a missed trade is not a loss. The protective stop
   is a plain stop — it fills on the gap, at the gap price, however bad, because
   an unfilled stop is just an unhedged 3x position.
3. **Gap fills are tagged, not averaged.** Averaging gap slippage into an
   overall mean hides the tail, and the tail is what ends accounts.

## Data status

| source | 60m depth | verdict |
|---|---|---|
| yfinance | 730 days | quarantined by construction — plumbing + structural diagnostics only |
| FirstRate | 2000– | intended production source; `adjustment` must be passed explicitly |
| synthetic | n/a | test fixture, no market content |

`Dataset.quarantined` has no parameter to disable it on the yfinance path. The
window excludes 2018Q4, 2020Q1 and 2022 — exactly the regimes that decide
whether a leveraged-ETF swing system is viable.

## Open items

- **Level generation rule.** `PlaceholderBreakout` is a shim: prior-session-high
  breakout, ATR-scaled band, stop at ATR multiple. Not reasoned about, not
  argued for, parameters picked to be roughly plausible. Replace before any
  claim.
- **`exclude_stubs`.** Unresolved. See below.
- **Broker capability.** Whether the broker supports stop / stop-limit orders on
  US equities, and what order durations are available, is still unverified. If
  stop orders are unavailable the whole design collapses. This gates everything
  downstream.
- **Execution falsifier.** SURFER's characteristic failure is execution, not
  signal. Pre-register something like: *mean deviation between realised fill and
  specified level exceeds X bp, or entry stop-limit fill rate falls below Y% →
  system void regardless of P&L.* Measurable in a few weeks of paper trading,
  before buying data.

## One assumption already falsified

The first version of `atr60()` excluded stub bars on the stated grounds that a
half-length bar has a smaller range, so leaving them in biases ATR downward.
`test_atr_excluding_stubs_is_not_lower_than_including_them` failed.

The 15:30–16:00 stub covers the closing auction and the last-hour volume ramp.
In the fixture its mean true range is **1.30× that of an average full 60-minute
bar** — it is the *widest* bar of the session, not the narrowest. Excluding it
*lowers* ATR.

So the direction of the bias is an empirical question about the instrument's
intraday volatility profile, not something reasoning settles. `exclude_stubs`
stays `True` only so the placeholder runs; `levels.stub_range_ratio()` measures
it, and `run_diagnostic.py` prints the measurement. Settle it on real TQQQ and
SOXL bars before fixing it in the pre-registration document.

A second, separate confound: excluding stubs shortens the bar sequence, so a
fixed `lookback_bars` window spans a different number of calendar sessions
between the two settings.

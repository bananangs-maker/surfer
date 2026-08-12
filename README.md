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
levels.py         entry candidates A-D, structural exit, ATR helpers
fills.py          LevelSet + bars -> fills; carries positions across sessions
aggregate.py      60m -> daily, for the resolution comparison
diagnostics.py    resolution comparison; purchase case
backtest.py       session mark-to-market, costs, buy-and-hold, DES-002 verdict
chart.py          annotated candlestick SVG, monochrome
board.py          today's signal (single engine) + candidate board
engine.py         SURFER-ENGINE-v1.0.0 - the unified rule
regimes.py        §2.2R regime split. REPORTING ONLY, never a trading input
windows.py        mechanical lock on the validation window
app.py            web viewer: / diagnostic, /levels, /compare, /backtest
report_template.html, backtest.html, levels_board.html, compare.html
run_diagnostic.py CLI
test_*.py         150 tests
SURFER-DES-001.md pre-registration, use 1 (ON HOLD)
SURFER-DES-002.md pre-registration, use 2 (ACTIVE)
```

## The validation window is locked in code

`SURFER-DES-002` §3.3 splits the data: candidates are chosen on sessions up to
2019-12-31, and everything after is out-of-sample. `windows.py` enforces it —
`validation_slice()` raises until `unlock_validation()` writes
`VALIDATION_UNLOCKED.json`, which is committed rather than ignored, so opening
the window is a timestamped record naming the candidate chosen.

The failure mode this guards is not dishonesty, it is one idle afternoon.
Looking once contaminates the window, and after that the purchased history
answers a weaker question than it was bought for.

Note that the current yfinance source (730 days) lies **entirely inside** the
validation window, so `/backtest` refuses real symbols until selection-window
data exists. The synthetic fixture stays available because it leaks nothing.

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


## Visual language

Monochrome and dark, after a chart-study reference. Dark mode inverts the
reference's tonal relationships rather than recolouring them: paper becomes
near-black, ink becomes bone, zone greys sit between. **No hue is introduced** —
a monochrome sheet cannot imply that one outcome is the good one. PASS is an
outlined chip, FAIL a filled one.

Direction is carried by fill (hollow up, solid down) and emphasis by stroke
weight. In dark mode a bright filled candle reads as "up" to most people, which
is the opposite of the convention, so the chart states `HOLLOW UP / SOLID DOWN`
in its footer rather than leaving it to be guessed.

`/levels` is a terminal, not a document: **one continuous surface**, no card
edges, no panel fills, minimal padding. The chart's own `paper` colour is `none`
so it blends into the page — a bordered panel reads as a figure pasted in, and the
reference is a trading terminal. Everything folds into collapsed `<details>`;
a test pins at least five folded sections with none open by default. The whole
page fits roughly one viewport.

**Pointer interaction.** A crosshair follows the cursor and **snaps to the nearest
bar** — a crosshair floating between bars invites reading a price no bar had. The
readout row above the chart switches to that bar's OHLC, change and timestamp, and
the right axis shows the price under the cursor. Bars ship as one JSON blob rather
than as attributes on 140 rects: one parse beats 140 DOM lookups per move. The
readout row is a sibling of the chart, not an overlay on it — as an overlay it
covered the indicator line and nothing movable inside the SVG could separate
them.

### Motion

Sequenced to be read in the order the system works — closed sessions, then the
levels derived from them, then the session being armed.

In the SVG: the entry band wipes in, candle wicks grow from their centres and
bodies rise behind them staggered 24ms apart, the session divider draws downward,
level hairlines wipe left to right, leader lines fade, right-rail price boxes
settle, numbered markers pop, and a single scan line crosses once. The
ambiguous-bar hatch drifts continuously and its highlight breathes.

On the page: prices count up and land on the **exact** rendered string rather than
a rounded reconstruction — a price that settled one tick off the truth would be
worse than no animation. Folded sections reveal on scroll via
IntersectionObserver. The badge takes one slow shimmer pass so the state reads as
current rather than as a screenshot. Rail cells, table rows, legend items and
summaries all have hover feedback whose only job is answering "did that
register?".

One thing is genuinely live rather than a snapshot: the staleness clock ticks
every 30 seconds, so an old tab cannot pass for a fresh one.

**All of it is defeasible.** `prefers-reduced-motion` collapses every animation,
transition, reveal and count-up to its final state — verified in a real browser at
120ms, not merely asserted in CSS. Sustained motion is genuinely sickening for
people with vestibular disorders, and this screen is meant to be opened every
morning. Tests assert the media query is present on all six pages, that every
declared SVG animation class is actually attached to an element (two were
declared and never applied), and that `animate=False` strips the keyframes
entirely.

`chart.py` draws real bars with a real `LevelSet` on them, generated from the
same data the diagnostic counts, so the illustration cannot drift from the
numbers printed beneath it. The abstract diagram it replaced could.

Two constraints are pinned by tests. The SVG contains no Hangul — Barlow has no
Hangul glyphs, so Korean inside the drawing falls back unpredictably; it lives in
the HTML legend instead, keyed to numbered markers. And the right-rail price
boxes are spaced apart, because trigger and limit often sit close enough that
their boxes and labels collided and both numbers became unreadable.

Wide Latin tracking applies to the wordmark and chart labels only. Applied to
Hangul it pulls syllable blocks apart, so Korean UI text uses normal spacing.


## Daily use: /levels

Once a day, before the US open, `/levels` shows what each of the four candidates
would arm for the next session: trigger, limit, fixed stop, and R.

**All four are shown and none is marked as chosen.** Selection belongs to
DES-002 §3.3 and happens on 2019-and-earlier data, which does not exist yet.
Displaying one candidate's levels every morning would let an unvalidated rule
slide into live use through familiarity, which is the exact path pre-registration
exists to block. Candidate D reproduces A's levels when its gate is open, and
reports the ATR percentile with its reason when it blocks.

There is no live quoting, by design. SURFER does not react to intraday bars; a
screen that updated during the session would put a person back in front of it at
03:00 KST, which is what the resting-order design exists to avoid.

**Lookahead guard.** If the feed's final session is still in progress, using it as
history would derive today's levels from bars that have not closed. `board.py`
detects this by bar count — seven for a regular session, four for an early close
— and drops the partial session, reporting that it did. The guard matters because
the failure is silent: levels built on an open session look entirely ordinary.

The page also emits a paste-able worksheet for paper execution, with
`actual_fill` and `filled_yn` left blank. Those columns are the input to the
DES-002 §8 execution falsifier, which is measurable now, without any purchased
history — and a failure there stops the data purchase.


## The engine

`SURFER-ENGINE-v1.0.0` — candidate D, unified per DES-002 §12.5.

    entry   prior-session-high breakout, gated off when the instrument's own
            60-minute ATR is at or above its 252-session 80th percentile
    exit    structural: prior session low - 0.25 ATR, ratcheted
    stop    fixed at entry; effective exit is the higher of the two
    cadence once daily, before the open, as resting orders

Chosen because on TQQQ over 730 sessions it was the only candidate to pass the
drawdown condition — halving A's drawdown (-58.4% → -28.7%) and turning its CAGR
positive (-13.3% → +4.0%) on an identical entry rule. A, B and C failed both.

**It is not validated.** The window that selected it has no decline and no
recovery, and a volatility gate's known failure mode is blocking entry during the
high-volatility early phase of a recovery — a cost that cannot appear in a sample
without one. `/levels` says this on every load.

### Why §2.2 was replaced

The original Calmar-multiple criterion turned out to be unsatisfiable: the only
way to pass the drawdown condition is to cut exposure, and cutting exposure in a
window where buy-and-hold compounds at 55% makes the Calmar multiple unreachable.
The two conditions contradicted each other. In a falling window the same
criterion inverts, because a negative baseline Calmar makes the multiple
meaningless.

`§2.2R` splits by regime instead: drawdown suppression is judged in DECLINE,
participation in RECOVERY, and the whole curve must simply not lose money. The
gate's benefit and its cost live in different regimes and cancel when averaged —
which is precisely why the 2024-2026 window made it look unambiguously good.

`regimes.py` labels sessions from the **benchmark** curve, and uses running peaks
and troughs, so a label depends on what happened afterwards. It is reporting
machinery only; a test asserts `engine.py` cannot import it.

### The split came back on its own

§3.3 was repealed to allow selection on the available window, then restored with
its boundary reversed: candidate D was chosen on 2024-08 onward, so everything
before that is untouched and is genuine out-of-sample. The cost of the repeal
turned out to be nothing — but only because the choice happened to be made on the
recent end.


## Data fetching

Yahoo rate-limits. A live `YFRateLimitError` on 2026-08-12 traced to a 30-minute
cache TTL on a system that decides **once per day** — the bars do not change
between two loads on the same morning, but every expiry sent another request, and
four pages opened a few times tripped the limiter.

    TTL          6 hours
    on refusal   retry twice with backoff (the limiter is per-window)
    then         serve the cached copy however old, and report its age
    beyond 72h   refuse rather than mislead

**A stale dataset beats no dataset.** Yesterday's levels are wrong but legible; a
traceback is neither. The page shows the cache age next to the staleness clock so
the compromise is visible rather than silent.

An unrelated failure is **not** relabelled as a rate limit. A delisted ticker
shown as "wait a few minutes" would bury its own cause, so only errors that
actually look like limits are retried; everything else propagates on the first
attempt.

This is one more reason the purchased history matters beyond §2.2R: FirstRate is a
file, so none of this applies to it.


## Robustness sweep — `/sweep`

256 parameter combinations, and **the maximum is deliberately not the answer.**
With no out-of-sample data left, the best cell of a large grid carries no
information: something always passes, and whether it passed because the rule is
right or because the grid was large cannot be told from the same sample.

The selection statistic was fixed in DES-002 §15.3 before any result was seen:

    worst-of-neighbourhood Calmar

A cell scores as the worst of itself and its immediate grid neighbours. Picking the
maximum picks noise; picking worst-of-neighbourhood picks a region that survives a
slightly wrong parameter — which is the property live use actually needs, since
nothing guarantees the parameters are right.

Reported in order: **pass rate first** (is there a robust region at all — 0% means
more searching cannot help, under 5% means any winner is noise), then the
distribution, then per-axis medians (an axis whose values barely differ does not
explain the result and tuning it is pointless), and only then the ranked
candidates. `no gate` sits in the grid as a point, so the gate's contribution shows
up inside the distribution rather than being assumed.

On the synthetic fixture the pass rate is **0.0%** and selection is blocked. The
best drawdown ratio across all 256 cells is 0.95 — barely shallower than
buy-and-hold, against a requirement of 0.50.

256 cells cannot finish inside one request on a 0.1-CPU instance, so the route
computes 14 per request and resumes on refresh. Crude, but it needs no task queue
and the partial distribution is already informative — **provided the prefix is
representative**, which it was not at first. `itertools.product` varies the last
axis fastest, so the first 56 cells all shared one trigger value; every top-ranked
candidate had that value and it read as a finding about the trigger. Evaluation
order is now shuffled under a fixed seed, so any prefix covers every axis value,
and the page reports per-axis coverage alongside progress.


## The ground, and what the loading screen is actually for

A seigaiha (青海波) pattern runs behind every page — concentric arcs on a
half-offset lattice, the traditional water motif, drifting one cell per 90 seconds.
Held at 8.5% opacity, `pointer-events:none`, `aria-hidden`, `z-index:0`. A ground
that competes with the chart is worse than none: the point of the terminal layout is
that the candles and the level lines are the only things asserting anything.

### Why the loading screen had to change

The first version only flashed *after* loading finished, which is the opposite of
useful. The reason is structural: the server does its slow work **before any HTML
exists**, so by the time a client-side script runs, the wait is already over.

The fix is to raise the overlay **on navigation** — on form submit and on internal
link clicks. The old document stays on screen while the server computes, so the
overlay is visible for exactly the interval that was previously blank. It releases
on `pageshow` and `popstate` unconditionally, not only on a cached restore: an
overlay left up over a perfectly good page is worse than no overlay, and a cached
restore keeps the old flag.

### The percentile series is not a resting order

`sf-level` carries a dashed stroke and a slow marching animation, which is right for
an **armed order** sitting in the market. The ATR percentile is a measurement, so it
uses its own class and is solid and still. A moving dashed line there would assert
something false about what the system is doing.

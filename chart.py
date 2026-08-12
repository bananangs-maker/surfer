"""Annotated candlestick SVG, monochrome.

Renders real bars with a real LevelSet drawn on them, in the annotation style of
a trading chart study: hairlines, shaded zones, boxed price labels on the right
rail, leader-line callouts.

Why this replaces the previous abstract diagram: ambiguity IS a chart-annotation
fact. "Two levels fall inside one bar" is something you see immediately on a
drawn bar and have to reason about in prose. The illustration is generated from
the same data the diagnostic counts, so it cannot drift from what is reported.

No colour. Direction is carried by fill (hollow up, solid down), emphasis by
stroke weight. That is a constraint borrowed from the reference and it suits the
subject: a monochrome chart cannot imply that one outcome is good.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from schema import EntryStyle, LevelSet

W, H = 900.0, 520.0
PAD_L, PAD_R, PAD_T, PAD_B = 96.0, 132.0, 64.0, 72.0

# The reference sheet is light; dark mode inverts the tonal relationships rather
# than recolouring them. Hollow still means up and solid still means down, so the
# only thing that changes is which end of the scale is paper.
THEMES = {
    "dark": {
        "paper": "#12141A", "ink": "#E9E7E2", "dim": "#8C8F98",
        "zone": "#252932", "grid": "#2A2E38", "hatch": "#E9E7E2",
        "up_fill": "#12141A",     # hollow
        "down_fill": "#E9E7E2",   # solid
    },
    "light": {
        "paper": "#FFFFFF", "ink": "#111111", "dim": "#8A8A88",
        "zone": "#D9D9D7", "grid": "#E4E4E2", "hatch": "#111111",
        "up_fill": "#FFFFFF", "down_fill": "#111111",
    },
}

# Animation: strokes draw themselves in, candles rise into place, right-rail
# boxes settle last. Sequenced so the eye reads the chart in the order the system
# does - closed sessions, then levels, then the session being armed.
_ANIM_CSS = """
<style>
  /* Sequenced to be read in the order the system works: the closed sessions
     first, then the levels derived from them, then the session being armed.
     Anything that repeats forever is kept below the threshold of distraction -
     this is an instrument panel, not a screensaver. */
  @keyframes sfRise   { from { opacity:0; transform: translateY(10px) } to { opacity:1; transform:none } }
  @keyframes sfFade   { from { opacity:0 } to { opacity:1 } }
  @keyframes sfWipe   { from { transform: scaleX(0) }  to { transform: scaleX(1) } }
  @keyframes sfGrow   { from { transform: scaleY(0) }  to { transform: scaleY(1) } }
  @keyframes sfPop    { 0% { opacity:0; transform: scale(.4) } 60% { transform: scale(1.12) } 100% { opacity:1; transform: scale(1) } }
  @keyframes sfPulse  { 0%,100% { opacity:.26 } 50% { opacity:.60 } }
  @keyframes sfScan   { 0% { opacity:0; transform: translateX(0) }
                        8% { opacity:.55 } 92% { opacity:.55 }
                        100% { opacity:0; transform: translateX(var(--sf-span)) } }
  @keyframes sfDrift  { to { transform: translate(6px, 6px) } }

  .sf-candle { animation: sfRise .40s cubic-bezier(.22,.7,.24,1) both }
  .sf-wick   { animation: sfGrow .34s cubic-bezier(.22,.7,.24,1) both;
               transform-box: fill-box; transform-origin: center }
  .sf-level  { animation: sfWipe .62s cubic-bezier(.3,.8,.2,1) both;
               transform-box: view-box; transform-origin: left center }
  .sf-lead   { animation: sfFade .3s ease both }
  .sf-box    { animation: sfRise .36s cubic-bezier(.22,.7,.24,1) both }
  .sf-num    { animation: sfPop .42s cubic-bezier(.3,1.5,.5,1) both;
               transform-box: fill-box; transform-origin: center }
  .sf-zone   { animation: sfWipe .78s cubic-bezier(.3,.8,.2,1) both;
               transform-box: view-box; transform-origin: left center }
  .sf-divide { animation: sfGrow .5s cubic-bezier(.3,.8,.2,1) both;
               transform-box: view-box; transform-origin: top }
  .sf-note   { animation: sfFade .5s ease both }
  .sf-live   { animation: sfPulse 2.6s ease-in-out infinite }
  .sf-hatch  { animation: sfDrift 3.4s linear infinite }
  .sf-scan   { animation: sfScan 1.5s cubic-bezier(.4,.1,.5,.9) both }

  @media (prefers-reduced-motion: reduce) {
    [class^="sf-"] {
      animation: none !important; opacity: 1 !important; transform: none !important;
    }
    .sf-scan { display: none !important }
  }
</style>
"""


@dataclass
class _Scale:
    lo: float
    hi: float

    def y(self, price: float) -> float:
        span = self.hi - self.lo or 1.0
        return PAD_T + (self.hi - price) / span * (H - PAD_T - PAD_B)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def annotated_session_svg(
    history_bars: pd.DataFrame,
    session_bars: pd.DataFrame,
    levels: LevelSet,
    ambiguous_bar_index: int | None = None,
    title: str = "SURFER | LEVEL SET",
    subtitle: str = "TIME FRAME | 60M",
    theme: str = "dark",
    animate: bool = True,
) -> str:
    """`history_bars` are the completed sessions the levels were derived from.

    They are drawn faintly to the left, because the point that keeps getting lost
    in prose is that every level comes from bars that had already closed.
    """
    bars = pd.concat([history_bars, session_bars], ignore_index=True)
    n = len(bars)
    if n == 0:
        return "<svg/>"

    prices = [
        float(bars["low"].min()), float(bars["high"].max()),
        levels.entry_trigger, levels.entry_limit, levels.initial_stop,
    ]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.08 or 1.0
    sc = _Scale(lo - pad, hi + pad)

    plot_w = W - PAD_L - PAD_R
    step = plot_w / max(n, 1)
    body_w = max(2.4, min(11.0, step * 0.56))
    n_hist = len(history_bars)

    def cx(i: int) -> float:
        return PAD_L + step * (i + 0.5)

    C = THEMES.get(theme, THEMES["dark"])
    A = _ANIM_CSS if animate else ""

    def cls(name: str, delay_ms: float = 0.0) -> str:
        if not animate:
            return ""
        d = f' style="animation-delay:{delay_ms:.0f}ms"' if delay_ms else ""
        return f' class="{name}"{d}'

    out: list[str] = [
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="{_esc(title)} — 완료된 세션에서 도출한 진입·손절 '
        f'레벨을 실제 60분봉에 표시한 주석 차트">',
        A,
        '<defs>'
        '<pattern id="hatch" width="6" height="6" patternTransform="rotate(45)" '
        'patternUnits="userSpaceOnUse" class="sf-hatch">'
        f'<line x1="0" y1="0" x2="0" y2="6" stroke="{C["hatch"]}" stroke-width="1" opacity=".22"/>'
        '</pattern>'
        f'<marker id="ar" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" '
        f'markerHeight="6" orient="auto"><path d="M0,1 L6,4 L0,7 z" fill="{C["ink"]}"/></marker>'
        '</defs>',
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="{C["paper"]}"/>',
    ]

    # title block
    out.append(
        f'<text x="{PAD_L - 60:.0f}" y="34" font-family="\'Barlow Condensed\',sans-serif" '
        f'font-size="17" font-weight="600" letter-spacing="3.4" fill="{C['ink']}">'
        f'{_esc(title)}</text>'
    )
    out.append(
        f'<text x="{W - PAD_R + 108:.0f}" y="34" text-anchor="end" '
        f'font-family="\'Barlow Condensed\',sans-serif" font-size="17" '
        f'font-weight="600" letter-spacing="3.4" fill="{C['ink']}">{_esc(subtitle)}</text>'
    )

    # boundary between completed sessions and the armed session
    if 0 < n_hist < n:
        bx = PAD_L + step * n_hist
        out.append(
            f'<line x1="{bx:.1f}" y1="{PAD_T - 8:.0f}" x2="{bx:.1f}" '
            f'y2="{H - PAD_B + 14:.0f}" stroke="{C['ink']}" stroke-width="1" '
            f'stroke-dasharray="2 4" opacity=".55"{cls("sf-divide", 520)}/>'
        )
        out.append(
            f'<text x="{bx + 10:.1f}" y="{PAD_T - 14:.0f}" '
            f'font-family="\'Barlow Condensed\',sans-serif" font-size="11.5" '
            f'letter-spacing="1.8" fill="{C['ink']}" opacity=".75">ARMED SESSION</text>'
        )
        out.append(
            f'<text x="{bx - 10:.1f}" y="{PAD_T - 14:.0f}" text-anchor="end" '
            f'font-family="\'Barlow Condensed\',sans-serif" font-size="11.5" '
            f'letter-spacing="1.8" fill="{C['ink']}" opacity=".45">CLOSED</text>'
        )

    # entry band, drawn as a zone rather than two lines
    y_trig, y_lim = sc.y(levels.entry_trigger), sc.y(levels.entry_limit)
    zy, zh = min(y_trig, y_lim), abs(y_lim - y_trig)
    out.append(
        f'<rect x="{PAD_L:.0f}" y="{zy:.1f}" width="{plot_w:.0f}" '
        f'height="{max(zh, 2):.1f}" fill="{C['zone']}" opacity=".9"'
        f'{cls("sf-zone", 200)}/>'
    )
    band = "ENTRY BAND (CEILING)" if levels.style is EntryStyle.BREAKOUT \
        else "ENTRY BAND (FLOOR)"
    out.append(
        f'<text x="{PAD_L + 8:.0f}" y="{zy + max(zh, 12) / 2 + 4:.1f}" '
        f'font-family="\'Barlow Condensed\',sans-serif" font-size="11.5" '
        f'letter-spacing="1.9" fill="{C['ink']}">{band}</text>'
    )

    # level hairlines + right-rail boxed prices.
    # Prices are stacked with a minimum gap: when trigger and limit sit close
    # together their boxes overlapped and both numbers became unreadable.
    used_y: list[float] = []

    def rail(price: float, label: str, num: int, weight: float = 1.2) -> None:
        y = sc.y(price)
        bh = 20.0
        by = y - bh / 2
        # Gap must clear the box AND the label drawn above it, or the label of
        # the lower box lands inside the upper one.
        gap = bh + 18
        for uy in sorted(used_y):
            if abs(by - uy) < gap:
                by = uy + gap
        used_y.append(by)

        out.append(
            f'<line x1="{PAD_L:.0f}" y1="{y:.1f}" x2="{W - PAD_R + 4:.0f}" '
            f'y2="{y:.1f}" stroke="{C["ink"]}" stroke-width="{weight}"'
            f'{cls("sf-level", 320 + num * 90)} stroke-dasharray="7 5"/>'
        )
        bx, bw = W - PAD_R + 12, 88.0
        if abs(by + bh / 2 - y) > 1:
            out.append(
                f'<line x1="{W - PAD_R + 4:.0f}" y1="{y:.1f}" x2="{bx:.0f}" '
                f'y2="{by + bh / 2:.1f}" stroke="{C['ink']}" stroke-width=".8" '
                f'opacity=".5"{cls("sf-lead", 640 + num * 110)}/>'
            )
        out.append(
            f'<rect x="{bx:.0f}" y="{by:.1f}" width="{bw:.0f}" height="{bh:.0f}" '
            f'fill="{C["paper"]}" stroke="{C["ink"]}" stroke-width="1"'
            f'{cls("sf-box", 620 + num * 110)}/>'
        )
        out.append(
            f'<text x="{bx + bw / 2:.0f}" y="{by + 14.2:.1f}" text-anchor="middle" '
            f'font-family="\'IBM Plex Mono\',monospace" font-size="11.5" '
            f'fill="{C['ink']}"{cls("sf-box", 700 + num * 110)}>{price:,.4f}</text>'
        )
        out.append(
            f'<text x="{bx + bw / 2:.0f}" y="{by - 5:.1f}" text-anchor="middle" '
            f'font-family="\'Barlow Condensed\',sans-serif" font-size="10" '
            f'letter-spacing="1.6" fill="{C['ink']}"'
            f'{cls("sf-box", 700 + num * 110)}>{_esc(label)}</text>'
        )
        # Markers live in a left rail outside the plot, where nothing can collide
        # with them. Earlier they sat inside and overlapped the zone label.
        _marker(PAD_L - 22, y, num)

    def _marker(x: float, y: float, num: int) -> None:
        d = 700 + num * 90
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="{C['paper']}" '
            f'stroke="{C['ink']}" stroke-width="1.2"{cls("sf-num", d)}/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{y + 3.6:.1f}" text-anchor="middle" '
            f'font-family="\'IBM Plex Mono\',monospace" font-size="10.5" '
            f'font-weight="600" fill="{C['ink']}"{cls("sf-num", d + 40)}>{num}</text>'
        )

    rail(levels.entry_limit, "LIMIT", 2)
    rail(levels.entry_trigger, "TRIGGER", 1, 1.6)
    rail(levels.initial_stop, "STOP", 3, 1.6)

    # candles
    for i in range(n):
        b = bars.iloc[i]
        o, h, l, c = (float(b["open"]), float(b["high"]),
                      float(b["low"]), float(b["close"]))
        x = cx(i)
        faint = i < n_hist
        op = ".38" if faint else "1"
        out.append(
            f'<line x1="{x:.1f}" y1="{sc.y(h):.1f}" x2="{x:.1f}" '
            f'y2="{sc.y(l):.1f}" stroke="{C["ink"]}" stroke-width="1" opacity="{op}"'
            f'{cls("sf-wick", 40 + i * 24)}/>'
        )
        top, bot = sc.y(max(o, c)), sc.y(min(o, c))
        fill = C["up_fill"] if c >= o else C["down_fill"]
        out.append(
            f'<rect x="{x - body_w / 2:.1f}" y="{top:.1f}" width="{body_w:.1f}" '
            f'height="{max(bot - top, 1.4):.1f}" fill="{fill}" stroke="{C["ink"]}" '
            f'stroke-width="1" opacity="{op}"{cls("sf-candle", 70 + i * 24)}/>'
        )

    # the ambiguous bar, if one was supplied
    if ambiguous_bar_index is not None and 0 <= ambiguous_bar_index < len(session_bars):
        gi = n_hist + ambiguous_bar_index
        b = session_bars.iloc[ambiguous_bar_index]
        x = cx(gi)
        yh, yl = sc.y(float(b["high"])), sc.y(float(b["low"]))
        out.append(
            f'<rect x="{x - body_w * 1.5:.1f}" y="{yh - 6:.1f}" '
            f'width="{body_w * 3:.1f}" height="{yl - yh + 12:.1f}" '
            f'fill="url(#hatch)" stroke="{C["ink"]}" stroke-width="1.6"'
            f'{cls("sf-live")}/>'
        )
        ax, ay = min(x + 30, W - PAD_R - 168), max(yh - 34, PAD_T + 10)
        out.append(
            f'<line x1="{x + body_w * 1.5:.1f}" y1="{yh - 4:.1f}" x2="{ax - 8:.1f}" '
            f'y2="{ay:.1f}" stroke="{C['ink']}" stroke-width="1" marker-end="url(#ar)"/>'
        )
        _marker(ax + 8, ay, 4)
        out.append(
            f'<text x="{ax + 22:.0f}" y="{ay + 4:.0f}" '
            f'font-family="\'Barlow Condensed\',sans-serif" font-size="11.5" '
            f'letter-spacing="1.9" font-weight="600" fill="{C['ink']}">AMBIGUOUS BAR</text>'
        )

    # baseline rail
    out.append(
        f'<line x1="{PAD_L:.0f}" y1="{H - PAD_B + 14:.0f}" x2="{W - PAD_R + 4:.0f}" '
        f'y2="{H - PAD_B + 14:.0f}" stroke="{C['ink']}" stroke-width="1" opacity=".35"/>'
    )
    if len(session_bars):
        d = session_bars["session_date"].iloc[0]
        out.append(
            f'<text x="{PAD_L:.0f}" y="{H - PAD_B + 34:.0f}" '
            f'font-family="\'IBM Plex Mono\',monospace" font-size="11" '
            f'fill="{C['dim']}">{_esc(d)}</text>'
        )
    out.append(
        f'<text x="{W - PAD_R + 4:.0f}" y="{H - PAD_B + 34:.0f}" text-anchor="end" '
        f'font-family="\'Barlow\',sans-serif" font-size="10.5" font-style="italic" '
        f'fill="{C["dim"]}">HOLLOW UP / SOLID DOWN &#183; '
        f'DERIVED FROM CLOSED SESSIONS</text>'
    )
    if animate:
        span = W - PAD_R + 4 - PAD_L
        out.append(
            f'<g class="sf-scan" style="--sf-span:{span:.0f}px; animation-delay:1150ms">'
            f'<rect x="{PAD_L:.0f}" y="{PAD_T - 10:.0f}" width="1.4" '
            f'height="{H - PAD_T - PAD_B + 24:.0f}" fill="{C["ink"]}"/></g>'
        )
    out.append("</svg>")
    return "".join(out)


def pick_example(ds, generator, prefer_ambiguous: bool = True):
    """Find a session worth drawing: an ambiguous one if there is one.

    Returns (history_bars, session_bars, levels, ambiguous_index) or None.
    """
    from fills import adjudicate_session

    sessions = ds.sessions
    by = {
        d: g.sort_values("ts").reset_index(drop=True)
        for d, g in ds.bars.groupby("session_date", sort=True)
    }
    window = getattr(generator, "lookback_sessions", 30)
    fallback = None

    for i, sd in enumerate(sessions):
        hist = [by[s] for s in sessions[max(0, i - window):i]]
        lv = generator(hist, sd)
        if lv is None:
            continue
        bars = by[sd]
        out = adjudicate_session(lv, bars)
        ctx = pd.concat([by[s] for s in sessions[max(0, i - 2):i]], ignore_index=True) \
            if i >= 1 else bars.iloc[:0]

        amb_idx = None
        for j in range(len(bars)):
            b = bars.iloc[j]
            inside = sum(
                1 for p in (lv.entry_trigger, lv.initial_stop)
                if float(b["low"]) <= p <= float(b["high"])
            )
            if inside >= 2:
                amb_idx = j
                break
        if amb_idx is not None:
            return ctx, bars, lv, amb_idx
        if fallback is None and out.entry is not None:
            fallback = (ctx, bars, lv, None)
        if not prefer_ambiguous and fallback:
            return fallback
    return fallback


# ============================================================
# TERMINAL RENDERER
#
# After a mobile exchange terminal: a dense readout row, a price panel with a
# right-hand axis and a boxed callout, then stacked sub-panels below.
#
# The sub-panels are not borrowed decoration. Where such a terminal puts RSI, this
# puts the gate's OWN INPUT - the ATR percentile - drawn against the threshold
# that governs it. Where it puts a MACD histogram, this puts the gate's resulting
# state, session by session. "Gated" as a word explains nothing; 94 against a line
# at 80 explains it immediately.
# ============================================================

TW, TH = 400.0, 566.0
T_AXIS = 56.0          # right-hand price gutter
T_L = 6.0
_P1 = (34.0, 330.0)    # price panel: top, bottom
_P2 = (376.0, 452.0)   # percentile panel
_P3 = (466.0, 536.0)   # gate-state panel


def terminal_svg(
    price_bars: pd.DataFrame,
    n_armed_bars: int,
    levels: LevelSet | None,
    series,
    symbol: str,
    state: str,
    atr60: float | None,
    percentile: float | None,
    theme: str = "dark",
    animate: bool = True,
) -> str:
    """All three panels share ONE x-axis.

    The first version drew four sessions of 60-minute bars above ninety sessions
    of percentile history, with a session divider running through all of it. The
    divider implied an alignment that did not exist, which is worse than showing
    less: a reader would place the gate's state against the wrong bars. Callers
    now pass a price frame and a percentile series covering the SAME sessions.

    `n_armed_bars` is how many bars at the right belong to the session being
    armed; everything before it is closed history and is drawn faint.
    """
    C = THEMES.get(theme, THEMES["dark"])
    A = _ANIM_CSS if animate else ""

    def cls(name: str, delay: float = 0.0) -> str:
        if not animate:
            return ""
        d = f' style="animation-delay:{delay:.0f}ms"' if delay else ""
        return f' class="{name}"{d}'

    bars = price_bars
    if len(bars) == 0:
        return "<svg/>"

    plot_r = TW - T_AXIS
    plot_w = plot_r - T_L
    n = len(bars)
    step = plot_w / max(n, 1)
    bw = max(1.6, min(7.0, step * 0.6))
    n_hist = max(0, n - n_armed_bars)

    prices = [float(bars["low"].min()), float(bars["high"].max())]
    if levels is not None:
        prices += [levels.entry_trigger, levels.entry_limit, levels.initial_stop]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.10 or 1.0
    lo, hi = lo - pad, hi + pad

    def py(p: float) -> float:
        return _P1[0] + (hi - p) / (hi - lo) * (_P1[1] - _P1[0])

    def cx(i: int) -> float:
        return T_L + step * (i + 0.5)

    o: list[str] = [
        f'<svg viewBox="0 0 {TW:.0f} {TH:.0f}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="{_esc(symbol)} 60분봉 터미널 — 가격 패널, '
        f'ATR 백분위 패널, 게이트 상태 패널">',
        A,
        f'<rect x="0" y="0" width="{TW}" height="{TH}" fill="{C["paper"]}"/>',
    ]

    def txt(x, y, s, size=7.0, fill=None, anchor="start", weight=400,
            mono=True, extra=""):
        fam = ("'IBM Plex Mono',monospace" if mono
               else "Inter,system-ui,sans-serif")
        o.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'font-family="{fam}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill or C["ink"]}"{extra}>{_esc(s)}</text>'
        )

    # ---- readout row (the MA-row analogue) --------------------------------
    row = [
        ("STATE", state.upper()),
        ("ATR60", f"{atr60:.4f}" if atr60 else "—"),
        ("PCTL", f"{percentile:.0f}" if percentile is not None else "—"),
        ("GATE", f"<{series.threshold:.0f}"),
    ]
    if levels is not None:
        row.append(("R", f"{levels.entry_trigger - levels.initial_stop:.4f}"))
    x = T_L
    for i, (k, v) in enumerate(row):
        txt(x, 12, k, 6.4, C["dim"], extra=cls("sf-note", 60 + i * 45))
        txt(x, 22, v, 8.2, C["ink"], weight=600, extra=cls("sf-note", 90 + i * 45))
        x += max(52.0, len(v) * 5.0 + 20.0)

    # ---- price panel ------------------------------------------------------
    o.append(
        f'<line x1="{T_L}" y1="{_P1[1]:.0f}" x2="{plot_r:.0f}" y2="{_P1[1]:.0f}" '
        f'stroke="{C["grid"]}" stroke-width=".7"/>'
    )
    for f in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = hi - (hi - lo) * f
        y = py(p)
        o.append(
            f'<line x1="{T_L}" y1="{y:.1f}" x2="{plot_r:.0f}" y2="{y:.1f}" '
            f'stroke="{C["grid"]}" stroke-width=".6" stroke-dasharray="1 3"/>'
        )
        txt(plot_r + 5, y + 2.6, f"{p:,.2f}", 6.6, C["dim"])

    if n_hist and n_hist < n:
        bx = T_L + step * n_hist
        o.append(
            f'<line x1="{bx:.1f}" y1="{_P1[0] - 4:.0f}" x2="{bx:.1f}" '
            f'y2="{_P3[1]:.0f}" stroke="{C["ink"]}" stroke-width=".7" '
            f'stroke-dasharray="1 4" opacity=".5"{cls("sf-divide", 480)}/>'
        )

    if levels is not None:
        yt, yl = py(levels.entry_trigger), py(levels.entry_limit)
        o.append(
            f'<rect x="{T_L}" y="{min(yt, yl):.1f}" width="{plot_w:.1f}" '
            f'height="{max(abs(yl - yt), 1.4):.1f}" fill="{C["zone"]}" '
            f'opacity=".85"{cls("sf-zone", 180)}/>'
        )
        for k, (p, lab) in enumerate((
            (levels.entry_limit, "LMT"),
            (levels.entry_trigger, "TRG"),
            (levels.initial_stop, "STP"),
        )):
            y = py(p)
            o.append(
                f'<line x1="{T_L}" y1="{y:.1f}" x2="{plot_r:.0f}" y2="{y:.1f}" '
                f'stroke="{C["ink"]}" stroke-width=".9" stroke-dasharray="5 4"'
                f'{cls("sf-level", 300 + k * 80)}/>'
            )
            # boxed callout on the axis, as the terminal does for last price
            solid = lab == "TRG"
            o.append(
                f'<rect x="{plot_r + 1:.0f}" y="{y - 6:.1f}" width="{T_AXIS - 3:.0f}" '
                f'height="12" fill="{C["ink"] if solid else C["paper"]}" '
                f'stroke="{C["ink"]}" stroke-width=".8"{cls("sf-box", 620 + k * 90)}/>'
            )
            txt(plot_r + 4, y + 2.6, f"{p:,.2f}", 6.8,
                C["paper"] if solid else C["ink"], weight=600,
                extra=cls("sf-box", 660 + k * 90))
            txt(plot_r + T_AXIS - 4, y - 8.4, lab, 5.6, C["dim"], anchor="end",
                extra=cls("sf-box", 660 + k * 90))

    for i in range(n):
        b = bars.iloc[i]
        oo, hh, ll, cc = (float(b["open"]), float(b["high"]),
                          float(b["low"]), float(b["close"]))
        cxx = cx(i)
        op = ".34" if i < n_hist else "1"
        o.append(
            f'<line x1="{cxx:.1f}" y1="{py(hh):.1f}" x2="{cxx:.1f}" '
            f'y2="{py(ll):.1f}" stroke="{C["ink"]}" stroke-width=".8" '
            f'opacity="{op}"{cls("sf-wick", 30 + i * 7)}/>'
        )
        top, bot = py(max(oo, cc)), py(min(oo, cc))
        o.append(
            f'<rect x="{cxx - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
            f'height="{max(bot - top, 1.0):.1f}" '
            f'fill="{C["up_fill"] if cc >= oo else C["down_fill"]}" '
            f'stroke="{C["ink"]}" stroke-width=".8" opacity="{op}"'
            f'{cls("sf-candle", 50 + i * 7)}/>'
        )

    txt(T_L, _P1[1] + 12,
        f"{bars['session_date'].iloc[0]} → {bars['session_date'].iloc[-1]}",
        6.4, C["dim"])
    txt(plot_r, _P1[1] + 12,
        f"60M · {len(set(bars['session_date']))} SESSIONS", 6.4, C["dim"],
        anchor="end")

    # ---- sub-panel: ATR percentile against its threshold -----------------
    def panel(top, bot, label, sub=""):
        o.append(
            f'<line x1="{T_L}" y1="{top - 10:.0f}" x2="{plot_r:.0f}" '
            f'y2="{top - 10:.0f}" stroke="{C["grid"]}" stroke-width=".7"/>'
        )
        txt(T_L, top - 2, label, 6.4, C["dim"], weight=600)
        if sub:
            txt(T_L + len(label) * 4.4 + 8, top - 2, sub, 6.4, C["dim"])

    pcts = [p for p in series.percentiles if p is not None]
    panel(_P2[0], _P2[1], "ATR PCTL(252)",
          f"NOW {percentile:.0f}" if percentile is not None else "")
    m = len(series.percentiles)
    sw = plot_w / max(m, 1)

    def qy(v: float) -> float:
        return _P2[0] + (100.0 - v) / 100.0 * (_P2[1] - _P2[0])

    ty = qy(series.threshold)
    o.append(
        f'<rect x="{T_L}" y="{_P2[0]:.1f}" width="{plot_w:.1f}" '
        f'height="{ty - _P2[0]:.1f}" fill="{C["zone"]}" opacity=".55"'
        f'{cls("sf-zone", 700)}/>'
    )
    o.append(
        f'<line x1="{T_L}" y1="{ty:.1f}" x2="{plot_r:.0f}" y2="{ty:.1f}" '
        f'stroke="{C["ink"]}" stroke-width=".9" stroke-dasharray="4 3"'
        f'{cls("sf-level", 760)}/>'
    )
    txt(plot_r + 5, ty + 2.4, f"{series.threshold:.0f}", 6.6, C["ink"], weight=600)
    txt(plot_r + 5, qy(0) + 2.4, "0", 6.4, C["dim"])
    txt(plot_r + 5, qy(100) + 2.4, "100", 6.4, C["dim"])

    pts = [
        f"{T_L + sw * (i + 0.5):.1f},{qy(p):.1f}"
        for i, p in enumerate(series.percentiles) if p is not None
    ]
    if len(pts) > 1:
        o.append(
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="{C["ink"]}" '
            f'stroke-width="1.1"{cls("sf-level", 820)}/>'
        )

    # ---- sub-panel: gate state, session by session -----------------------
    n_blocked = sum(1 for g in series.gate_open if g is False)
    panel(_P3[0], _P3[1], "GATE STATE",
          f"BLOCKED {n_blocked}/{len(series.gate_open)}")
    mid = (_P3[0] + _P3[1]) / 2
    o.append(
        f'<line x1="{T_L}" y1="{mid:.1f}" x2="{plot_r:.0f}" y2="{mid:.1f}" '
        f'stroke="{C["grid"]}" stroke-width=".6"/>'
    )
    for i, g in enumerate(series.gate_open):
        if g is None:
            continue
        x0 = T_L + sw * i + sw * 0.15
        w = max(sw * 0.7, 0.8)
        h = (_P3[1] - mid) * 0.82
        if g:      # open: hollow bar below the line
            o.append(
                f'<rect x="{x0:.1f}" y="{mid:.1f}" width="{w:.1f}" '
                f'height="{h:.1f}" fill="none" stroke="{C["ink"]}" '
                f'stroke-width=".7" opacity=".8"{cls("sf-candle", 880 + i * 4)}/>'
            )
        else:      # blocked: solid bar above it
            o.append(
                f'<rect x="{x0:.1f}" y="{mid - h:.1f}" width="{w:.1f}" '
                f'height="{h:.1f}" fill="{C["ink"]}"'
                f'{cls("sf-candle", 880 + i * 4)}/>'
            )
    txt(plot_r + 5, mid - 4, "BLK", 5.8, C["dim"])
    txt(plot_r + 5, mid + 9, "OPEN", 5.8, C["dim"])
    txt(T_L, _P3[1] + 12,
        f"{series.dates[0]} → {series.dates[-1]}" if series.dates else "",
        6.2, C["dim"])
    txt(plot_r, _P3[1] + 12, "ALIGNED", 6.2, C["dim"], anchor="end")

    if animate:
        span = plot_r - T_L
        o.append(
            f'<g class="sf-scan" style="--sf-span:{span:.0f}px; animation-delay:1250ms">'
            f'<rect x="{T_L}" y="{_P1[0] - 6:.0f}" width="1" '
            f'height="{_P3[1] - _P1[0] + 12:.0f}" fill="{C["ink"]}"/></g>'
        )
    o.append("</svg>")
    return "".join(o)

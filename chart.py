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
  @keyframes sfDraw { to { stroke-dashoffset: 0 } }
  @keyframes sfRise { from { opacity:0; transform: translateY(9px) } to { opacity:1; transform:none } }
  @keyframes sfFade { from { opacity:0 } to { opacity:1 } }
  @keyframes sfPulse { 0%,100% { opacity:.30 } 50% { opacity:.62 } }
  .sf-candle { animation: sfRise .42s cubic-bezier(.22,.7,.24,1) both }
  .sf-level  { stroke-dasharray: 7 5; animation: sfFade .5s ease both }
  .sf-sweep  { animation: sfDraw 1.05s cubic-bezier(.35,.9,.2,1) both }
  .sf-box    { animation: sfRise .38s cubic-bezier(.22,.7,.24,1) both }
  .sf-note   { animation: sfFade .55s ease both }
  .sf-zone   { animation: sfFade .7s ease both }
  .sf-live   { animation: sfPulse 2.6s ease-in-out infinite }
  @media (prefers-reduced-motion: reduce) {
    .sf-candle,.sf-level,.sf-sweep,.sf-box,.sf-note,.sf-zone,.sf-live {
      animation: none !important; stroke-dashoffset: 0 !important; opacity: 1 !important;
    }
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
        'patternUnits="userSpaceOnUse">'
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
            f'stroke-dasharray="2 4" opacity=".55"/>'
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
        f'height="{max(zh, 2):.1f}" fill="{C['zone']}" opacity=".9"/>'
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
                f'y2="{by + bh / 2:.1f}" stroke="{C['ink']}" stroke-width=".8" opacity=".5"/>'
            )
        out.append(
            f'<rect x="{bx:.0f}" y="{by:.1f}" width="{bw:.0f}" height="{bh:.0f}" '
            f'fill="{C["paper"]}" stroke="{C["ink"]}" stroke-width="1"'
            f'{cls("sf-box", 620 + num * 110)}/>'
        )
        out.append(
            f'<text x="{bx + bw / 2:.0f}" y="{by + 14.2:.1f}" text-anchor="middle" '
            f'font-family="\'IBM Plex Mono\',monospace" font-size="11.5" '
            f'fill="{C['ink']}">{price:,.4f}</text>'
        )
        out.append(
            f'<text x="{bx + bw / 2:.0f}" y="{by - 5:.1f}" text-anchor="middle" '
            f'font-family="\'Barlow Condensed\',sans-serif" font-size="10" '
            f'letter-spacing="1.6" fill="{C['ink']}">{_esc(label)}</text>'
        )
        # Markers live in a left rail outside the plot, where nothing can collide
        # with them. Earlier they sat inside and overlapped the zone label.
        _marker(PAD_L - 22, y, num)

    def _marker(x: float, y: float, num: int) -> None:
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="{C['paper']}" '
            f'stroke="{C['ink']}" stroke-width="1.2"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{y + 3.6:.1f}" text-anchor="middle" '
            f'font-family="\'IBM Plex Mono\',monospace" font-size="10.5" '
            f'font-weight="600" fill="{C['ink']}">{num}</text>'
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
            f'{cls("sf-candle", 30 + i * 26)}/>'
        )
        top, bot = sc.y(max(o, c)), sc.y(min(o, c))
        fill = C["up_fill"] if c >= o else C["down_fill"]
        out.append(
            f'<rect x="{x - body_w / 2:.1f}" y="{top:.1f}" width="{body_w:.1f}" '
            f'height="{max(bot - top, 1.4):.1f}" fill="{fill}" stroke="{C["ink"]}" '
            f'stroke-width="1" opacity="{op}"{cls("sf-candle", 30 + i * 26)}/>'
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

"""SURFER mark and loading screen.

The mark is built as a kamon (家紋): enclosed in a ring, three-fold rotationally
symmetric, drawn from circular arcs only, and legible as a silhouette at 16px.
Traditional wave crests (波) are a standing motif, and the 三つ盛り arrangement -
three of a thing set inside a ring - is one of the commonest constructions.

Geometry is generated rather than hand-drawn so the symmetry is exact: one crest
is described once and placed three times at 120 degrees. Kamon are constructed,
not sketched, and an eyeballed rotation reads as a mistake at small sizes.
"""

from __future__ import annotations

import math

# Canonical 100x100 box, centred at (50, 50).
CX = CY = 50.0
R_OUT = 46.0        # outer ring
R_IN = 41.5         # inner ring
FAN_R = 19.0        # distance from centre to each fan's base
FAN_SPAN = 16.5     # radius of the outermost arc in a fan
FAN_ROT = 60        # rotational offset: gives the three fans a pinwheel turn

# Detail levels. Three nested arcs per fan turn to mush below about 32px, so the
# mark has a compact cut with fewer, heavier strokes - standard practice for a
# favicon against a wordmark, and the reason a kamon on a 16px crest is never the
# same drawing as the one on a banner.
DETAIL = {
    "full":    dict(rings=3, sw=3.4, span=16.5, fan_r=19.0, ring_sw=3.2),
    "compact": dict(rings=1, sw=5.6, span=17.0, fan_r=20.0, ring_sw=4.6),
}


def _fan(r_outer: float, rings: int = 3, sw: float = 2.6,
         ink: str = "currentColor") -> str:
    """One seigaiha (青海波) fan: nested half-arcs opening upward.

    The first attempt drew each crest as a filled hook, which rendered as a comma
    rather than a wave. Seigaiha - concentric arcs - is the standing Japanese wave
    motif and is unmistakable at any size, because the reading comes from the
    repetition rather than from the silhouette of one shape.

    Arcs only, as the idiom requires.
    """
    out = []
    step = r_outer / rings
    for k in range(rings):
        r = r_outer - k * step
        out.append(
            f'<path d="M {-r:.2f} 0 A {r:.2f} {r:.2f} 0 0 1 {r:.2f} 0" '
            f'fill="none" stroke="{ink}" stroke-width="{sw:.2f}" '
            f'stroke-linecap="round"/>'
        )
    return "".join(out)


def mark_svg(
    size: int = 100,
    ink: str = "currentColor",
    stroke_only: bool = False,
    animate: bool = False,
    idpfx: str = "sf",
    detail: str = "auto",
) -> str:
    """The mark. `detail="auto"` picks the compact cut below 32px."""
    if detail == "auto":
        detail = "compact" if size < 32 else "full"
    D = DETAIL[detail]
    sw = 2.2 if stroke_only else D["sw"]
    parts = [
        f'<svg viewBox="0 0 100 100" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="서퍼 문장 — 원 안에 세 개의 파도">'
    ]
    if animate:
        parts.append(
            "<style>"
            f"#{idpfx}-ring,#{idpfx}-ring2{{stroke-dasharray:300;stroke-dashoffset:300;"
            "animation:sfRingDraw 1.15s cubic-bezier(.3,.8,.2,1) forwards}"
            f"#{idpfx}-ring2{{animation-delay:.12s}}"
            f".{idpfx}-crest > g{{opacity:0;transform-box:fill-box;"
            "transform-origin:center;"
            "animation:sfCrestIn .58s cubic-bezier(.25,1.3,.4,1) forwards}"
            "@keyframes sfRingDraw{to{stroke-dashoffset:0}}"
            "@keyframes sfCrestIn{from{opacity:0;transform:scale(.55)}"
            "to{opacity:1;transform:scale(1)}}"
            "@media (prefers-reduced-motion:reduce){"
            f"#{idpfx}-ring,#{idpfx}-ring2,.{idpfx}-crest > g{{animation:none;"
            "stroke-dashoffset:0;opacity:1;transform:none}}"
            "</style>"
        )
    # Double ring: the standard kamon enclosure.
    parts.append(
        f'<circle id="{idpfx}-ring" cx="{CX}" cy="{CY}" r="{R_OUT}" fill="none" '
        f'stroke="{ink}" stroke-width="{D["ring_sw"]}"/>'
    )
    if detail == "full":
        # The thin inner ring is the first thing to disappear at small sizes.
        parts.append(
            f'<circle id="{idpfx}-ring2" cx="{CX}" cy="{CY}" r="{R_IN}" fill="none" '
            f'stroke="{ink}" stroke-width="1.1"/>'
        )
    # Three fans at 120 degrees: the 三つ盛り arrangement. Each opens outward from
    # the centre, so the arcs read as swells running around the ring.
    for i, ang in enumerate((0.0, 120.0, 240.0)):
        rad = math.radians(ang - 90.0)
        x = CX + D["fan_r"] * math.cos(rad)
        y = CY + D["fan_r"] * math.sin(rad)
        delay = f' style="animation-delay:{0.5 + i * 0.11:.2f}s"' if animate else ""
        # Two nested groups on purpose: the outer one positions, the inner one
        # animates. A single group cannot do both - `transform-box: fill-box` plus
        # a keyframe transform overwrote the translate, and all three fans
        # collapsed onto the origin while still fading in correctly.
        parts.append(
            f'<g class="{idpfx}-crest" transform="translate({x:.2f} {y:.2f}) '
            f'rotate({ang + FAN_ROT:.0f})">'
            f'<g{delay}>{_fan(D["span"], D["rings"], sw, ink)}</g></g>'
        )
    parts.append("</svg>")
    return "".join(parts)


def loading_html(subtitle: str = "", ink: str = "#E9E7E2",
                 paper: str = "#0B0D11") -> str:
    """Full-bleed loading screen: the mark draws itself, then the wordmark opens.

    Sequenced rather than simultaneous, because a mark and a word appearing at once
    read as a single flash and nothing is legible. Total run is about 1.9s, which
    is roughly what a Render cold start plus a Yahoo fetch costs - long enough to
    be worth filling, short enough that it never becomes the thing you wait for.

    Fades out on `data-ready`, and gives up on its own after 12 seconds rather
    than trapping the page behind a spinner that outlived its request.
    """
    mark = mark_svg(112, ink=ink, animate=True, idpfx="ld", detail="full")
    return f"""<div class="sf-load" id="sf-load" role="status" aria-live="polite">
  <div class="sf-load-in">
    <div class="sf-load-mark">{mark}</div>
    <div class="sf-load-word">SURFER</div>
    <div class="sf-load-sub">{subtitle}</div>
    <div class="sf-load-bar"><i></i></div>
  </div>
</div>
<style>
  .sf-load{{
    position:fixed; inset:0; z-index:90; background:{paper}; color:{ink};
    display:flex; align-items:center; justify-content:center;
    transition:opacity .5s cubic-bezier(.22,.7,.24,1), visibility .5s;
  }}
  .sf-load[data-ready]{{opacity:0; visibility:hidden}}
  .sf-load-in{{display:flex; flex-direction:column; align-items:center; gap:14px}}
  .sf-load-mark{{opacity:.96}}
  .sf-load-word{{
    font-family:'Barlow Condensed',sans-serif; font-weight:700; font-size:26px;
    text-transform:uppercase; letter-spacing:.06em; opacity:0;
    animation:sfWord .9s cubic-bezier(.22,.7,.24,1) 1.02s forwards;
  }}
  .sf-load-sub{{
    font-family:'IBM Plex Mono',monospace; font-size:10px; color:#7E8189;
    opacity:0; animation:sfWord .7s ease 1.35s forwards; letter-spacing:.04em;
  }}
  .sf-load-bar{{
    width:132px; height:1px; background:rgba(233,231,226,.16); overflow:hidden;
    opacity:0; animation:sfWord .5s ease 1.45s forwards;
  }}
  .sf-load-bar i{{
    display:block; width:38%; height:100%; background:{ink};
    animation:sfSweep 1.35s cubic-bezier(.5,.05,.5,.95) 1.5s infinite;
  }}
  @keyframes sfWord{{
    from{{opacity:0; letter-spacing:.30em; transform:translateY(5px)}}
    to{{opacity:1; letter-spacing:.06em; transform:none}}
  }}
  @keyframes sfSweep{{from{{transform:translateX(-110%)}} to{{transform:translateX(300%)}}}}
  @media (prefers-reduced-motion:reduce){{
    .sf-load-word,.sf-load-sub,.sf-load-bar{{animation:none; opacity:1;
      letter-spacing:.06em; transform:none}}
    .sf-load-bar i{{animation:none; width:100%}}
  }}
</style>
<script>
(function(){{
  var el = document.getElementById("sf-load");
  if (!el) return;
  var stay = false;
  function show(){{ el.removeAttribute("data-ready"); }}
  function done(){{ if (!stay) el.setAttribute("data-ready",""); }}

  // Entrance: the page arrives fully rendered, so this only covers the font swap
  // and the chart's own entrance animation.
  if (document.readyState === "complete") setTimeout(done, 1700);
  else window.addEventListener("load", function(){{ setTimeout(done, 1700); }});
  setTimeout(done, 12000);   // failsafe: a screen that outlives its request is a bug

  // Departure: this is the part that matters. The server does the slow work BEFORE
  // any HTML exists, so a purely client-side screen can never cover it - by the
  // time the script runs, the wait is already over. Raising the screen on
  // navigation instead covers exactly that gap: the old document stays on screen
  // while the server computes, so the overlay stays visible until the new page
  // paints.
  function hold(){{
    stay = true;
    show();
    var sub = el.querySelector(".sf-load-sub");
    if (sub) sub.textContent = "계산 중 — 서버 응답을 기다립니다";
  }}
  document.addEventListener("submit", function(e){{
    if (e.target && e.target.tagName === "FORM") hold();
  }}, true);
  document.addEventListener("click", function(e){{
    var a = e.target && e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    var href = a.getAttribute("href") || "";
    if (href.charAt(0) === "#" || a.target === "_blank") return;
    if (a.host && a.host !== location.host) return;
    hold();
  }}, true);
  // Browsers restore a cached page on Back; the overlay must not be stuck up.
  // Always release on pageshow, not only when the page came from cache. Back
  // navigation that re-renders still runs this script fresh, but a cached restore
  // keeps the old `stay` flag - and an overlay left up over a perfectly good page
  // is worse than no overlay at all.
  window.addEventListener("pageshow", function(){{
    stay = false;
    setTimeout(function(){{ el.setAttribute("data-ready",""); }}, 400);
  }});
  // Same for a cancelled navigation: the browser fires this when it comes back.
  window.addEventListener("popstate", function(){{
    stay = false; el.setAttribute("data-ready","");
  }});
}})();
</script>"""

def seigaiha_bg(
    ink: str = "#E9E7E2",
    opacity: float = 0.085,
    cell: float = 46.0,
    rings: int = 4,
    drift_seconds: float = 90.0,
) -> str:
    """Full-bleed seigaiha (青海波) ground.

    The traditional construction: circles of radius w/2 on a half-offset brick
    lattice, each drawn as concentric arcs, so the overlaps read as scales of
    water. Two rows per tile supply the offset and the tile then repeats seamlessly.

    Held at ~5% opacity and pinned behind everything. A ground that competes with
    the chart is worse than none: the point of the terminal layout is that the
    candles and the level lines are the only things asserting anything. The drift is
    one cell per 90 seconds - slow enough to register as the surface being alive
    rather than as motion.
    """
    r = cell / 2.0
    h = cell / 2.0
    step = r / rings
    arcs = []
    for row_y in (0.0, h):
        offset = 0.0 if row_y == 0.0 else r
        for cx in (offset - cell, offset, offset + cell):
            for k in range(rings):
                rr = r - k * step
                if rr <= 0.6:
                    continue
                arcs.append(
                    f'<path d="M {cx - rr:.2f} {row_y + h:.2f} '
                    f'A {rr:.2f} {rr:.2f} 0 0 1 {cx + rr:.2f} {row_y + h:.2f}" '
                    f'fill="none" stroke="{ink}" stroke-width="1"/>'
                )
    body = "".join(arcs)
    return f"""<div class="sf-sea" aria-hidden="true"><svg width="100%" height="100%"
  xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
  <defs><pattern id="sf-seigaiha" width="{cell:.0f}" height="{cell:.0f}"
      patternUnits="userSpaceOnUse">{body}</pattern></defs>
  <rect width="100%" height="100%" fill="url(#sf-seigaiha)"/>
</svg></div>
<style>
  .sf-sea{{
    position:fixed; inset:-{cell:.0f}px -{cell:.0f}px; z-index:0; pointer-events:none;
    opacity:{opacity}; color:{ink};
    animation:sfSea {drift_seconds:.0f}s linear infinite;
  }}
  .sf-sea svg{{display:block; width:100%; height:100%}}
  @keyframes sfSea{{
    from{{transform:translate3d(0,0,0)}}
    to{{transform:translate3d(0,{cell:.0f}px,0)}}
  }}
  .sheet,.wrap,.sf-load{{position:relative; z-index:1}}
  @media (prefers-reduced-motion:reduce){{ .sf-sea{{animation:none}} }}
</style>"""

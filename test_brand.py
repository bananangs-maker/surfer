"""Mark and loading screen.

The mark is a kamon: enclosed in a ring, three-fold rotational symmetry, arcs
only. Symmetry is generated rather than drawn, because an eyeballed 120-degree
rotation reads as a mistake at small sizes.
"""

from __future__ import annotations

import re

import pytest

from brand import DETAIL, loading_html, mark_svg


def test_mark_is_three_fold_symmetric_by_construction():
    """Three fans, placed by computed rotation rather than by hand."""
    svg = mark_svg(120)
    rots = re.findall(r"rotate\((-?\d+)\)", svg)
    assert len(rots) == 3
    angles = sorted(int(r) % 360 for r in rots)
    gaps = [(angles[(i + 1) % 3] - angles[i]) % 360 for i in range(3)]
    assert all(g == 120 for g in gaps), angles


def test_positioning_and_animation_use_separate_groups():
    """A single group cannot do both.

    `transform-box: fill-box` plus a keyframe transform overwrote the translate,
    and all three fans collapsed onto the origin while still fading in correctly —
    an animation that looked fine in isolation and destroyed the mark.
    """
    svg = mark_svg(120, animate=True, idpfx="ld")
    # Outer group positions, inner group carries the delay.
    assert re.search(r'<g class="ld-crest" transform="translate[^>]*>\s*<g', svg)
    assert ".ld-crest > g{" in svg


def test_compact_cut_is_used_below_32px():
    """Three nested arcs per fan turn to mush at favicon sizes."""
    assert mark_svg(16).count("<path") < mark_svg(160).count("<path")
    assert DETAIL["compact"]["rings"] < DETAIL["full"]["rings"]
    assert DETAIL["compact"]["sw"] > DETAIL["full"]["sw"]
    # The hairline inner ring is the first thing to disappear.
    assert "ring2" not in mark_svg(16)
    assert "ring2" in mark_svg(160)


def test_mark_is_arcs_only():
    """Kamon are constructed from circle segments; no straight edges."""
    svg = mark_svg(120)
    assert "<path" in svg and "<circle" in svg
    paths = re.findall(r'd="([^"]+)"', svg)
    assert paths
    for d in paths:
        assert "L" not in d.upper().replace("LINECAP", ""), d


def test_mark_inherits_colour_by_default():
    assert "currentColor" in mark_svg(24)


def test_loading_screen_gives_up_on_its_own():
    """A loading screen that outlives its request is a broken page."""
    html = loading_html()
    assert "setTimeout(done, 12000)" in html
    assert "data-ready" in html


def test_loading_screen_respects_reduced_motion():
    html = loading_html()
    assert "prefers-reduced-motion" in html
    assert mark_svg(112, animate=True).count("prefers-reduced-motion") == 1


def test_every_page_serves_the_mark_and_a_favicon():
    from app import app

    urls = ["/", "/levels?symbol=SYNTH3X", "/compare?symbol=SYNTH3X",
            "/backtest?symbol=SYNTH3X", "/validate?symbol=SYNTH3X"]
    with app.test_client() as c:
        assert c.get("/favicon.svg").mimetype == "image/svg+xml"
        for u in urls:
            html = c.get(u).data.decode()
            assert "favicon.svg" in html, u
            assert "mark-glyph" in html, u
            assert "sf-load" in html, u


# --- loading screen must cover the wait, not follow it -------------------

def test_loading_screen_rises_on_navigation():
    """The reason a purely client-side screen is not enough.

    The server does its slow work BEFORE any HTML exists, so by the time the script
    runs the wait is already over — the first version only flashed after loading
    finished. Raising the overlay on navigation covers exactly that gap: the old
    document stays on screen while the server computes.
    """
    html = loading_html()
    assert 'document.addEventListener("submit"' in html
    assert 'document.addEventListener("click"' in html
    assert "hold()" in html
    assert "계산 중" in html


def test_loading_screen_releases_on_back_navigation():
    """A cached restore keeps the old `stay` flag, and an overlay left up over a
    perfectly good page is worse than no overlay."""
    html = loading_html()
    assert 'window.addEventListener("pageshow"' in html
    assert 'window.addEventListener("popstate"' in html
    assert "stay = false" in html


def test_external_links_do_not_raise_the_screen():
    html = loading_html()
    assert "a.host !== location.host" in html
    assert '_blank' in html


# --- seigaiha ground ----------------------------------------------------

def test_seigaiha_tiles_seamlessly():
    """Two rows per tile give the half-offset brick lattice the motif needs; a
    single row would show a visible seam every cell."""
    from brand import seigaiha_bg

    bg = seigaiha_bg(cell=46.0, rings=4)
    assert 'patternUnits="userSpaceOnUse"' in bg
    assert 'width="46" height="46"' in bg
    # Arcs at two row offsets and three horizontal positions each.
    import re

    ys = {m for m in re.findall(r"A [\d.]+ [\d.]+ 0 0 1 [-\d.]+ ([\d.]+)", bg)}
    assert len(ys) == 2, ys


def test_seigaiha_stays_behind_and_out_of_the_way():
    """A ground that competes with the chart is worse than none."""
    from brand import seigaiha_bg

    bg = seigaiha_bg()
    assert "z-index:0" in bg
    assert "pointer-events:none" in bg
    assert 'aria-hidden="true"' in bg
    opacity = float(bg.split("opacity:")[1].split(";")[0])
    assert 0.02 <= opacity <= 0.12, opacity


def test_seigaiha_drift_is_slow_and_defeasible():
    from brand import seigaiha_bg

    bg = seigaiha_bg()
    assert "prefers-reduced-motion" in bg
    seconds = float(bg.split("animation:sfSea ")[1].split("s ")[0])
    assert seconds >= 60, "faster than a minute per cell reads as motion, not water"


def test_every_page_has_the_ground():
    from app import app

    urls = ["/", "/levels?symbol=SYNTH3X", "/compare?symbol=SYNTH3X",
            "/backtest?symbol=SYNTH3X", "/validate?symbol=SYNTH3X",
            "/sweep?symbol=SYNTH3X&reset=1"]
    with app.test_client() as c:
        for u in urls:
            assert "sf-seigaiha" in c.get(u).data.decode(), u


# --- the measured series must not look like a resting order --------------

def test_percentile_series_is_solid_and_static():
    """sf-level carries a dashed stroke and a marching animation meant for ARMED
    order levels. A moving dashed line reads as an order sitting in the market; the
    ATR percentile is a measurement."""
    from app import app

    with app.test_client() as c:
        html = c.get("/levels?symbol=SYNTH3X").data.decode()
    import re

    m = re.search(r"<polyline[^>]*>", html)
    assert m, "percentile series missing"
    assert 'class="sf-series"' in m.group(0)
    assert "sf-level" not in m.group(0)
    assert "stroke-dasharray" not in m.group(0)
    assert ".sf-series{stroke-dasharray:none; animation:none}" in html

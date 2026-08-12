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

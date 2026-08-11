"""Web-layer smoke tests.

These exist because the first Render deploy failed on a bug the unit tests could
not see: the report page rendered its error branch instead of a result, and
nothing asserted otherwise. A page that returns HTTP 200 while displaying a
traceback is a passing health check and a broken product.

The rule these encode: 200 is not success. The absence of the error branch is.
"""

from __future__ import annotations

import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_healthz():
    with app.test_client() as c:
        assert c.get("/healthz").json == {"ok": True}


def test_report_renders_a_result_not_an_error(client):
    html = client.get("/?run=1&symbol=SYNTH3X").data.decode()
    assert "실행 실패" not in html, "error branch rendered instead of a result"
    assert "Traceback" not in html
    assert "측정 결과" in html


def test_headline_numbers_are_present(client):
    import re

    html = client.get("/?run=1&symbol=SYNTH3X").data.decode()
    nums = re.findall(r'class="big">([\d.]+)%<', html)
    assert len(nums) == 2, f"expected two ambiguity rates, got {nums}"
    daily, sixty = float(nums[0]), float(nums[1])
    # 60m resolution cannot be MORE ambiguous than a single daily bar:
    # splitting a bar can only separate levels, never merge them.
    assert sixty <= daily


def test_quarantine_banner_is_shown_for_quarantined_data(client):
    html = client.get("/?run=1&symbol=SYNTH3X").data.decode()
    assert "성과 수치 산출 금지" in html


def test_parameters_change_the_result(client):
    import re

    def rate(qs):
        html = client.get(qs).data.decode()
        return float(re.findall(r'class="big">([\d.]+)%<', html)[1])

    wide = rate("/?run=1&symbol=SYNTH3X&trigger=0.30&stop=2.0")
    tight = rate("/?run=1&symbol=SYNTH3X&trigger=0.10&stop=1.2")
    # Spacing levels further apart must reduce ambiguity. If this ever fails,
    # the diagnostic is not measuring what it claims to measure.
    assert wide < tight


def test_unknown_symbol_falls_back_rather_than_erroring(client):
    html = client.get("/?run=1&symbol=NOPE").data.decode()
    assert "실행 실패" not in html


def test_landing_page_is_cheap_and_does_no_work(client):
    """The bug that broke deploy #4.

    Render probes "/" with a short timeout. When the landing page eagerly ran
    the diagnostic, every probe timed out and the service was reported as having
    no open HTTP ports - while its own /healthz check passed, because that route
    was cheap. A landing page must answer immediately.
    """
    import time

    t0 = time.perf_counter()
    r = client.get("/")
    elapsed = time.perf_counter() - t0

    assert r.status_code == 200
    assert elapsed < 0.5, f"landing page took {elapsed:.2f}s; must be instant"
    html = r.data.decode()
    assert "준비됨" in html
    assert "측정 결과" not in html, "landing page must not compute a report"


def test_head_request_on_root_is_also_cheap(client):
    import time

    t0 = time.perf_counter()
    r = client.head("/")
    assert r.status_code == 200
    assert time.perf_counter() - t0 < 0.5


def test_verdict_uses_absolute_sessions_not_relative_reduction(client):
    """Guards a metric that was actively misleading.

    The relative reduction in ambiguity rate peaks where intraday data is least
    worth buying: widen the stop until daily ambiguity is near zero and the
    ratio approaches 100% while almost no sessions change outcome. The verdict
    must lead with the count of sessions rescued.
    """
    html = client.get("/?run=1&symbol=SYNTH3X").data.decode()
    assert "구제된 세션" in html
    assert "상대 감소율(%)에 기대지 마세요" in html


def test_wider_stops_rescue_fewer_sessions(client):
    """The relative ratio rises with stop distance while the absolute benefit
    falls. Pin the absolute direction, since that is the decision quantity."""
    import re

    def rescued(qs):
        html = client.get(qs).data.decode()
        return int(re.search(r'verdict-num">(\d+)<', html).group(1))

    tight = rescued("/?run=1&symbol=SYNTH3X&stop=1.2")
    wide = rescued("/?run=1&symbol=SYNTH3X&stop=3.0")
    assert wide < tight, f"wide={wide} tight={tight}"


# --- annotated chart ------------------------------------------------------

def test_diagnostic_page_embeds_a_generated_chart(client):
    """The illustration is drawn from the same data the diagnostic counts, so it
    cannot drift from the numbers printed under it. A hand-drawn diagram could."""
    html = client.get("/?run=1&symbol=SYNTH3X").data.decode()
    assert "<svg viewBox" in html
    assert "ENTRY BAND" in html
    assert "TRIGGER" in html and "STOP" in html


def test_chart_svg_contains_no_hangul():
    """Barlow has no Hangul glyphs, so Korean inside the SVG falls back
    unpredictably. Korean belongs in the HTML legend, not the drawing."""
    import re

    from chart import annotated_session_svg, pick_example
    from levels import PlaceholderBreakout
    from loaders import load_synthetic

    ex = pick_example(load_synthetic(n_sessions=150), PlaceholderBreakout())
    assert ex is not None
    svg = annotated_session_svg(*ex)
    body = re.sub(r'aria-label="[^"]*"', "", svg)
    assert not re.findall(r"[가-힣]", body)


def test_right_rail_price_boxes_do_not_overlap():
    """Trigger and limit can sit close together; their boxes and labels must not
    collide, or both numbers become unreadable."""
    import re

    from chart import annotated_session_svg, pick_example
    from levels import PlaceholderBreakout
    from loaders import load_synthetic

    ex = pick_example(load_synthetic(n_sessions=150), PlaceholderBreakout())
    svg = annotated_session_svg(*ex)
    ys = [float(m) for m in re.findall(r'<rect x="780" y="([\d.]+)" width="88"', svg)]
    assert len(ys) == 3
    for a, b in zip(sorted(ys), sorted(ys)[1:]):
        # 20px box plus room for the label drawn above the lower one.
        assert b - a >= 20, f"price boxes overlap: {sorted(ys)}"


def test_backtest_page_renders_a_verdict(client):
    html = client.get("/backtest?symbol=SYNTH3X&candidate=A").data.decode()
    assert "실행 실패" not in html
    assert "class=\"final" in html
    assert "바이앤홀드" in html

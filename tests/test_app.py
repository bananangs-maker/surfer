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
    html = client.get("/?symbol=SYNTH3X").data.decode()
    assert "실행 실패" not in html, "error branch rendered instead of a result"
    assert "Traceback" not in html
    assert "측정 결과" in html


def test_headline_numbers_are_present(client):
    import re

    html = client.get("/?symbol=SYNTH3X").data.decode()
    nums = re.findall(r'class="big">([\d.]+)%<', html)
    assert len(nums) == 2, f"expected two ambiguity rates, got {nums}"
    daily, sixty = float(nums[0]), float(nums[1])
    # 60m resolution cannot be MORE ambiguous than a single daily bar:
    # splitting a bar can only separate levels, never merge them.
    assert sixty <= daily


def test_quarantine_banner_is_shown_for_quarantined_data(client):
    html = client.get("/?symbol=SYNTH3X").data.decode()
    assert "성과 수치 산출 금지" in html


def test_parameters_change_the_result(client):
    import re

    def rate(qs):
        html = client.get(qs).data.decode()
        return float(re.findall(r'class="big">([\d.]+)%<', html)[1])

    wide = rate("/?symbol=SYNTH3X&trigger=0.30&stop=2.0")
    tight = rate("/?symbol=SYNTH3X&trigger=0.10&stop=1.2")
    # Spacing levels further apart must reduce ambiguity. If this ever fails,
    # the diagnostic is not measuring what it claims to measure.
    assert wide < tight


def test_unknown_symbol_falls_back_rather_than_erroring(client):
    html = client.get("/?symbol=NOPE").data.decode()
    assert "실행 실패" not in html

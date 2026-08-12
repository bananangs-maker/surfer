"""Data-fetch caching and rate-limit handling.

Written after a live YFRateLimitError. The cause was a 30-minute TTL on a system
that decides once per day: the bars do not change between two loads on the same
morning, but every expiry sent another request, and four pages opened a few times
was enough to trip Yahoo's limiter.
"""

from __future__ import annotations

import time

import pytest

import app as A
from loaders import load_synthetic


class FakeRateLimit(Exception):
    """Name contains 'ratelimit' the way yfinance's YFRateLimitError does."""


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    A._CACHE.clear()
    yield
    A._CACHE.clear()


def test_cache_ttl_matches_a_once_daily_system():
    """Six hours, not thirty minutes."""
    assert A.CACHE_TTL >= 60 * 60 * 4


def test_fresh_cache_makes_no_request(monkeypatch):
    calls = {"n": 0}

    def boom(symbol):
        calls["n"] += 1
        raise AssertionError("should not fetch")

    monkeypatch.setattr(A, "_fetch", boom)
    A._CACHE["TQQQ"] = (time.time() - 60, load_synthetic(n_sessions=40))
    ds, age = A.get_dataset("TQQQ")
    assert calls["n"] == 0
    assert age < 120


def test_rate_limit_is_retried_then_raises_with_guidance(monkeypatch):
    calls = {"n": 0}

    def limited(symbol):
        calls["n"] += 1
        raise FakeRateLimit("Too Many Requests. Rate limited.")

    monkeypatch.setattr(A, "_fetch", limited)
    with pytest.raises(A.RateLimited) as e:
        A.get_dataset("TQQQ")
    assert calls["n"] == 3, "must back off and retry, not fail on first refusal"
    assert "몇 분 뒤" in str(e.value)


def test_stale_cache_is_served_when_rate_limited(monkeypatch):
    """A stale dataset beats no dataset.

    Yesterday's levels are wrong but legible; a traceback is neither. The age is
    returned so the page can say so.
    """
    def limited(symbol):
        raise FakeRateLimit("Too Many Requests")

    monkeypatch.setattr(A, "_fetch", limited)
    A._CACHE["TQQQ"] = (time.time() - 60 * 60 * 10, load_synthetic(n_sessions=40))
    ds, age = A.get_dataset("TQQQ")
    assert ds.sessions
    assert age > 60 * 60 * 9


def test_very_stale_cache_is_refused(monkeypatch):
    def limited(symbol):
        raise FakeRateLimit("Too Many Requests")

    monkeypatch.setattr(A, "_fetch", limited)
    A._CACHE["TQQQ"] = (time.time() - 60 * 60 * 80, load_synthetic(n_sessions=40))
    with pytest.raises(A.RateLimited):
        A.get_dataset("TQQQ")


def test_unrelated_failure_is_not_relabelled_as_a_rate_limit(monkeypatch):
    """A delisted ticker shown as "wait a few minutes" would hide its own cause."""
    calls = {"n": 0}

    def other(symbol):
        calls["n"] += 1
        raise ValueError("possibly delisted; no price data")

    monkeypatch.setattr(A, "_fetch", other)
    with pytest.raises(ValueError):
        A.get_dataset("TQQQ")
    assert calls["n"] == 1, "no point retrying a failure that is not a limit"


def test_page_explains_a_rate_limit_instead_of_showing_a_traceback(monkeypatch):
    def limited(symbol):
        raise FakeRateLimit("Too Many Requests")

    monkeypatch.setattr(A, "_fetch", limited)
    with A.app.test_client() as c:
        html = c.get("/levels?symbol=TQQQ").data.decode()
    assert "Rate Limited" in html
    assert "Traceback" not in html
    assert "코드 문제가 아닙니다" in html

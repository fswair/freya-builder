"""Service recovery keeps its deadline and never retries writes or permanent errors."""

from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "diagnostics"))
    return importlib.import_module("recover_practice")


def failure(method, status):
    if status is None:
        return httpx.ReadTimeout("synthetic transport failure")
    response = httpx.Response(status, request=httpx.Request(method, "https://test.invalid"))
    return httpx.HTTPStatusError("synthetic status", request=response.request, response=response)


@pytest.mark.parametrize("status", [None, 429, 500, 502, 503, 504])
def test_exhausted_transient_get_group_can_recover(module, monkeypatch, status):
    calls, sleeps, events = [], [], []

    def api(self, method, path, body=None, **params):
        calls.append((method, path, body, params))
        if len(calls) == 1:
            raise failure(method, status)
        return {"simulation": {"status": "running"}}

    monkeypatch.setattr(module.ReadResilientPractice, "api", api)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    practice = object.__new__(module.ServiceWaitingPractice)
    practice.remaining = lambda: 45
    practice.trace = lambda event, **kwargs: events.append(event)
    assert practice.api("GET", "/synthetic", limit=26) == {"simulation": {"status": "running"}}
    assert calls == [("GET", "/synthetic", None, {"limit": 26})] * 2
    assert sleeps == [10]
    assert events == ["practice_read_wait"]


@pytest.mark.parametrize(
    "method,status",
    [
        ("GET", 400),
        ("GET", 401),
        ("GET", 403),
        ("GET", 404),
        ("POST", None),
        ("POST", 503),
        ("DELETE", 503),
    ],
)
def test_writes_and_permanent_errors_never_get_an_extra_retry(module, monkeypatch, method, status):
    calls, sleeps = [], []

    def api(self, method, path, body=None, **params):
        calls.append(method)
        raise failure(method, status)

    monkeypatch.setattr(module.ReadResilientPractice, "api", api)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    practice = object.__new__(module.ServiceWaitingPractice)
    practice.remaining = lambda: 45
    practice.trace = lambda *args, **kwargs: None
    with pytest.raises((httpx.ReadTimeout, httpx.HTTPStatusError)):
        practice.api(method, "/synthetic")
    assert calls == [method]
    assert sleeps == []


def test_service_wait_consumes_existing_deadline_and_stops(module, monkeypatch):
    calls, sleeps = [], []
    clock = [0.0]

    def remaining():
        left = 11.0 - clock[0]
        if left <= 0:
            raise TimeoutError("synthetic total deadline")
        return min(45, left)

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def api(self, method, path, body=None, **params):
        calls.append(method)
        raise httpx.ReadTimeout("synthetic persistent outage")

    monkeypatch.setattr(module.ReadResilientPractice, "api", api)
    monkeypatch.setattr(module.time, "sleep", sleep)
    practice = object.__new__(module.ServiceWaitingPractice)
    practice.remaining = remaining
    practice.trace = lambda *args, **kwargs: None
    with pytest.raises(TimeoutError, match="total deadline"):
        practice.api("GET", "/synthetic")
    assert calls == ["GET", "GET"]
    assert sleeps == [10, 1]
    assert clock[0] == 11

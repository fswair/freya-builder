from pathlib import Path
from types import SimpleNamespace

import pytest

from freya_hackathon.builder import Builder
from freya_hackathon.pipeline import run_pipeline


class FakeBuilder(Builder):
    def __init__(self, output: Path):
        self.output = output
        self.seconds = 900

    async def run(self) -> dict:
        self.output.mkdir()
        return {"status": "locally_validated"}


def test_pipeline_gives_practice_only_remaining_budget(tmp_path: Path) -> None:
    times = iter([100.0, 107.0, 111.0])
    observed = []

    def factory(path, seconds):
        observed.append((path, seconds))
        return SimpleNamespace(
            execute=lambda *_: {"passed": 1, "total": 1},
            client=SimpleNamespace(close=lambda: observed.append("closed")),
        )

    builder = FakeBuilder(tmp_path / "run")
    result = run_pipeline(
        builder,
        Path("cases.json"),
        "A",
        "slot",
        20,
        practice_factory=factory,
        clock=lambda: next(times),
    )
    assert observed == [(builder.output, 13.0), "closed"]
    assert result["practice"]["passed"] == 1
    assert builder.seconds == 20


def test_expired_pipeline_never_starts_practice(tmp_path: Path) -> None:
    times = iter([100.0, 121.0, 121.0])

    def unexpected(*_):
        raise AssertionError("Practice must not start after the deadline")

    with pytest.raises(TimeoutError):
        run_pipeline(
            FakeBuilder(tmp_path / "run"),
            Path("cases.json"),
            "A",
            "slot",
            20,
            practice_factory=unexpected,
            clock=lambda: next(times),
        )

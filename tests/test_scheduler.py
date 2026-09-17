"""Scheduler semantics with a fake clock: slots, grace, refire protection,
cooldown, attempt caps, dependencies, catch-up after 'sleep'."""

import asyncio
from datetime import datetime

from presence.core.scheduler import Job, Scheduler


class Clock:
    def __init__(self, iso: str):
        self.now = datetime.fromisoformat(iso)

    def __call__(self) -> datetime:
        return self.now


def make(tmp_path, clock, results, fail=frozenset(), jobs=None):
    async def run(name):
        results.append(name)
        if name in fail:
            raise RuntimeError("boom")

    jobs = jobs or [Job(name="scout", at="08:00", run=lambda: run("scout"))]
    for job in jobs:
        real = job.name
        job.run = (lambda n: (lambda: run(n)))(real)
    return Scheduler(jobs, tmp_path / "runs.json", clock=clock)


def tick(scheduler):
    return asyncio.run(scheduler.tick())


def test_not_due_before_slot_then_fires_once(tmp_path):
    clock = Clock("2026-09-11T07:59:00")
    results = []
    scheduler = make(tmp_path, clock, results)
    assert tick(scheduler) == []
    clock.now = datetime.fromisoformat("2026-09-11T08:01:30")
    assert tick(scheduler) == ["scout"]
    assert tick(scheduler) == []  # already ok today
    assert results == ["scout"]


def test_catchup_long_after_slot(tmp_path):
    clock = Clock("2026-09-11T14:30:00")  # "woke from sleep" well past the slot
    results = []
    scheduler = make(tmp_path, clock, results)
    assert tick(scheduler) == ["scout"]


def test_failure_cooldown_and_attempt_cap(tmp_path):
    clock = Clock("2026-09-11T08:02:00")
    results = []
    scheduler = make(tmp_path, clock, results, fail={"scout"})
    assert tick(scheduler) == ["scout"]
    assert tick(scheduler) == []  # inside cooldown
    clock.now = datetime.fromisoformat("2026-09-11T08:15:00")
    assert tick(scheduler) == ["scout"]
    clock.now = datetime.fromisoformat("2026-09-11T08:30:00")
    assert tick(scheduler) == ["scout"]
    clock.now = datetime.fromisoformat("2026-09-11T09:00:00")
    assert tick(scheduler) == []  # attempt cap (3) reached
    assert results == ["scout"] * 3


def test_dependency_gates_until_dep_ok(tmp_path):
    clock = Clock("2026-09-11T09:30:00")
    results = []
    jobs = [
        Job(name="brief", at="09:00", run=None, deps=["scout"]),
        Job(name="scout", at="08:00", run=None),
    ]
    scheduler = make(tmp_path, clock, results, jobs=jobs)
    # first tick: scout runs; brief is gated because scout wasn't ok yet at check time
    attempted = tick(scheduler)
    assert "scout" in attempted
    # second tick: scout is ok today, so brief fires
    assert tick(scheduler) == ["brief"]


def test_new_day_runs_again(tmp_path):
    clock = Clock("2026-09-11T08:05:00")
    results = []
    scheduler = make(tmp_path, clock, results)
    assert tick(scheduler) == ["scout"]
    clock.now = datetime.fromisoformat("2026-09-12T08:05:00")
    assert tick(scheduler) == ["scout"]

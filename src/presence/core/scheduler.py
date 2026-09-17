"""The supervisor: fires each job once per day at its slot, with the
semantics a daily job needs — a grace window after the slot, cooldown
between failed attempts, a per-day attempt cap, and job dependencies.
Catch-up after laptop sleep needs no special code: every tick compares the
wall clock with today's run record, so a missed slot fires on the next tick
after wake.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("presence.scheduler")


@dataclass
class Job:
    name: str
    at: str  # "HH:MM" daily slot
    run: Callable[[], Awaitable[None]]
    deps: list[str] = field(default_factory=list)


class Scheduler:
    def __init__(
        self,
        jobs: list[Job],
        state_path: Path,
        grace_seconds: int = 60,
        cooldown_seconds: int = 600,
        max_attempts_per_day: int = 3,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.jobs = {j.name: j for j in jobs}
        for job in jobs:
            for dep in job.deps:
                if dep not in self.jobs:
                    raise ValueError(f"job '{job.name}' depends on unknown job '{dep}'")
        self.state_path = state_path
        self.grace = grace_seconds
        self.cooldown = cooldown_seconds
        self.max_attempts = max_attempts_per_day
        self.clock = clock

    # ------------------------------------------------------------- state

    def _load(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {}

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=1))

    def _record(self, job: str, status: str) -> None:
        now = self.clock()
        state = self._load()
        day = state.setdefault(now.date().isoformat(), {})
        entry = day.setdefault(job, {"attempts": 0})
        entry["status"] = status
        entry["attempts"] += 1
        entry["last_attempt"] = now.isoformat(timespec="seconds")
        self._save(state)

    # -------------------------------------------------------------- logic

    def due(self) -> list[Job]:
        """Jobs whose slot (plus grace) has passed today, that haven't
        succeeded today, respecting attempt caps, cooldowns and deps."""
        now = self.clock()
        today = self._load().get(now.date().isoformat(), {})
        ready: list[Job] = []
        for job in self.jobs.values():
            hour, minute = (int(x) for x in job.at.split(":"))
            slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if (now - slot).total_seconds() < self.grace:
                continue
            entry = today.get(job.name, {})
            if entry.get("status") == "ok":
                continue
            if entry.get("attempts", 0) >= self.max_attempts:
                continue
            last = entry.get("last_attempt")
            if last and (now - datetime.fromisoformat(last)).total_seconds() < self.cooldown:
                continue
            if any(today.get(dep, {}).get("status") != "ok" for dep in job.deps):
                continue
            ready.append(job)
        return ready

    async def tick(self) -> list[str]:
        """Run everything currently due, sequentially, recording outcomes.
        Returns the names of jobs attempted (for tests and status surfaces)."""
        attempted: list[str] = []
        for job in self.due():
            attempted.append(job.name)
            try:
                await job.run()
            except Exception:
                log.exception("job %s failed", job.name)
                self._record(job.name, "error")
            else:
                self._record(job.name, "ok")
        return attempted

    async def run_forever(self, interval_seconds: int = 30) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(interval_seconds)

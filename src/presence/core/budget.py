"""The daily token budget — charter rule 4 ("Spending is capped") as code.

A JSON ledger of tokens spent per day. `ensure_available()` raises once the
day's cap is reached; `charge()` records real usage after each provider call
and fires the notification hook exactly once per day when the cap is crossed
(W4 wires that hook to the messenger).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from presence.core.providers import Usage


class BudgetExhausted(Exception):
    def __init__(self, spent: int, limit: int):
        super().__init__(f"daily token budget exhausted: {spent} of {limit} tokens used")
        self.spent = spent
        self.limit = limit


class DailyBudget:
    def __init__(
        self,
        max_tokens_per_day: int,
        ledger_path: Path,
        on_exhausted: Callable[[int, int], None] | None = None,
        today: Callable[[], str] = lambda: date.today().isoformat(),
    ):
        self.limit = max_tokens_per_day
        self.ledger_path = ledger_path
        self.on_exhausted = on_exhausted
        self._today = today

    def _load(self) -> dict:
        if self.ledger_path.exists():
            return json.loads(self.ledger_path.read_text())
        return {}

    def _save(self, ledger: dict) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(ledger, indent=1))

    def _day(self, ledger: dict) -> dict:
        return ledger.setdefault(
            self._today(), {"input": 0, "output": 0, "calls": 0, "notified": False}
        )

    @property
    def spent_today(self) -> int:
        day = self._day(self._load())
        return day["input"] + day["output"]

    def ensure_available(self) -> None:
        spent = self.spent_today
        if spent >= self.limit:
            raise BudgetExhausted(spent, self.limit)

    def charge(self, usage: Usage) -> None:
        ledger = self._load()
        day = self._day(ledger)
        day["input"] += usage.input_tokens
        day["output"] += usage.output_tokens
        day["calls"] += 1
        spent = day["input"] + day["output"]
        if spent >= self.limit and not day["notified"]:
            day["notified"] = True
            if self.on_exhausted is not None:
                self.on_exhausted(spent, self.limit)
        self._save(ledger)

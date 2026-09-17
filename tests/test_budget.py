import pytest

from presence.core.budget import BudgetExhausted, DailyBudget
from presence.core.providers import Usage


def test_accumulates_and_raises(tmp_path):
    budget = DailyBudget(100, tmp_path / "ledger.json")
    budget.charge(Usage(input_tokens=40, output_tokens=30))
    assert budget.spent_today == 70
    budget.ensure_available()
    budget.charge(Usage(input_tokens=40, output_tokens=0))
    with pytest.raises(BudgetExhausted):
        budget.ensure_available()


def test_notifies_exactly_once(tmp_path):
    events = []
    budget = DailyBudget(50, tmp_path / "l.json", on_exhausted=lambda s, li: events.append((s, li)))
    budget.charge(Usage(input_tokens=60, output_tokens=0))
    budget.charge(Usage(input_tokens=10, output_tokens=0))
    assert events == [(60, 50)]


def test_new_day_resets(tmp_path):
    day = ["2026-09-11"]
    budget = DailyBudget(50, tmp_path / "l.json", today=lambda: day[0])
    budget.charge(Usage(input_tokens=50, output_tokens=0))
    with pytest.raises(BudgetExhausted):
        budget.ensure_available()
    day[0] = "2026-09-12"
    budget.ensure_available()
    assert budget.spent_today == 0

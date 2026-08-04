from pathlib import Path

import pytest

from tripops.context import (
    BudgetExceeded,
    FileArtifactStore,
    RunBudget,
    RunBudgetLimits,
    SQLiteMemoryStore,
)


def test_artifact_store_is_content_addressed(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")

    first = store.put_text("large tool output", source="weather.search")
    second = store.put_text("large tool output", source="weather.search")

    assert first.id == second.id
    assert store.get_text(first.id) == "large tool output"


def test_artifact_store_rejects_path_traversal(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="invalid artifact id"):
        store.get_text("../../secret")


def test_memory_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    SQLiteMemoryStore(path).put("user:u1", "diet", {"excluded": ["peanut"]})

    reopened = SQLiteMemoryStore(path)

    assert reopened.get("user:u1", "diet") == {"excluded": ["peanut"]}
    assert reopened.list_namespace("user:u1") == {"diet": {"excluded": ["peanut"]}}
    assert reopened.delete("user:u1", "diet")
    assert reopened.get("user:u1", "diet") is None


def test_run_budget_does_not_commit_failed_consumption() -> None:
    budget = RunBudget(RunBudgetLimits(model_calls=1, tool_calls=1, cost_units=1))
    budget.consume_tool_call(cost_units=0.8)

    with pytest.raises(BudgetExceeded, match="tool_calls"):
        budget.consume_tool_call(cost_units=0.1)

    assert budget.snapshot() == {"model_calls": 0, "tool_calls": 1, "cost_units": 0.8}


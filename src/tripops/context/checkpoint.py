from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

TRIPOPS_CHECKPOINT_MODULES = (
    ("tripops.agents.models", "ResearchResult"),
    ("tripops.constraints.impact", "RepairScope"),
    ("tripops.context.state", "WorkflowPhase"),
    ("tripops.domain.approval", "ApprovalDecision"),
    ("tripops.domain.approval", "ApprovalRequest"),
    ("tripops.domain.candidates", "CandidateFact"),
    ("tripops.domain.constraints", "Constraint"),
    ("tripops.domain.constraints", "ConstraintKind"),
    ("tripops.domain.constraints", "ConstraintPriority"),
    ("tripops.domain.disruptions", "DisruptionEvent"),
    ("tripops.domain.disruptions", "DisruptionType"),
    ("tripops.domain.evidence", "Evidence"),
    ("tripops.domain.evidence", "EvidenceSource"),
    ("tripops.domain.plan", "ItineraryItem"),
    ("tripops.domain.plan", "PlanStep"),
    ("tripops.domain.plan", "PlanStepStatus"),
    ("tripops.domain.plan", "TravelPlan"),
    ("tripops.domain.trip", "Traveler"),
    ("tripops.domain.trip", "TripRequest"),
    ("tripops.domain.violations", "Violation"),
    ("tripops.domain.violations", "ViolationCode"),
    ("tripops.domain.violations", "ViolationSeverity"),
    ("tripops.tools.models", "RiskLevel"),
)


@asynccontextmanager
async def open_sqlite_checkpointer(path: Path) -> AsyncIterator[AsyncSqliteSaver]:
    """Open and initialize a durable LangGraph checkpointer."""

    path.parent.mkdir(parents=True, exist_ok=True)
    serde = JsonPlusSerializer(allowed_msgpack_modules=TRIPOPS_CHECKPOINT_MODULES)
    async with aiosqlite.connect(str(path)) as connection:
        saver = AsyncSqliteSaver(connection, serde=serde)
        await saver.setup()
        yield saver

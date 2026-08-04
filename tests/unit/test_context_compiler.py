from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from langchain_core.messages import AIMessage, HumanMessage

from tripops.context import ContextCompiler, ContextPolicy, RuntimeContext, WorkflowPhase
from tripops.context.compiler import ContextSectionName
from tripops.domain import (
    Evidence,
    EvidenceSource,
    Traveler,
    TravelPlan,
    TripRequest,
    Violation,
    ViolationCode,
    ViolationSeverity,
)
from tripops.domain.plan import ItineraryItem

NOW = datetime(2030, 10, 1, tzinfo=UTC)


def runtime() -> RuntimeContext:
    return RuntimeContext(
        run_id="context-run",
        thread_id="context-thread",
        user_id="alice",
        permissions=frozenset({"travel:read"}),
    )


def request(raw: str = "Plan a Kyoto trip") -> TripRequest:
    return TripRequest(
        id="context-trip",
        origin="Shanghai",
        destinations=("Kyoto",),
        start_date=date(2030, 10, 1),
        end_date=date(2030, 10, 3),
        budget=Decimal("1000"),
        travelers=(Traveler(id="alice", display_name="Alice"),),
        raw_requirement=raw,
    )


def evidence(
    evidence_id: str,
    *,
    confidence: float = 0.9,
    expires_at: datetime | None = None,
    artifact_id: str | None = None,
) -> Evidence:
    return Evidence(
        id=evidence_id,
        claim=f"Claim for {evidence_id}",
        source_type=EvidenceSource.MCP_TOOL,
        source_name="weather",
        retrieved_at=NOW,
        expires_at=expires_at,
        confidence=confidence,
        artifact_id=artifact_id,
    )


def state(*, raw: str = "Plan a Kyoto trip") -> dict[str, object]:
    trip = request(raw)
    item = ItineraryItem(
        id="museum",
        title="Museum",
        location="Kyoto",
        starts_at=NOW.replace(hour=9),
        ends_at=NOW.replace(hour=11),
        cost=Decimal("100"),
        evidence_ids=("fresh",),
    )
    return {
        "messages": [HumanMessage(content="first"), AIMessage(content="second")],
        "phase": WorkflowPhase.VERIFY,
        "request": trip,
        "plan": TravelPlan(
            trip_id=trip.id,
            itinerary=(item,),
            estimated_total_cost=Decimal("100"),
        ),
        "evidence": [
            evidence("fresh", expires_at=NOW + timedelta(hours=1), artifact_id="art_abc123"),
            evidence("stale", expires_at=NOW - timedelta(seconds=1)),
            evidence("weak", confidence=0.1),
        ],
        "violations": [
            Violation(
                code=ViolationCode.BUDGET_EXCEEDED,
                severity=ViolationSeverity.ERROR,
                message="Budget exceeded",
            )
        ],
        "selected_skills": ["itinerary-optimization"],
        "required_capabilities": ["itinerary_planning"],
    }


def test_compiler_filters_stale_and_weak_evidence_and_keeps_artifact_reference() -> None:
    compiler = ContextCompiler(clock=lambda: NOW)

    envelope = compiler.compile(
        state(),  # type: ignore[arg-type]
        runtime(),
        memory={"home_airport": "PVG"},
    )

    assert "Claim for fresh" in envelope.text
    assert "Claim for stale" not in envelope.text
    assert "Claim for weak" not in envelope.text
    assert envelope.stale_evidence_ids == ("stale",)
    assert envelope.omitted_evidence_ids == ("stale", "weak")
    assert envelope.artifact_ids == ("art_abc123",)
    assert "artifact://art_abc123" in envelope.text


def test_compiler_respects_evidence_and_message_limits() -> None:
    current = state()
    current["evidence"] = [evidence(f"ev-{index}") for index in range(5)]
    current["messages"] = [HumanMessage(content=f"message-{index}") for index in range(5)]
    compiler = ContextCompiler(
        ContextPolicy(max_evidence=2, max_messages=2),
        clock=lambda: NOW,
    )

    envelope = compiler.compile(current, runtime())  # type: ignore[arg-type]
    by_name = {section.name: section for section in envelope.sections}

    assert by_name[ContextSectionName.EVIDENCE].item_count == 2
    assert by_name[ContextSectionName.CONVERSATION].item_count == 2
    assert set(envelope.omitted_evidence_ids) == {"ev-2", "ev-3", "ev-4"}
    assert "message-0" not in envelope.text
    assert "message-4" in envelope.text


def test_critical_violations_survive_tight_context_before_large_request() -> None:
    compiler = ContextCompiler(
        ContextPolicy(max_chars=1000, section_overhead_chars=20),
        clock=lambda: NOW,
    )

    envelope = compiler.compile(
        state(raw="x" * 4_000),  # type: ignore[arg-type]
        runtime(),
    )
    by_name = {section.name: section for section in envelope.sections}

    assert "budget_exceeded" in by_name[ContextSectionName.VIOLATIONS].content
    assert not by_name[ContextSectionName.VIOLATIONS].truncated
    assert by_name[ContextSectionName.REQUEST].truncated
    assert ContextSectionName.REQUEST in envelope.truncated_sections


def test_compiler_can_include_stale_evidence_when_policy_allows() -> None:
    compiler = ContextCompiler(
        ContextPolicy(include_stale_evidence=True),
        clock=lambda: NOW,
    )

    envelope = compiler.compile(state(), runtime())  # type: ignore[arg-type]

    assert "Claim for stale" in envelope.text
    assert envelope.stale_evidence_ids == ("stale",)


def test_compiled_size_and_token_estimate_are_auditable() -> None:
    envelope = ContextCompiler(clock=lambda: NOW).compile(
        state(),  # type: ignore[arg-type]
        runtime(),
    )

    assert envelope.total_chars == len(envelope.text)
    assert envelope.estimated_tokens == (len(envelope.text) + 3) // 4
    assert {section.name for section in envelope.sections} == set(ContextSectionName)

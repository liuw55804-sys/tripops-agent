from pathlib import Path

from tripops.middleware.events import ToolEvent, ToolEventType
from tripops.observability import (
    InMemoryTraceSink,
    JsonlTraceSink,
    ToolTraceAdapter,
    TraceEvent,
    TraceKind,
    TraceStatus,
    trace_span,
)


def test_jsonl_trace_sink_round_trips(tmp_path: Path) -> None:
    sink = JsonlTraceSink(tmp_path / "traces" / "events.jsonl")
    event = TraceEvent(
        run_id="run-1",
        kind=TraceKind.VIOLATION,
        name="budget_exceeded",
        status=TraceStatus.FAILED,
        attributes={"amount": 1200},
    )

    sink.emit(event)

    assert sink.read() == (event,)


def test_trace_span_records_start_and_failure() -> None:
    sink = InMemoryTraceSink()

    try:
        with trace_span(
            sink,
            run_id="run-1",
            kind=TraceKind.AGENT,
            name="planner",
        ):
            raise ValueError("bad plan")
    except ValueError:
        pass

    events = sink.snapshot()
    assert [event.status for event in events] == [TraceStatus.STARTED, TraceStatus.FAILED]
    assert events[0].span_id == events[1].span_id


def test_tool_events_share_unified_trace_schema() -> None:
    sink = InMemoryTraceSink()
    adapter = ToolTraceAdapter(sink)

    adapter.emit(
        ToolEvent(
            event_type=ToolEventType.FALLBACK_STARTED,
            run_id="run-1",
            tool_call_id="call-1",
            tool_name="weather.backup",
            details={"primary_tool": "weather.primary"},
        )
    )

    event = sink.snapshot()[0]
    assert event.kind is TraceKind.DEGRADATION
    assert event.status is TraceStatus.DEGRADED
    assert event.span_id == "call-1"


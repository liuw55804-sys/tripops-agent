from enum import StrEnum

from pydantic import BaseModel, Field


class ViolationCode(StrEnum):
    BUDGET_EXCEEDED = "budget_exceeded"
    BUDGET_UNVERIFIED = "budget_unverified"
    TIME_OVERLAP = "time_overlap"
    CLOSED_AT_ARRIVAL = "closed_at_arrival"
    TRANSIT_TIME_INSUFFICIENT = "transit_time_insufficient"
    REQUIRED_PREFERENCE_MISSING = "required_preference_missing"
    EVIDENCE_MISSING = "evidence_missing"
    STALE_EVIDENCE = "stale_evidence"
    POLICY_VIOLATION = "policy_violation"
    DATE_OUT_OF_RANGE = "date_out_of_range"
    EXCLUDED_ACTIVITY_PRESENT = "excluded_activity_present"
    ACCESSIBILITY_UNMET = "accessibility_unmet"
    DIETARY_UNSAFE = "dietary_unsafe"
    COST_MISMATCH = "cost_mismatch"


class ViolationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Violation(BaseModel):
    code: ViolationCode
    severity: ViolationSeverity
    message: str = Field(min_length=1)
    affected_item_ids: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()
    repair_hint: str | None = None
    repair_capabilities: tuple[str, ...] = ()
    deterministic: bool = True

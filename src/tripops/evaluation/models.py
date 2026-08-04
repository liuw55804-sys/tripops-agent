from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from tripops.domain.disruptions import DisruptionEvent
from tripops.domain.evidence import Evidence
from tripops.domain.plan import TravelPlan
from tripops.domain.trip import TripRequest
from tripops.domain.violations import ViolationCode


class CaseCategory(StrEnum):
    STANDARD = "standard"
    DYNAMIC = "dynamic"
    FAULT = "fault"


class FaultMode(StrEnum):
    NONE = "none"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    MALFORMED_RESPONSE = "malformed_response"
    RATE_LIMIT = "rate_limit"
    PRIMARY_UNAVAILABLE = "primary_unavailable"


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1)
    category: CaseCategory
    description: str = Field(min_length=1)
    request: TripRequest
    plan: TravelPlan
    evidence: tuple[Evidence, ...] = ()
    disruption: DisruptionEvent | None = None
    fault_mode: FaultMode = FaultMode.NONE
    expected_violation_codes: frozenset[ViolationCode] = frozenset()
    expected_affected_item_ids: frozenset[str] = frozenset()
    expected_preserved_item_ids: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def validate_category_payload(self) -> "EvaluationCase":
        if self.category is CaseCategory.DYNAMIC and self.disruption is None:
            raise ValueError("dynamic cases require a disruption")
        if self.category is CaseCategory.FAULT and self.fault_mode is FaultMode.NONE:
            raise ValueError("fault cases require a fault mode")
        return self


class EvaluationResult(BaseModel):
    case_id: str
    category: CaseCategory
    expected_codes: frozenset[ViolationCode]
    actual_codes: frozenset[ViolationCode]
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    hard_constraint_pass: bool
    preference_coverage: float = Field(ge=0, le=1)
    group_fairness: float = Field(ge=0, le=1)
    citation_correctness: float = Field(ge=0, le=1)
    citation_freshness: float = Field(ge=0, le=1)
    affected_item_recall: float = Field(ge=0, le=1)
    plan_preservation: float = Field(ge=0, le=1)
    degradation_success: bool
    latency_ms: float = Field(ge=0)


class MetricSummary(BaseModel):
    case_count: int = Field(ge=0)
    hard_constraint_pass_rate: float = Field(ge=0, le=1)
    violation_precision: float = Field(ge=0, le=1)
    violation_recall: float = Field(ge=0, le=1)
    violation_f1: float = Field(ge=0, le=1)
    preference_coverage: float = Field(ge=0, le=1)
    group_fairness: float = Field(ge=0, le=1)
    citation_correctness: float = Field(ge=0, le=1)
    citation_freshness: float = Field(ge=0, le=1)
    affected_item_recall: float = Field(ge=0, le=1)
    plan_preservation: float = Field(ge=0, le=1)
    degradation_success_rate: float = Field(ge=0, le=1)
    average_latency_ms: float = Field(ge=0)


class EvaluationReport(BaseModel):
    suite_name: str
    generated_at: str
    summary: MetricSummary
    by_category: dict[CaseCategory, MetricSummary]
    results: tuple[EvaluationResult, ...]

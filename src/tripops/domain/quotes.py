from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import AnyHttpUrl, BaseModel, Field, field_serializer


class QuoteKind(StrEnum):
    ACCOMMODATION = "accommodation"
    TRANSPORT = "transport"
    RESTAURANT = "restaurant"


class QuoteUnit(StrEnum):
    PER_ROOM_NIGHT = "per_room_night"
    PER_PERSON = "per_person"
    PER_PERSON_MEAL = "per_person_meal"


class QuoteStatus(StrEnum):
    MATCHED = "matched"
    INDICATIVE = "indicative"
    REJECTED = "rejected"


class QuoteFact(BaseModel):
    id: str = Field(min_length=1)
    kind: QuoteKind
    title: str = Field(min_length=1)
    location: str | None = None
    scope_key: str = Field(default="general", min_length=1)
    amount_low: Decimal = Field(gt=0)
    amount_high: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    unit: QuoteUnit
    status: QuoteStatus
    source_uri: AnyHttpUrl
    evidence_id: str = Field(min_length=1)
    observed_at: datetime
    applicable_date: date | None = None
    rejection_reason: str | None = None

    @field_serializer("source_uri")
    def serialize_source_uri(self, value: AnyHttpUrl) -> str:
        return str(value)


class FxRate(BaseModel):
    base: str = Field(min_length=3, max_length=3)
    quote: str = Field(min_length=3, max_length=3)
    rate: Decimal = Field(gt=0)
    as_of: date
    source_uri: AnyHttpUrl

    @field_serializer("source_uri")
    def serialize_source_uri(self, value: AnyHttpUrl) -> str:
        return str(value)


class BudgetComponent(BaseModel):
    kind: str = Field(min_length=1)
    label: str = Field(min_length=1)
    amount_low: Decimal = Field(ge=0)
    amount_high: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    quantity: Decimal = Field(gt=0)
    quote_ids: tuple[str, ...] = ()
    note: str = ""


class BudgetLedger(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    total_low: Decimal = Field(ge=0)
    total_high: Decimal = Field(ge=0)
    components: tuple[BudgetComponent, ...] = ()
    quotes: tuple[QuoteFact, ...] = ()
    fx_rates: tuple[FxRate, ...] = ()
    unpriced_kinds: tuple[str, ...] = ()
    generated_at: datetime

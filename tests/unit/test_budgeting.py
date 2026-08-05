from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tripops.domain import Evidence, EvidenceSource, Traveler, TripRequest
from tripops.domain.plan import ItineraryItem
from tripops.domain.quotes import FxRate, QuoteStatus
from tripops.planning import EvidenceBudgetBuilder, WebQuoteExtractor


def request() -> TripRequest:
    return TripRequest(
        id="quotes",
        origin="上海",
        destinations=("悉尼", "墨尔本"),
        start_date=date(2026, 9, 30),
        end_date=date(2026, 10, 3),
        budget=Decimal("21000"),
        travelers=tuple(Traveler(id=f"t{index}", display_name=f"T{index}") for index in range(4)),
        raw_requirement="悉尼进、墨尔本出，共两间房",
    )


def evidence(
    evidence_id: str,
    capability: str,
    title: str,
    claim: str,
    url: str,
) -> Evidence:
    return Evidence(
        id=evidence_id,
        claim=claim,
        source_type=EvidenceSource.LOCAL_TOOL,
        source_name="tavily-web-search",
        source_uri=url,
        retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        confidence=0.9,
        metadata={"capability": capability, "search_title": title},
    )


class FixedRates:
    async def get_rate(self, base: str, quote: str) -> FxRate:
        assert (base, quote) == ("AUD", "CNY")
        return FxRate(
            base=base,
            quote=quote,
            rate=Decimal("5"),
            as_of=date(2026, 8, 5),
            source_uri="https://example.com/fx",
        )


def quote_evidence() -> tuple[Evidence, ...]:
    return (
        evidence(
            "stay-syd",
            "accommodation_search",
            "Sydney hotel",
            "2026-09-30 AUD $200 per night",
            "https://hotel.example/sydney",
        ),
        evidence(
            "stay-mel",
            "accommodation_search",
            "Melbourne hotel",
            "2026-10-03 AUD $150 per night",
            "https://hotel.example/melbourne",
        ),
        evidence(
            "flight",
            "transport_search",
            "Sydney to Melbourne",
            "2026-10-03 fare from AUD $100 per person",
            "https://air.example/fare",
        ),
        evidence(
            "meal",
            "restaurant_search",
            "Melbourne menu",
            "Average menu AUD $50 per person",
            "https://food.example/menu",
        ),
        evidence(
            "stale",
            "transport_search",
            "Sydney flight to Melbourne",
            "Departing 2027 from AUD $80",
            "https://air.example/stale",
        ),
        evidence(
            "wrong-city",
            "restaurant_search",
            "Melbourne dining",
            "Melbourne Florida near Orlando average AUD $20",
            "https://food.example/florida",
        ),
    )


def test_quote_extractor_rejects_wrong_year_and_wrong_melbourne() -> None:
    quotes = WebQuoteExtractor().extract(request(), quote_evidence())
    rejected = {
        quote.evidence_id: quote.rejection_reason
        for quote in quotes
        if quote.status is QuoteStatus.REJECTED
    }

    assert rejected == {
        "stale": "source dates do not match the trip year",
        "wrong-city": "source refers to Melbourne, Florida",
    }


@pytest.mark.asyncio
async def test_budget_ledger_applies_rooms_nights_travelers_meals_and_fx() -> None:
    meal = ItineraryItem(
        id="meal-item",
        title="Dinner",
        location="墨尔本",
        starts_at=datetime(2026, 10, 3, 18, tzinfo=UTC),
        ends_at=datetime(2026, 10, 3, 19, tzinfo=UTC),
        category="food",
    )
    ledger = await EvidenceBudgetBuilder(rate_provider=FixedRates()).build(
        request(),
        quote_evidence(),
        (meal,),
    )

    assert ledger.total_low == Decimal("8500")
    assert ledger.total_high == Decimal("8500")
    assert ledger.unpriced_kinds == ()
    assert {component.label for component in ledger.components} == {
        "悉尼住宿",
        "墨尔本住宿",
        "城际交通",
        "行程内餐饮",
    }
    assert len(ledger.fx_rates) == 1

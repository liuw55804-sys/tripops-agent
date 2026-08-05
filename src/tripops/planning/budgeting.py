import re
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha1
from typing import Protocol
from urllib.parse import urlparse

import httpx
from pydantic import AnyHttpUrl

from tripops.domain.evidence import Evidence
from tripops.domain.plan import ItineraryItem
from tripops.domain.quotes import (
    BudgetComponent,
    BudgetLedger,
    FxRate,
    QuoteFact,
    QuoteKind,
    QuoteStatus,
    QuoteUnit,
)
from tripops.domain.trip import TripRequest
from tripops.planning.route import allocate_destination_days

FRANKFURTER_RATE_URL = "https://api.frankfurter.dev/v2/rate/{base}/{quote}"
PRICE_PATTERN = re.compile(
    r"(?<![\w])(?P<currency>AUD|USD|CNY|RMB|AU\$|A\$|US\$|¥|￥|\$)\s*\$?\s*"
    r"(?P<amount>\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
YEAR_PATTERN = re.compile(r"\b20\d{2}\b")
ROOM_PATTERN = re.compile(r"([一二两三四五六七八九十\d]+)\s*间(?:房|客房)?")
CHINESE_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}
DESTINATION_ALIASES = {
    "悉尼": ("悉尼", "sydney"),
    "墨尔本": ("墨尔本", "melbourne"),
    "皇后镇": ("皇后镇", "queenstown"),
}


class FxRateProvider(Protocol):
    async def get_rate(self, base: str, quote: str) -> FxRate: ...


class FrankfurterFxRateProvider:
    async def get_rate(self, base: str, quote: str) -> FxRate:
        url = FRANKFURTER_RATE_URL.format(base=base, quote=quote)
        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=10) as client:
            for attempt in range(2):
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 1:
                        raise
        if response is None:
            raise RuntimeError("exchange-rate provider returned no response")
        payload = response.json()
        return FxRate(
            base=str(payload["base"]),
            quote=str(payload["quote"]),
            rate=Decimal(str(payload["rate"])),
            as_of=payload["date"],
            source_uri=AnyHttpUrl(url),
        )


class WebQuoteExtractor:
    def extract(
        self,
        request: TripRequest,
        evidence: tuple[Evidence, ...],
    ) -> tuple[QuoteFact, ...]:
        quotes: list[QuoteFact] = []
        for item in evidence:
            capability = str(item.metadata.get("capability", ""))
            kind = self._kind(capability)
            if kind is None or item.source_uri is None:
                continue
            title = str(item.metadata.get("search_title") or item.claim[:90]).strip()
            title_matches = list(PRICE_PATTERN.finditer(title))
            searchable = title if title_matches else item.claim
            matches = title_matches or list(PRICE_PATTERN.finditer(item.claim))
            if not matches:
                continue
            match = matches[0]
            currency = self._currency(match.group("currency"), str(item.source_uri))
            if currency is None:
                continue
            amount_low = Decimal(match.group("amount").replace(",", ""))
            amount_high = amount_low
            if len(matches) > 1:
                connector = searchable[match.end() : matches[1].start()].casefold()
                next_currency = self._currency(matches[1].group("currency"), str(item.source_uri))
                if next_currency == currency and re.search(r"\bto\b|[-–—至到]", connector):
                    amount_high = Decimal(matches[1].group("amount").replace(",", ""))
            if amount_low <= 0 or amount_high <= 0:
                continue
            location = self._location(request, item)
            status, reason = self._status(request, item, location, kind)
            quote_key = f"{item.id}:{currency}:{amount_low}:{amount_high}"
            quotes.append(
                QuoteFact(
                    id=f"quote-{sha1(quote_key.encode()).hexdigest()[:12]}",
                    kind=kind,
                    title=title,
                        location=location,
                        scope_key=str(item.metadata.get("search_scope") or "general"),
                    amount_low=min(amount_low, amount_high),
                    amount_high=max(amount_low, amount_high),
                    currency=currency,
                    unit=self._unit(kind),
                    status=status,
                    source_uri=item.source_uri,
                    evidence_id=item.id,
                    observed_at=item.retrieved_at,
                    rejection_reason=reason,
                )
            )
        return tuple(quotes)

    @staticmethod
    def _kind(capability: str) -> QuoteKind | None:
        return {
            "accommodation_search": QuoteKind.ACCOMMODATION,
            "transport_search": QuoteKind.TRANSPORT,
            "restaurant_search": QuoteKind.RESTAURANT,
        }.get(capability)

    @staticmethod
    def _unit(kind: QuoteKind) -> QuoteUnit:
        return {
            QuoteKind.ACCOMMODATION: QuoteUnit.PER_ROOM_NIGHT,
            QuoteKind.TRANSPORT: QuoteUnit.PER_PERSON,
            QuoteKind.RESTAURANT: QuoteUnit.PER_PERSON_MEAL,
        }[kind]

    @staticmethod
    def _currency(token: str, source_uri: str) -> str | None:
        normalized = token.upper()
        if normalized in {"AUD", "A$", "AU$"}:
            return "AUD"
        if normalized in {"USD", "US$"}:
            return "USD"
        if normalized in {"CNY", "RMB", "¥", "￥"}:
            return "CNY"
        host = urlparse(source_uri).hostname or ""
        if host.endswith(".com.au") or host.endswith(".gov.au"):
            return "AUD"
        if host.startswith("us.") or host in {"expedia.com", "www.expedia.com"}:
            return "USD"
        return None

    @staticmethod
    def _location(request: TripRequest, evidence: Evidence) -> str | None:
        text = f"{evidence.metadata.get('search_title', '')} {evidence.claim}".casefold()
        for destination in request.destinations:
            aliases = DESTINATION_ALIASES.get(destination, (destination.casefold(),))
            if any(alias.casefold() in text for alias in aliases):
                return destination
        return request.destinations[0] if len(request.destinations) == 1 else None

    @staticmethod
    def _status(
        request: TripRequest,
        evidence: Evidence,
        location: str | None,
        kind: QuoteKind,
    ) -> tuple[QuoteStatus, str | None]:
        text = f"{evidence.metadata.get('search_title', '')} {evidence.claim}".casefold()
        required_terms = {
            QuoteKind.ACCOMMODATION: ("hotel", "accommodation", "住宿", "酒店"),
            QuoteKind.TRANSPORT: ("flight", "fare", "train", "bus", "机票", "航班", "火车"),
            QuoteKind.RESTAURANT: ("restaurant", "menu", "dining", "餐厅", "菜单", "人均"),
        }[kind]
        if not any(term in text for term in required_terms):
            return QuoteStatus.REJECTED, f"source content does not match {kind.value}"
        if kind is QuoteKind.RESTAURANT and any(
            term in text for term in ("flight", "airfare", "航班", "机票")
        ):
            return QuoteStatus.REJECTED, "flight result was returned for a restaurant query"
        years = {int(value) for value in YEAR_PATTERN.findall(text)}
        if years and request.start_date.year not in years:
            return QuoteStatus.REJECTED, "source dates do not match the trip year"
        if "price guide" in text and "average cost for two" in text:
            return QuoteStatus.REJECTED, "symbolic price guide is not a numeric per-person quote"
        if "melbourne" in text and any(token in text for token in ("florida", "orlando")):
            return QuoteStatus.REJECTED, "source refers to Melbourne, Florida"
        if location is None:
            return QuoteStatus.REJECTED, "destination could not be matched"
        exact_dates = request.start_date.isoformat() in text or request.end_date.isoformat() in text
        return (QuoteStatus.MATCHED, None) if exact_dates else (QuoteStatus.INDICATIVE, None)


class EvidenceBudgetBuilder:
    def __init__(
        self,
        *,
        extractor: WebQuoteExtractor | None = None,
        rate_provider: FxRateProvider | None = None,
    ) -> None:
        self.extractor = extractor or WebQuoteExtractor()
        self.rate_provider = rate_provider or FrankfurterFxRateProvider()

    async def build(
        self,
        request: TripRequest,
        evidence: tuple[Evidence, ...],
        itinerary: tuple[ItineraryItem, ...],
    ) -> BudgetLedger:
        quotes = self.extractor.extract(request, evidence)
        usable = tuple(quote for quote in quotes if quote.status is not QuoteStatus.REJECTED)
        rates = await self._rates({quote.currency for quote in usable}, request.currency)
        rate_map = {rate.base: rate.rate for rate in rates}
        rate_map[request.currency] = Decimal("1")
        components: list[BudgetComponent] = []
        unpriced: list[str] = []

        activity_cost = sum(
            (
                item.cost
                for item in itinerary
                if item.category not in {"food", "meal", "restaurant", "transport", "accommodation"}
                and item.metadata.get("cost_status") != "unknown"
            ),
            Decimal("0"),
        )
        if activity_cost:
            components.append(
                BudgetComponent(
                    kind="activities",
                    label="景点与体验（已估）",
                    amount_low=activity_cost,
                    amount_high=activity_cost,
                    currency=request.currency,
                    quantity=Decimal("1"),
                    note="来自已排程项目，不含待核价门票",
                )
            )

        rooms = self._room_count(request)
        allocations = allocate_destination_days(request)
        for destination in request.destinations:
            city_quotes = tuple(
                quote
                for quote in usable
                if quote.kind is QuoteKind.ACCOMMODATION
                and quote.location == destination
                and quote.currency in rate_map
            )
            nights = sum(1 for day in allocations if day.destination == destination)
            if destination == request.destinations[-1]:
                nights = max(0, nights - 1)
            self._append_quote_component(
                components,
                unpriced,
                city_quotes,
                rate_map,
                request.currency,
                kind=f"accommodation:{destination}",
                label=f"{destination}住宿",
                quantity=Decimal(rooms * nights),
                note=f"{rooms} 间 × {nights} 晚；网页指示价",
            )

        transport_scopes = [
            ("international_outbound", f"{request.origin} → {request.destinations[0]} 国际交通"),
            *(
                (f"intercity_{index + 1}", f"{origin} → {destination} 城际交通")
                for index, (origin, destination) in enumerate(
                    zip(request.destinations, request.destinations[1:], strict=False)
                )
            ),
            ("international_return", f"{request.destinations[-1]} → {request.origin} 国际交通"),
        ]
        for scope, label in transport_scopes:
            transport_quotes = tuple(
                quote
                for quote in usable
                if quote.kind is QuoteKind.TRANSPORT
                and quote.scope_key == scope
                and quote.currency in rate_map
            )
            self._append_quote_component(
                components,
                unpriced,
                transport_quotes,
                rate_map,
                request.currency,
                kind=f"transport:{scope}",
                label=label,
                quantity=Decimal(len(request.travelers)),
                note=f"单人价格 × {len(request.travelers)} 人；日期与行李额需下单页确认",
            )

        meal_count = sum(1 for item in itinerary if item.category in {"food", "meal", "restaurant"})
        restaurant_quotes = tuple(
            quote
            for quote in usable
            if quote.kind is QuoteKind.RESTAURANT and quote.currency in rate_map
        )
        self._append_quote_component(
            components,
            unpriced,
            restaurant_quotes,
            rate_map,
            request.currency,
            kind="restaurant:all_meals",
            label="行程内餐饮",
            quantity=Decimal(max(1, meal_count) * len(request.travelers)),
            note=f"人均价格 × {len(request.travelers)} 人 × {meal_count} 餐",
        )

        return BudgetLedger(
            currency=request.currency,
            total_low=sum((item.amount_low for item in components), Decimal("0")),
            total_high=sum((item.amount_high for item in components), Decimal("0")),
            components=tuple(components),
            quotes=quotes,
            fx_rates=rates,
            unpriced_kinds=tuple(dict.fromkeys(unpriced)),
            generated_at=datetime.now(UTC),
        )

    async def _rates(self, currencies: set[str], target: str) -> tuple[FxRate, ...]:
        rates: list[FxRate] = []
        for currency in sorted(currencies - {target}):
            try:
                rates.append(await self.rate_provider.get_rate(currency, target))
            except (httpx.HTTPError, KeyError, ValueError):
                continue
        return tuple(rates)

    @staticmethod
    def _append_quote_component(
        components: list[BudgetComponent],
        unpriced: list[str],
        quotes: tuple[QuoteFact, ...],
        rates: dict[str, Decimal],
        currency: str,
        *,
        kind: str,
        label: str,
        quantity: Decimal,
        note: str,
    ) -> None:
        if not quotes or quantity <= 0:
            unpriced.append(kind)
            return
        converted_lows = [quote.amount_low * rates[quote.currency] for quote in quotes]
        converted_highs = [quote.amount_high * rates[quote.currency] for quote in quotes]
        components.append(
            BudgetComponent(
                kind=kind,
                label=label,
                amount_low=(min(converted_lows) * quantity).quantize(Decimal("1")),
                amount_high=(max(converted_highs) * quantity).quantize(Decimal("1")),
                currency=currency,
                quantity=quantity,
                quote_ids=tuple(quote.id for quote in quotes),
                note=note,
            )
        )

    @staticmethod
    def _room_count(request: TripRequest) -> int:
        explicit_counts = []
        for raw in ROOM_PATTERN.findall(request.raw_requirement):
            if raw.isdigit():
                explicit_counts.append(int(raw))
            elif raw in CHINESE_NUMBERS:
                explicit_counts.append(CHINESE_NUMBERS[raw])
        if explicit_counts:
            return max(1, max(explicit_counts))
        return max(1, (len(request.travelers) + 1) // 2)

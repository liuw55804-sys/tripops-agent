import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from tripops.middleware import ToolExecutionEngine
from tripops.tools.models import RiskLevel, ToolDescriptor
from tripops.tools.registry import ToolRegistry

OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def install_live_travel_tools(
    registry: ToolRegistry,
    engine: ToolExecutionEngine,
    client: httpx.AsyncClient,
    *,
    tavily_api_key: str = "",
) -> frozenset[str]:
    """Install read-only travel providers and return their routed capabilities."""
    descriptors = (
        ToolDescriptor(
            name="live.open_meteo.forecast",
            description="Resolve a destination and retrieve an honest live weather forecast",
            capabilities=frozenset({"weather_search"}),
            allowed_agents=frozenset({"live_researcher"}),
            risk_level=RiskLevel.READ_ONLY,
            timeout_seconds=10,
            estimated_latency_ms=500,
            freshness_seconds=1800,
        ),
        ToolDescriptor(
            name="live.wikipedia.nearby_places",
            description="Find cited nearby places through Wikipedia geosearch",
            capabilities=frozenset({"poi_search", "accessibility_search"}),
            allowed_agents=frozenset({"live_researcher"}),
            risk_level=RiskLevel.READ_ONLY,
            timeout_seconds=12,
            estimated_latency_ms=900,
            freshness_seconds=86400,
        ),
    )
    for descriptor in descriptors:
        registry.register(descriptor)
    engine.register_handler(
        "live.open_meteo.forecast",
        lambda arguments: _weather_forecast(client, arguments),
    )
    engine.register_handler(
        "live.wikipedia.nearby_places",
        lambda arguments: _nearby_places(client, arguments),
    )
    capabilities = {capability for item in descriptors for capability in item.capabilities}

    if tavily_api_key.strip():
        web_descriptor = ToolDescriptor(
            name="live.tavily.search",
            description="Search current public web pages for travel facts and options",
            capabilities=frozenset(
                {
                    "transport_search",
                    "policy_search",
                    "poi_search",
                    "restaurant_search",
                    "accommodation_search",
                    "accessibility_search",
                }
            ),
            allowed_agents=frozenset({"live_researcher"}),
            risk_level=RiskLevel.READ_ONLY,
            timeout_seconds=15,
            estimated_latency_ms=1200,
            estimated_cost_units=1,
            freshness_seconds=3600,
        )
        registry.register(web_descriptor)
        engine.register_handler(
            web_descriptor.name,
            lambda arguments: _tavily_search(client, arguments, tavily_api_key),
        )
        capabilities.update(web_descriptor.capabilities)
    return frozenset(capabilities)


async def _weather_forecast(
    client: httpx.AsyncClient,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    destinations = [str(item) for item in arguments.get("destinations", [])]
    if len(destinations) > 1:
        payloads = [
            await _weather_forecast(
                client,
                {**arguments, "destinations": [], "location": destination},
            )
            for destination in destinations
        ]
        return {
            "summary": " ".join(str(payload["summary"]) for payload in payloads),
            "source_uri": OPEN_METEO_FORECAST_URL,
            "evidence": [
                evidence
                for payload in payloads
                for evidence in payload.get("evidence", [])
            ],
        }
    location = str(arguments["location"])
    start_date = date.fromisoformat(str(arguments["start_date"]))
    end_date = date.fromisoformat(str(arguments["end_date"]))
    resolved = await _geocode(client, location)
    source_uri = _url(OPEN_METEO_FORECAST_URL)
    horizon = datetime.now(UTC).date() + timedelta(days=16)
    if start_date > horizon or end_date < datetime.now(UTC).date():
        claim = (
            f"{resolved['name']} resolved to {resolved['latitude']},"
            f" {resolved['longitude']}; requested dates {start_date} to {end_date} "
            "are outside the live forecast horizon, so no forecast was fabricated."
        )
        return {
            "summary": claim,
            "source_uri": _url(OPEN_METEO_GEOCODING_URL),
            "evidence": [
                {
                    "claim": claim,
                    "source_name": "open-meteo",
                    "source_uri": _url(OPEN_METEO_GEOCODING_URL),
                    "confidence": 0.95,
                }
            ],
        }

    response = await client.get(
        OPEN_METEO_FORECAST_URL,
        params={
            "latitude": resolved["latitude"],
            "longitude": resolved["longitude"],
            "start_date": start_date.isoformat(),
            "end_date": min(end_date, horizon).isoformat(),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "timezone": "auto",
        },
    )
    response.raise_for_status()
    daily = response.json().get("daily", {})
    rain = daily.get("precipitation_probability_max", [])
    high = daily.get("temperature_2m_max", [])
    low = daily.get("temperature_2m_min", [])
    claim = (
        f"Open-Meteo forecast for {resolved['name']} covers {len(daily.get('time', []))} days; "
        f"temperature range {min(low):.0f}–{max(high):.0f}°C and maximum precipitation "
        f"probability {max(rain):.0f}%."
        if rain and high and low
        else f"Open-Meteo returned a forecast for {resolved['name']}."
    )
    return {
        "summary": claim,
        "source_uri": source_uri,
        "evidence": [
            {
                "claim": claim,
                "source_name": "open-meteo",
                "source_uri": source_uri,
                "confidence": 0.95,
            }
        ],
    }


async def _nearby_places(
    client: httpx.AsyncClient,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    destinations = [str(item) for item in arguments.get("destinations", [])]
    entries: list[dict[str, Any]] = []
    for destination in destinations:
        resolved = await _geocode(client, destination)
        response = await client.get(
            WIKIPEDIA_API_URL,
            params={
                "action": "query",
                "format": "json",
                "generator": "geosearch",
                "ggscoord": f"{resolved['latitude']}|{resolved['longitude']}",
                "ggsradius": 10000,
                "ggslimit": 30,
                "ggsnamespace": 0,
                "prop": "coordinates|extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "inprop": "url",
                "origin": "*",
            },
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        ordered = sorted(pages.values(), key=lambda item: item.get("index", 999))
        for page in ordered:
            title = str(page.get("title", "")).strip()
            extract = str(page.get("extract", "")).strip()
            source_uri = str(page.get("fullurl", "")).strip()
            if not title or not source_uri or title.casefold() == destination.casefold():
                continue
            if not _is_visit_candidate(title, extract):
                continue
            category, tags, period, indoor = _classify_place(title, extract)
            claim = extract[:350] or f"{title} is a documented place near {destination}."
            entries.append(
                {
                    "claim": claim,
                    "source_name": "wikipedia-geosearch",
                    "source_uri": source_uri,
                    "confidence": 0.82,
                    "candidate": {
                        "id": _stable_id("wiki", title, destination),
                        "title": title,
                        "location": destination,
                        "category": category,
                        "tags": sorted(tags),
                        "preferred_period": period,
                        "duration_minutes": 120,
                        "required_transit_minutes": 30,
                        "indoor": indoor,
                    },
                }
            )
    return {
        "summary": f"Wikipedia geosearch returned {len(entries)} cited nearby places.",
        "source_uri": WIKIPEDIA_API_URL,
        "evidence": entries,
    }


async def _tavily_search(
    client: httpx.AsyncClient,
    arguments: dict[str, Any],
    api_key: str,
) -> dict[str, Any]:
    capability = str(arguments.get("capability", "general_research"))
    query = str(arguments.get("query", "travel research"))
    response = await client.post(
        TAVILY_SEARCH_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "query": query,
            "search_depth": "basic",
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": False,
        },
    )
    response.raise_for_status()
    entries = []
    for result in response.json().get("results", []):
        title = str(result.get("title", "")).strip()
        source_uri = str(result.get("url", "")).strip()
        content = str(result.get("content", "")).strip()
        if not title or not source_uri or not content:
            continue
        entry: dict[str, Any] = {
            "claim": content[:700],
            "source_name": "tavily-web-search",
            "source_uri": source_uri,
            "confidence": min(0.95, max(0.55, float(result.get("score", 0.7)))),
        }
        entries.append(entry)
    return {
        "summary": f"Tavily returned {len(entries)} current web results for {capability}.",
        "source_uri": TAVILY_SEARCH_URL,
        "evidence": entries,
    }


async def _geocode(client: httpx.AsyncClient, location: str) -> dict[str, Any]:
    response = await client.get(
        OPEN_METEO_GEOCODING_URL,
        params={"name": location, "count": 1, "language": "zh", "format": "json"},
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        raise ValueError(f"location could not be resolved: {location}")
    return dict(results[0])


def _classify_place(
    title: str,
    extract: str,
) -> tuple[str, frozenset[str], str, bool]:
    text = f"{title} {extract}".casefold()
    rules = (
        ("museum", {"museum", "gallery", "art collection"}, "afternoon", True),
        ("park", {"park", "garden", "reserve"}, "morning", False),
        ("beach", {"beach", "coast", "harbour", "island"}, "morning", False),
        ("nature", {"mountain", "forest", "wildlife", "zoo"}, "morning", False),
        ("market", {"market", "shopping"}, "afternoon", True),
        ("culture", {"theatre", "opera", "historic", "heritage"}, "afternoon", True),
    )
    for category, keywords, period, indoor in rules:
        matched = {keyword for keyword in keywords if keyword in text}
        if matched:
            return category, frozenset({category, *matched}), period, indoor
    return "landmark", frozenset({"landmark", "culture"}), "afternoon", False


def _is_visit_candidate(title: str, extract: str) -> bool:
    text = f"{title} {extract}".casefold()
    excluded = {
        "accident",
        "bombing",
        "disaster",
        "election",
        "fire",
        "hailstorm",
        "history of",
        "murder",
        "outbreak",
        "pandemic",
        "protest",
        "radio station",
        "siege",
        "television station",
    }
    if re.match(r"^\d{4}\b", title) or any(term in text for term in excluded):
        return False
    place_terms = {
        "beach",
        "bridge",
        "cathedral",
        "church",
        "gallery",
        "garden",
        "harbour",
        "island",
        "library",
        "market",
        "monument",
        "museum",
        "opera",
        "park",
        "reserve",
        "temple",
        "theatre",
        "tower",
        "zoo",
    }
    return any(term in text for term in place_terms)


def _stable_id(prefix: str, title: str, location: str) -> str:
    digest = hashlib.sha1(f"{title}|{location}".encode()).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _url(value: str) -> str:
    return value

from datetime import datetime, timedelta

from fastmcp import FastMCP

mcp = FastMCP("tripops-transport")


@mcp.tool()
def search_options(
    origin: str,
    destination: str,
    departure_after: str,
    travelers: int = 1,
) -> list[dict[str, object]]:
    """Search deterministic transport options used by demos and offline tests."""

    start = datetime.fromisoformat(departure_after)
    return [
        {
            "option_id": f"rail-{index}",
            "mode": "rail",
            "origin": origin,
            "destination": destination,
            "departs_at": (start + timedelta(minutes=30 * index)).isoformat(),
            "arrives_at": (start + timedelta(minutes=30 * index + 90)).isoformat(),
            "total_price_cny": travelers * (180 + index * 30),
            "availability": "available",
            "is_mock": True,
        }
        for index in range(1, 4)
    ]


@mcp.tool()
def propose_rebooking(option_id: str, booking_id: str) -> dict[str, object]:
    """Create a proposal only; the host must require approval before calling."""

    return {
        "proposal_id": f"proposal-{booking_id}-{option_id}",
        "booking_id": booking_id,
        "option_id": option_id,
        "status": "proposal_only",
        "requires_confirmation": True,
        "is_mock": True,
    }


if __name__ == "__main__":
    mcp.run()


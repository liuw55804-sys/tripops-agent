from fastmcp import FastMCP

mcp = FastMCP("tripops-policy")

POLICIES = {
    "rail_refund": "Rail tickets in the demo may be refunded before departure with a 10% fee.",
    "hotel_cancellation": (
        "Demo hotels allow free cancellation until 18:00 two days before check-in."
    ),
    "travel_insurance": (
        "Weather disruption claims require an official cancellation or delay record."
    ),
}


@mcp.tool()
def search_policy(query: str) -> list[dict[str, str | bool]]:
    """Search the small deterministic policy corpus used by TripOps demos."""

    tokens = {token.lower() for token in query.replace("_", " ").split() if token}
    results = []
    for policy_id, content in POLICIES.items():
        searchable = f"{policy_id} {content}".lower()
        if not tokens or any(token in searchable for token in tokens):
            results.append(
                {
                    "policy_id": policy_id,
                    "content": content,
                    "source": "tripops-demo-policy",
                    "is_mock": True,
                }
            )
    return results


if __name__ == "__main__":
    mcp.run()

from datetime import date

from fastmcp import FastMCP

mcp = FastMCP("tripops-weather")


@mcp.tool()
def get_forecast(city: str, target_date: str) -> dict[str, object]:
    """Return deterministic demo weather with provenance and freshness metadata."""

    parsed = date.fromisoformat(target_date)
    rainy = (sum(ord(character) for character in city) + parsed.day) % 3 == 0
    return {
        "city": city,
        "date": parsed.isoformat(),
        "condition": "heavy_rain" if rainy else "partly_cloudy",
        "temperature_c": 22 if rainy else 26,
        "precipitation_probability": 0.85 if rainy else 0.2,
        "source": "tripops-demo-weather",
        "is_mock": True,
    }


if __name__ == "__main__":
    mcp.run()


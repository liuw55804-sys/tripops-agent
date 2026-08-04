FROM python:3.12-slim

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY skills ./skills
COPY config ./config
RUN uv sync --frozen --no-dev

EXPOSE 9900
CMD ["uv", "run", "tripops-api"]

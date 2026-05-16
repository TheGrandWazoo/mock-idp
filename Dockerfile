FROM python:3.14-slim

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/mock_idp ./mock_idp
COPY src/playground.html ./

EXPOSE 8080

ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "mock_idp.main:app", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE CHARTER.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# Developer preview: `docker compose run presence init /data`, then `... cycle /data --send`.
ENTRYPOINT ["uv", "run", "--no-sync", "presence"]
CMD ["--version"]

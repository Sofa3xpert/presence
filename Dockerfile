FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE CHARTER.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# Pre-alpha: prove the package is importable. The supervisor replaces this in W1.
CMD ["uv", "run", "python", "-c", "import presence; print(f'presence {presence.__version__} — pre-alpha; the supervisor arrives in W1')"]

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY app/ ./app/
RUN python -m pip install .

FROM base AS test
RUN python -m pip install '.[dev]'
COPY tests/ ./tests/
CMD ["sh", "-c", "ruff check app tests && pytest -q"]

FROM base AS runtime
RUN useradd --uid 10001 --create-home appuser
USER appuser
ENV PORT=8080
EXPOSE 8080
# Cloud Run supplies PORT. exec passes termination signals to Uvicorn.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

# syntax=docker/dockerfile:1
# Dependencies resolve once at the workspace root, then exactly one service's code is
# copied in. Services are not installed as packages — each exposes a top-level `app`, so
# only one may be present per image.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
RUN groupadd -r atmora && useradd -r -g atmora atmora
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --chown=atmora:atmora services/support/ /app/
USER atmora
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u,sys; \
      sys.exit(0 if u.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# syntax=docker/dockerfile:1
# Dependencies resolve once at the workspace root, then exactly one service's code is
# copied in. Services are not installed as packages — each exposes a top-level `app`, so
# only one may be present per image.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
# Built at the path it will run at: every venv script's shebang is absolute, and a venv
# built under /build and copied to /app cannot exec alembic or uvicorn. --no-editable
# installs platform-core into site-packages instead of a .pth pointing back at libs/.
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
RUN groupadd -r opsgrids && useradd -r -g opsgrids opsgrids
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --chown=opsgrids:opsgrids services/timesheet/ /app/
USER opsgrids
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u,sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

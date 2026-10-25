# syntax=docker/dockerfile:1
# One image recipe for every Python service, parameterised by workspace package (plan M1).
#   docker build -f infra/docker/python.Dockerfile --build-arg PACKAGE=orchestrix-api .
# uv is pinned to the version that wrote uv.lock; the lockfile is used as-is (--frozen).

ARG PYTHON_IMAGE=python:3.13.17-slim-trixie

FROM ghcr.io/astral-sh/uv:0.12.0 AS uv

FROM ${PYTHON_IMAGE} AS builder
ARG PACKAGE
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    test -n "${PACKAGE}" && \
    uv sync --frozen --no-dev --no-editable --package "${PACKAGE}"

FROM ${PYTHON_IMAGE}
RUN groupadd --system --gid 10001 orchestrix && \
    useradd --system --uid 10001 --gid orchestrix --home-dir /app --no-create-home orchestrix
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001

# syntax=docker/dockerfile:1.7

# Backend 與 worker 共用同一組鎖定依賴；base image 可由部署環境覆蓋。
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.27
ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm
FROM ${UV_IMAGE} AS uv_bin
FROM ${PYTHON_IMAGE} AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY --from=uv_bin /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false
WORKDIR /app

# scikit-learn / scipy 的 OpenMP runtime；healthcheck 使用 Python 標準函式庫。
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid "${APP_GID}" patent \
    && useradd --system --uid "${APP_UID}" --gid patent --home-dir /app patent

COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY backend ./backend

# PatentSBERTa 已在 backend/models 內，建置後不需在正式環境重新下載。
RUN mkdir -p /app/data/model_artifacts /app/output \
    && chown -R patent:patent /app

USER patent
EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

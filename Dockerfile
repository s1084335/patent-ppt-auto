# syntax=docker/dockerfile:1.7

# One production image is shared by backend and worker.  Runtime keeps GPU-capable
# Python dependencies, but model weights and data are mounted or downloaded.
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
RUN uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

# HF_HUB_OFFLINE／TRANSFORMERS_OFFLINE 刻意**不設**（2026-07-23 定案：方案 B）。
# 原本兩者皆為 1，但 image 不含 PatentSBERTa（837MB，見下方 .dockerignore 排除），
# offline 模式下找不到權重不會下載、直接拋錯，Railway worker 一跑 embeddings 必 crash。
# 改由 backend.app.deploy 於啟動時確保權重就位（缺就下載到 MODEL_ARTIFACT_ROOT），
# 之後推論仍走 local_files_only=True，維持「權重 SHA-256 當模型版本」的可重現性。
# HF_HOME 指向 /app/data 之下，掛 volume 即可跨重啟保留，不必每次冷啟動重抓。
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/data/hf_cache \
    TOKENIZERS_PARALLELISM=false
WORKDIR /app

# scikit-learn and scipy need the OpenMP runtime.  Keep apt packages minimal.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid "${APP_GID}" patent \
    && useradd --system --uid "${APP_UID}" --gid patent --home-dir /app patent

COPY --from=builder --chown=patent:patent /app/.venv /app/.venv
COPY --chown=patent:patent alembic.ini ./
COPY --chown=patent:patent alembic ./alembic
COPY --chown=patent:patent backend ./backend
COPY --chown=patent:patent scripts ./scripts
COPY --chown=patent:patent sql ./sql

# PatentSBERTa is intentionally excluded from the image (837MB).  It is downloaded
# on first start into MODEL_ARTIFACT_ROOT (/app/data/model_artifacts/PatentSBERTa).
# Mount a volume at /app/data to keep both the weights and the HF cache across
# restarts; without one the download repeats on every cold start.
RUN mkdir -p /app/data/model_artifacts /app/data/hf_cache /app/output \
    && chown -R patent:patent /app /app/data /app/output

USER patent
EXPOSE 8000

# APP_ROLE=backend uses the platform PORT; APP_ROLE=worker runs the worker shim.
CMD ["python", "-m", "backend.app.deploy"]

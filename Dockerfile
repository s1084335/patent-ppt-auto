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

# COPY 時直接給 owner：避免事後 chown -R 遍歷 .venv（數 GB、數十萬檔）——
# Windows Docker Desktop overlay 上該遞迴要 600s+ 且複寫出等大的重複 layer（export 超時主因）。
COPY --from=builder --chown=patent:patent /app/.venv /app/.venv
COPY --chown=patent:patent alembic.ini ./
COPY --chown=patent:patent alembic ./alembic
COPY --chown=patent:patent backend ./backend

# PatentSBERTa 已在 backend/models 內，建置後不需在正式環境重新下載。
# chown 只點名兩個新建的空目錄（非遞迴掃全樹），成本趨近零。
RUN mkdir -p /app/data/model_artifacts /app/output \
    && chown patent:patent /app /app/data /app/data/model_artifacts /app/output

USER patent
EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

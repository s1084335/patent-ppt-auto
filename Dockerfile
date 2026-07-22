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

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
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

# PatentSBERTa is intentionally excluded from the image.  Mount or download it
# under MODEL_ARTIFACT_ROOT, usually /app/data/model_artifacts/PatentSBERTa.
RUN mkdir -p /app/data/model_artifacts /app/output \
    && chown patent:patent /app /app/data /app/data/model_artifacts /app/output

USER patent
EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

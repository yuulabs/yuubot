# Stage 1: build yuutrace frontend
FROM node:22-slim AS ui-builder
WORKDIR /ui
COPY yuutrace/ui/package.json yuutrace/ui/package-lock.json ./
RUN npm ci
COPY yuutrace/ui ./
RUN npm run build:app

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/yuubot-src/.venv \
    YUUBOT_CONFIG=/config/config.yaml \
    YUU_DEPLOYMENT_MODE=container \
    YUU_WORKSPACE_ROOT=/workspace \
    TZ=Asia/Shanghai

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        sqlite3 \
        tzdata \
        vim-tiny \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -sf /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /opt/yuubot-src

# Layer 1: install external deps only — cached unless pyproject.toml/uv.lock changes
COPY pyproject.toml uv.lock ./
COPY yuuagents/pyproject.toml ./yuuagents/pyproject.toml
COPY yuubot/pyproject.toml    ./yuubot/pyproject.toml
COPY yuullm/pyproject.toml    ./yuullm/pyproject.toml
COPY yuutools/pyproject.toml  ./yuutools/pyproject.toml
COPY yuutrace/pyproject.toml  ./yuutrace/pyproject.toml

RUN uv sync --frozen --package yuubot --no-dev --no-install-workspace

# Layer 2: copy source and install local workspace packages (fast, no downloads)
COPY yuuagents ./yuuagents
COPY yuubot    ./yuubot
COPY yuullm    ./yuullm
COPY yuutools  ./yuutools
COPY yuutrace  ./yuutrace

RUN uv sync --frozen --package yuubot --no-dev \
    && ln -sf /opt/yuubot-src/.venv/bin/ybot /usr/local/bin/ybot \
    && ln -sf /opt/yuubot-src/.venv/bin/ytrace /usr/local/bin/ytrace

# Overlay the freshly-built frontend assets over whatever was committed to the repo
COPY --from=ui-builder /ui/dist/app/ ./yuutrace/src/yuutrace/cli/_static/

COPY yuubot/docker/entrypoint.sh /usr/local/bin/yuubot-entrypoint
RUN chmod +x /usr/local/bin/yuubot-entrypoint

ENTRYPOINT ["yuubot-entrypoint"]

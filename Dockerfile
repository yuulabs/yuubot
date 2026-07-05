# Build with: DOCKER_BUILDKIT=1 docker compose build

FROM node:22-slim AS admin-builder

ARG PNPM_VERSION=10.12.1

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@${PNPM_VERSION} --activate

COPY web/package.json web/pnpm-lock.yaml ./
RUN echo "onlyBuiltDependencies='*'" > .npmrc && \
    pnpm config set registry https://registry.npmmirror.com/ && \
    pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm run build

FROM python:3.14-slim

ARG UV_VERSION=0.9.13
ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/yuubot/.venv \
    YUUBOT_CONFIG=/config/config.yaml \
    TZ=Asia/Shanghai

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git sqlite3 tzdata

RUN --mount=type=cache,target=/root/.cache/uv \
    curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh && \
    ln -sf /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /opt/yuubot

ENV UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

COPY src ./src
COPY --from=admin-builder /app/dist/ ./web/dist/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev && \
    ln -sf /opt/yuubot/.venv/bin/ybot /usr/local/bin/ybot && \
    ln -sf /opt/yuubot/.venv/bin/yuubot /usr/local/bin/yuubot

COPY docker/entrypoint.sh /usr/local/bin/yuubot-entrypoint
RUN chmod +x /usr/local/bin/yuubot-entrypoint

EXPOSE 8765

ENTRYPOINT ["yuubot-entrypoint"]

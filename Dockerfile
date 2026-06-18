# Build context: local monorepo root (agent-kits/)
# Dockerfile path: yuubot-v2/Dockerfile
#
# Caching strategy:
#   - BuildKit cache mounts persist apt/pnpm/uv downloads across rebuilds
#   - Dependency layers only invalidate when pyproject.toml or lockfiles change
#   - Source code changes only rebuild Layer 2 (fast, no downloads)
#   - Pin pnpm and uv versions for reproducibility and cache hits
#   - CN mirrors for apt, npm, and PyPI to speed up builds in China
#
# Build with: DOCKER_BUILDKIT=1 docker compose build

# ── Stage 1: Build admin frontend ──────────────────────────────────────────────
FROM node:22-slim AS admin-builder

ARG PNPM_VERSION=10.12.1

WORKDIR /app

# Install pnpm (pinned version for reproducibility)
RUN corepack enable && corepack prepare pnpm@${PNPM_VERSION} --activate

# Install deps first (cached unless package.json or pnpm-lock.yaml change)
# Use npmmirror (official CN mirror) for faster downloads
COPY yuubot-v2/web/package.json yuubot-v2/web/pnpm-lock.yaml ./
RUN echo "onlyBuiltDependencies='*'" > .npmrc && \
    pnpm config set registry https://registry.npmmirror.com/ && \
    pnpm install --frozen-lockfile

# Build frontend (cached unless web source changes)
COPY yuubot-v2/web/ ./
RUN pnpm run build
# Output: /app/dist/

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.14-slim

ARG UV_VERSION=0.9.13
ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/yuubot-src/.venv \
    YUUBOT_CONFIG=/config/config.yaml \
    TZ=Asia/Shanghai

# Use TUNA mirror for apt
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources

# System deps — cache mount avoids re-downloading apt packages
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git sqlite3 tzdata

# Install uv (pinned version) — cache mount avoids re-downloading if version unchanged
RUN --mount=type=cache,target=/root/.cache/uv \
    curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh && \
    ln -sf /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /opt/yuubot-src

# Use TUNA mirror for PyPI (uv sync reads this env var)
ENV UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

# Layer 1: install external deps only — cached unless pyproject.toml/uv.lock change.
# yuubot-v2 is copied as yuubot so the workspace member path resolves correctly.
# NOTE: yuubot-v2/uv.lock is the v2 lockfile (includes cryptography etc.).
#       The local monorepo root uv.lock was generated for v1 yuubot and is missing deps.
COPY pyproject.toml ./
COPY yuubot-v2/uv.lock ./uv.lock
COPY yuuagents/pyproject.toml    ./yuuagents/pyproject.toml
COPY yuubot-v2/pyproject.toml   ./yuubot/pyproject.toml
COPY yuullm/pyproject.toml       ./yuullm/pyproject.toml
COPY yuutools/pyproject.toml     ./yuutools/pyproject.toml
COPY yuutrace/pyproject.toml     ./yuutrace/pyproject.toml

# Fix yuubot's uv.sources: workspace members must use { workspace = true }
# instead of { path = "../...", editable = true } when inside a workspace.
RUN sed -i 's|{ path = "../yuuagents", editable = true }|{ workspace = true }|' \
        ./yuubot/pyproject.toml \
    && sed -i 's|{ path = "../yuutrace", editable = true }|{ workspace = true }|' \
        ./yuubot/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package yuubot --no-dev --no-install-workspace

# Layer 2: copy source and install local workspace packages (fast, no downloads)
COPY yuuagents  ./yuuagents
COPY yuubot-v2  ./yuubot
COPY yuullm     ./yuullm
COPY yuutools   ./yuutools
COPY yuutrace   ./yuutrace
COPY --from=admin-builder /app/dist/ /opt/yuubot-src/admin-web/dist/

# Fix yuubot's uv.sources again (source copy overwrote the sed fix from layer 1)
RUN sed -i 's|{ path = "../yuuagents", editable = true }|{ workspace = true }|' \
        ./yuubot/pyproject.toml \
    && sed -i 's|{ path = "../yuutrace", editable = true }|{ workspace = true }|' \
        ./yuubot/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package yuubot --no-dev && \
    ln -sf /opt/yuubot-src/.venv/bin/ybot /usr/local/bin/ybot && \
    ln -sf /opt/yuubot-src/.venv/bin/ytrace /usr/local/bin/ytrace

COPY yuubot-v2/docker/entrypoint.sh /usr/local/bin/yuubot-entrypoint
RUN chmod +x /usr/local/bin/yuubot-entrypoint

EXPOSE 8780 8781

ENTRYPOINT ["yuubot-entrypoint"]
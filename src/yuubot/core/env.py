"""Centralized environment variables for yuubot runtime context."""

import os

TASK_ID = "YUU_TASK_ID"
IN_BOT = "YUU_IN_BOT"
BOT_CTX = "YUU_BOT_CTX"
USER_ID = "YUU_USER_ID"
USER_ROLE = "YUU_USER_ROLE"
AGENT_NAME = "YUU_AGENT_NAME"
DEPLOYMENT_MODE = "YUU_DEPLOYMENT_MODE"
WORKSPACE_ROOT = "YUU_WORKSPACE_ROOT"

_ALL_KEYS = (TASK_ID, IN_BOT, BOT_CTX, USER_ID, USER_ROLE, AGENT_NAME,
             DEPLOYMENT_MODE, WORKSPACE_ROOT)


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

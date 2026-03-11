"""Centralized environment variables for skill CLI context.

所有 YUU_* 环境变量在此定义。Skill CLI 和 agent_runner 通过此模块读写。
以后重构上下文传递机制时只改这一个文件。
"""

import os

TASK_ID = "YUU_TASK_ID"
IN_BOT = "YUU_IN_BOT"
BOT_CTX = "YUU_BOT_CTX"
USER_ID = "YUU_USER_ID"
USER_ROLE = "YUU_USER_ROLE"
AGENT_NAME = "YUU_AGENT_NAME"
DOCKER_HOST_MOUNT = "YUU_DOCKER_HOST_MOUNT"
DOCKER_HOME_HOST_DIR = "YUU_DOCKER_HOME_HOST_DIR"
DOCKER_HOME_DIR = "YUU_DOCKER_HOME_DIR"

_ALL_KEYS = (TASK_ID, IN_BOT, BOT_CTX, USER_ID, USER_ROLE, AGENT_NAME,
             DOCKER_HOST_MOUNT, DOCKER_HOME_HOST_DIR, DOCKER_HOME_DIR)


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

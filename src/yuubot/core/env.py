"""Centralized environment variables for skill CLI context.

所有 YUU_* 环境变量在此定义。Skill CLI 和 agent_runner 通过此模块读写。
以后重构上下文传递机制时只改这一个文件。
"""

import os
from contextlib import contextmanager

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


@contextmanager
def task_env(
    *,
    task_id: str,
    ctx_id: int | str = "",
    user_id: int | str = "",
    user_role: str = "",
    agent_name: str = "",
    docker_host_mount: str = "",
    docker_home_host_dir: str = "",
    docker_home_dir: str = "",
):
    """Set all YUU_* env vars for the duration of an agent run."""
    prev = {k: os.environ.get(k) for k in _ALL_KEYS}

    os.environ[TASK_ID] = task_id
    os.environ[IN_BOT] = "1"
    if ctx_id:
        os.environ[BOT_CTX] = str(ctx_id)
    if user_id:
        os.environ[USER_ID] = str(user_id)
    if user_role:
        os.environ[USER_ROLE] = user_role
    if agent_name:
        os.environ[AGENT_NAME] = agent_name
    if docker_host_mount:
        os.environ[DOCKER_HOST_MOUNT] = docker_host_mount
    if docker_home_host_dir:
        os.environ[DOCKER_HOME_HOST_DIR] = docker_home_host_dir
    if docker_home_dir:
        os.environ[DOCKER_HOME_DIR] = docker_home_dir

    try:
        yield
    finally:
        for k in _ALL_KEYS:
            if prev[k] is not None:
                os.environ[k] = prev[k]
            else:
                os.environ.pop(k, None)

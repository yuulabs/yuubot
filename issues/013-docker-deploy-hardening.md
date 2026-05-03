# Issue 013: Docker 部署安全与可运维性缺口

**Severity**: High
**Source**: Docker deployment audit 2026-05-02

## 问题

当前 Docker 部署链路已经可以生成 compose bundle、构建镜像并启动 NapCat/yuubot/traces-ui，但还不适合作为默认生产部署形态。

主要缺口：

1. Admin 面板默认发布到宿主机端口，且 `admin.secret` 为空时放行所有请求。
2. Admin 的文件浏览和终端 WebSocket 没有统一挂认证；终端会直接启动容器内 `/bin/bash -l`。
3. 文件 API 允许绝对路径，存在任意目录浏览/上传风险。
4. `traces-ui` 将 `/data` 以只读方式挂载，但 yuutrace UI 打开 SQLite 时会执行 schema/WAL 写入，可能导致容器启动失败或重启循环。
5. monorepo 根目录缺少 `.dockerignore`，build context 会包含 `.venv`、`node_modules`、cache 等大目录。
6. `docker_config.yaml` 查找依赖当前工作目录或包源码位置，没有明确跟随 `docker.source_root`。

## 风险

- 如果在服务器上开放端口，Admin 可能成为未认证远程 shell。
- Docker 更新时 traces-ui 可能被重建后无法正常启动。
- build 速度慢、上下文过大，也可能误把本地敏感文件送入 Docker build context。
- 从非 repo 目录运行 `ybot docker init/install/update` 时，部署 bundle 可能缺少 Docker 覆盖配置。

## 待处理

- Docker 默认将 Admin 绑定到 `127.0.0.1`，或要求显式开启公网发布。
- 要求 Docker 部署必须设置 `admin.secret`，并给 `/files/*`、`/terminal/ws` 统一认证。
- 限制文件 API 到 `/workspace` 或显式白名单。
- 修复 traces-ui 只读挂载，或让 yuutrace UI 支持真正 readonly 打开数据库。
- 在 monorepo 根添加 `.dockerignore`。
- 让 Docker bundle 生成逻辑按 `repo_root` 查找 `docker_config.yaml`。

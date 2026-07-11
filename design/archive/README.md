# 历史设计归档

本目录中的文档已经过时，只用于理解历史背景、被放弃的方案和迁移过程，**不得作为当前
实现依据**。当前系统设计、扩展点与外部 facade 的唯一权威入口是
[`design/system-design.md`](../system-design.md)。

归档时保留原始正文，并在每份文档顶部标记过时。文档之间发生冲突时，裁决顺序是：

1. 当前代码；
2. Git 修改日期较新的文档；
3. Git 修改日期较旧的文档。

当前归档分组：

- `design.md`、`lifecycle.md`、`service-surface.md`：早期整体架构方案；
- `services/`：按实现顺序编写的历史服务设计，包含部分未落地接口；
- `frontend-migrate/`：阶段性前端迁移计划；
- `deployment/`：已被当前 listener 配置和边界实现取代的部署设计。

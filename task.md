一、违背开闭原则 (OCP)
[OCP] 中 src/yuubot/web/ws.py:36-70 —— handle_ws_command 用 7 连 if command_type == ... 手工分派 WS 命令，且部分命令（interrupt/cancel）的业务逻辑直接内联在分派函数里；新增命令必须改动分派链。改进：改为 dict[str, Handler] 注册表 + 每命令一个 msgspec payload 结构。

[OCP] 中 src/yuubot/cli/__init__.py:83-141 —— 命令分派是长 if args.command == ... 链，且 db/upgrade 子命令还要二级组合判断（args.command == "upgrade" and args.upgrade_command == "check"）；新增命令需同时改 parser 构建区和分派链两处。改进：用 subparser.set_defaults(func=...) 让 argparse 承担分派。

[OCP] 低 src/yuubot/web/routes/terminal.py:32-57 —— 终端 WS 命令同样是手工 if 链分派（open/input/resize/close），规模小但与 ws.py 是同一种模式重复。改进：与 WS 命令分派统一为注册表模式。

[OCP] 低 src/yuubot/web/routes/admin.py:855-858 —— SPA catch-all 里 if path.startswith("api/") 硬编码路径前缀做手工路由裁决。改进：将 API 前缀常量化并由 boundary 层统一裁决。

二、抽象泄露 / hack
[抽象泄露] 中 src/yuubot/web/routes/admin.py:590 —— body.owner.split(":conv:", 1)[0].removeprefix("actor:") 在 route 层手工解析 stringly-typed 的 owner 编码格式，owner 格式知识泄露到 HTTP 层，格式一变此处静默出错。改进：在 runtime/tasks 域提供 parse_owner() 或结构化 Owner 类型。

[hack] 低 src/yuubot/cli/commands.py:204 —— server._server.started 直接访问 UvicornServer 的私有 uvicorn 实例，探测启动状态。改进：在 UvicornServer 上暴露 started 属性或 wait_started()。

[定位] 低 src/yuubot/web/html.py:4-136 —— 136 行内嵌 HTML+JS 的手写 demo 页，仅作为 React dist 缺失时的 fallback（admin.py:92-97, 862）。它是与正式 React 管理台并行的第二套 UI，功能重叠、无 escape 的 {actor_id} 插值（第 5 行，actor id 可控时有轻微 XSS 面）。改进：降级为纯文本"请构建前端"提示页，删掉整套内嵌 JS 客户端。

三、绕过类型系统
[类型] 中 src/yuubot/web/routes/admin.py:142-149, 458-463 —— admin_interrupt 与 api_configure_integration 均用裸 dict + 手工 isinstance 校验（raw.get("all") is True、isinstance(name_value, str)），旁边所有端点都有 Body struct。改进：补 InterruptBody、IntegrationConfigBody msgspec 结构。

[类型] 低 src/yuubot/web/boundary.py:84-89 —— wrap_admin_auth(..., sessions: object) 故意声明为 object 再运行时 isinstance 检查抛 TypeError，把静态类型检查降级为运行时断言。改进：直接注解为 SessionStore（顶部 import 无循环依赖问题，auth.py 不 import boundary）。

[类型] 低 src/yuubot/web/routes/admin.py:637-647 —— CreateCronJobBody.schedule/action 是 dict[str, object]，handler 内再二次 msgspec.convert + 函数内 from ...runtime.cron import ... 延迟 import。改进：body 直接声明 CronSchedule 与 action union 类型，import 提到模块顶部。

四、错误抽象带来的性能问题
[性能] 中 src/yuubot/web/routes/admin.py:186-190 —— /api/providers 对每个 provider 串行 await list_model_cards(record.id)，是 N+1 顺序 IO。改进：一次批量查询所有 model cards 后按 provider 分组，或 asyncio.gather。

五、非数据库迁移的兼容代码（项目规则：兼容层即技术债）
[兼容-豁免确认] src/yuubot/cli/commands.py:243-272, 275-374 —— old_config_data/legacy_db_from_old_config/auto_legacy_db/migrate_command 均服务于 migrate 数据库迁移命令，属规则豁免范围，不计为债务。

未发现的问题（排除项）
全目录无 cast( / type: ignore；
路径穿越防护（safe_workspace_path/ensure_contained）在 files.py 中使用一致；
boundary.py / auth.py 的 ASGI 中间件分层清晰，is_actor_inbound 的硬编码正则（auth.py:53）算轻微的路由知识泄露到 auth 层，但作为 loopback 白名单尚可接受。
整体健康度
中等偏上：分层骨架（boundary→auth→admin/public、service facade、msgspec Body）是健康的，主要债务集中在 admin.py 这个未拆完的注册中心、部分 PUT 端点的裸 dict 中转惯用法、以及"全量 bootstrap 快照当万能响应"带来的隐性性能税——都属于可渐进偿还的结构性债，而非腐烂性债。



  4. 中：EventBus 队列无界
     src/yuubot/runtime/events.py:31 是无界 asyncio.Queue。正常 startup() 会启动 listener，但如果只 Yuubot.create()
     后直接跑对话，或 listener 消费慢于 emit，事件队列会增长。buffer 是 bounded 100，但 queue 不是。

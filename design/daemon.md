# Daemon 进程设计

## 职责

Daemon 是 yuubot 的核心进程，负责：
1. **接收事件** — 连接 Recorder 内部 WS，接收转发的消息事件
2. **命令解析** — 树形命令匹配 + 权限检查
3. **Agent 驱动** — 使用 yuuagents SDK 创建并运行 Agent
4. **定时任务** — Cron 触发主动模式

## 启动方式

```bash
ybot up [--config config.yaml]
```

## 模块设计

### app.py — FastAPI 应用

```python
app = FastAPI()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动
    config = load_config()
    ws_client = WSClient(config.recorder.relay_ws)
    dispatcher = Dispatcher(config)
    scheduler = Scheduler(config)
    agent_runner = AgentRunner(config)
    
    await ws_client.connect()
    scheduler.start()
    
    yield
    
    # 关闭
    scheduler.stop()
    await ws_client.close()
```

FastAPI 同时提供：
- 健康检查接口 `GET /health`
- Agent 状态查询 `GET /agent/status`
- 手动触发 Agent `POST /agent/trigger`（调试用）

### ws_client.py — Recorder WS 客户端

```python
class WSClient:
    """连接 Recorder 内部 WS，接收事件"""
    
    async def connect(self):
        """连接并自动重连"""
        
    async def on_event(self, event: dict):
        """收到事件，交给 dispatcher"""
        await self.dispatcher.dispatch(event)
```

特性：
- 自动重连（Recorder 重启时）
- 心跳保活

### dispatcher.py — 消息分发

```python
class Dispatcher:
    """消息分发器"""
    
    def __init__(self, config, command_tree, agent_runner):
        self.command_tree = command_tree
        self.agent_runner = agent_runner
        self.queue = asyncio.Queue()  # 全局消息队列
    
    async def dispatch(self, event: dict):
        """分发事件"""
        if event["post_type"] != "message":
            return  # 暂时只处理消息事件
        
        # 1. 检查是否需要响应（at模式/free模式）
        if not self.should_respond(event):
            return
        
        # 2. 命令解析
        match = self.command_tree.match(event["message"])
        if match is None:
            return  # 不是命令，忽略
        
        # 3. 权限检查
        role = self.get_role(event["user_id"])
        if not match.command.check_permission(role):
            return  # 权限不足
        
        # 4. 放入队列（全局单 Agent，排队处理）
        await self.queue.put((match, event))
    
    async def process_loop(self):
        """消费队列，逐个处理"""
        while True:
            match, event = await self.queue.get()
            await self.agent_runner.run(match, event)
```

**全局单 Agent**：所有消息排队处理，避免并发资源竞争。队列保证先来先服务。

### scheduler.py — 定时任务

```python
class Scheduler:
    """Cron 定时任务，触发主动模式"""
    
    def __init__(self, config, agent_runner):
        self.scheduler = AsyncIOScheduler()
        
        # 从配置加载定时任务
        for job in config.cron_jobs:
            self.scheduler.add_job(
                self.trigger_agent,
                CronTrigger.from_crontab(job.cron),
                args=[job.task, job.ctx_id],
            )
    
    async def trigger_agent(self, task: str, ctx_id: int | None):
        """定时触发 Agent"""
        await self.agent_runner.run_scheduled(task, ctx_id)
```

### agent_runner.py — Agent 运行器

```python
import yuutools as yt
from yuuagents import Agent
from yuuagents.agent import AgentConfig, SimplePromptBuilder
from yuuagents.loop import run as run_agent

class AgentRunner:
    """使用 yuuagents SDK 创建并运行 Agent"""
    
    def __init__(self, config):
        self.config = config
        self.tool_manager = self._setup_tools()
    
    def _setup_tools(self) -> yt.ToolManager:
        """注册所有 tools"""
        tm = yt.ToolManager()
        
        # 注册内置工具
        from yuuagents import tools
        for tool in tools.get(["execute_bash"]):
            tm.register(tool)
        
        # execute_bash 已经足够调用 ybot CLI skills
        # Agent 通过 execute_bash("ybot im send ...") 来使用 skills
        
        return tm
    
    async def run(self, match, event):
        """被动模式：处理命令触发的 Agent 任务"""
        ctx_id = event["ctx_id"]
        message = event["message"]
        
        prompt_builder = SimplePromptBuilder()
        prompt_builder.add_section(self.config.agent.persona)
        prompt_builder.add_section(self._load_skills_docs())
        
        config = AgentConfig(
            agent_id=f"yuubot-{ctx_id}",
            persona=self.config.agent.persona,
            tools=self.tool_manager,
            llm=self.llm_client,
            prompt_builder=prompt_builder,
        )
        
        agent = Agent(config=config)
        task = self._build_task(match, message, ctx_id)
        await run_agent(agent, task=task, ctx=self.context)
    
    async def run_scheduled(self, task: str, ctx_id: int | None):
        """主动模式：定时触发的 Agent 任务"""
        # 类似 run()，但 task 来自配置而非用户消息
        ...
    
    def _load_skills_docs(self) -> str:
        """加载 skills 文档，注入到 Agent prompt"""
        # 扫描 ~/.yagents/skills/ 下的 SKILL.md
        from yuuagents.skills import scan, render
        return render(scan(self.config.skills.paths))
    
    def _build_task(self, match, message, ctx_id) -> str:
        """构建 Agent 任务描述"""
        return ...

**关键设计**：Agent 通过 `execute_bash` 调用 `ybot` CLI skills。这样 skills 是独立的 CLI 工具，agent 不需要特殊的 tool 注册，只需要知道 CLI 用法（通过 SKILL.md 文档注入 prompt）。

## 响应规则

| 场景 | 默认行为 |
|------|----------|
| Master 私聊 | free 模式，直接响应 |
| 其他人私聊 | 需要 `/bot allow-dm` 开启 |
| 群聊 @bot | at 模式，响应 |
| 群聊命令（free模式） | 需要 Master 开启 `/bot on --free` |

## 配置项

```yaml
daemon:
  # Recorder 内部 WS 地址
  recorder_ws: "ws://127.0.0.1:8766"
  
  # FastAPI 服务
  api:
    host: "127.0.0.1"
    port: 8780
  
  # Agent 配置
  agent:
    persona: "你是一个有用的QQ机器人助手"
    skills:
      - im
      - web
      - mem
  
  # 定时任务
  cron_jobs:
    - task: "检查待办事项并提醒"
      cron: "0 9 * * *"  # 每天9点
      ctx_id: 1           # 发送到指定 ctx
```

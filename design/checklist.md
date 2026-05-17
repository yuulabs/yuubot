## Milestones

1. web ui发布
2. skills管理 + skills配置 + opencode集成
3. linear/plane 的python functions集成（从而使得bot可以参与project management）
4. w&b + swanlab + github集成
5. bridge功能回连


### 功能点

#### 持久化系统

yuubot有一个启动时可配置的DATA PATH. yuubot的所有数据均需位于该路径下。在docker部署时，该路径应当挂载至主机以实现数据保存。

data path为方便，记作 data*/

文件夹层次如下：

```shell
data*/
    integrations/
        <integration-name>/ #每个integration自己的资源
    yuubot/ #yuubot的各种数据所在地。包括但不限于配置，gateway路由，各种数据，还有yuutrace数据etc
    workspace/ #actor的workspace
        <actor-name>/ 
    skills/ #标准的skill目录。 见 https://agentskills.io/home
```

导出功能十分简单：将data文件夹打包成压缩包。导入则是解压并替换。

#### Gateway

Gateway支持按glob对source path分流消息

Gateway的前端面板将会比较简单：

一个表格

|Source|Pattern|Actor| (以及其他必要字段)
|System|*|*|
|telegram|group.*|Amy|

具体消息来源之类的由各Integration自己渲染好。

#### 插件系统

1. 插件系统规范 初始化 （资源分配） & 调用（input/output schema） & 配置 约定。 每个Integration提供一组Capability供Actor/Agent调用。
2. 无论是builtin integration还是external integration
3. 插件产生的每一条消息有一个唯一id. 插件必须提供一个response方法，该方法传入消息id和需要发送的消息，表示“对该消息id的原路返回回应”，用于系统进行反馈。api为 `response(target_msg_id, msg, poke)`.  `poke`对应贴表情（如果平台支持，不支持可静默），用于系统快速响应输入（以避免用户干等）。`msg`则是文字内容，通常用于报错。二者传一个即可。

插件有自己的专门面板进行特定的配置。
Actor页面中，每个Actor可以选择启用哪些Integration（对应着它们是否能看到这些Integration对应的facade functions）

##### 内置Integration

##### Admin Chat

该插件提供借由Admin WebUI进行协作的 1v1 对话能力。

##### OpenCode/Codex集成

这两个插件逻辑基本一致。以Opencode为例：

Opencode集成将会托管本地的opencode登录信息。用户启用Opencode集成之后需要自行使用pty完成opencode的connector配置（对于codex则是登录chatgpt账号）。在那之后，opencode Integration将会接管opencode的auth.json及其相关配置/skills仓库（即.config/opencode & .agent/skills）。Agent将会获得如下函数：

1. `oc_client = yext.opencode.client(connection=paramiko | None)` 创建一个代理对象。可配置远程（ssh能连上的）
2. `await oc_client.sync()`. 同步目标和本地的opencode配置。如果目标就在本地等于什么都不做。否则等于在远程安装opencode并将本地配置复制过去.
3. `response, session = await oc_client.run(prompt, agent = ...,)` 运行一次opencode，返回最后的回复和session id. 见opencode run的cli参数。注意该方法很容易长期阻塞，agent应当使用
4. `oc_client.list_agents()` 展示可用的agent.

用户需要自己维护OC Agent的设定（例如人设，使用什么模型）。插件将会提供一个前端页面来方便完成这件事（一个json编辑器，用于编辑opencode 的 config.json）

> 经过考虑我认为continue session没有必要，因为对于agent来说它不应该续聊一个会话，通常如果一次没搞定，续聊只会越来越垃圾，浪费budget. 

##### Web工具

通过Tavily提供的web search进行搜索。自己编写web read.

agent获得一个 `web.search/web.read`端点。

web.search返回字符串。而web read返回Pages对象（list[str]）. 模型自行通过re等手段（或者delegate给一个sub agent摘要）查找所需内容。

##### Telegram & Discord

IM集成.

##### Github / Linear / Notion / Lark / Google Calendar

工作管理和协作。

#### Trace系统

转发Yuutrace的UI做可观测性 & yuubot做好成本记录和聚合。yuubot需要自己编写成本仪表盘。

#### Web PTY

允许用户通过web pty连入远程终端进行debug. 支持ctrl+c/v的复制粘贴和vim操作。

#### Web FS

允许用户直接拖拽上传下载文件。

#### LLM Provider管理

#### Character、Agent和Actor

Character：人设管理。需要一个专门的页面方便用户编写人设（编辑器和多人设浏览器）。需要提供模板库方便用户copy paste常用指令（例如一些环境设定）

Agent：Agent = Character + Skills (&是否展开) + Available tool providers(见Yuuagents provider) + Available Functions(system functions) Agent可以通过expand functions过滤掉不想要的功能。

Character & Agent都属于配置范畴（它们没有活跃实例，可以被多次引用传递）。在前端中可以方便地复制它们（例如创造一个轻微不同的变体）。

Actor: Actor = ActorType（代码中写死的）+ Agent配置（主Agent+Agent用什么LLM Provider的模型驱动） + 一堆Integrations + 各种资源。

ActorType随代码演进而变化（通常代表着一种Multi Agent交互算法）。在一开始，支持一种类型的Actor，核心逻辑大致为：


```py
integration.response(new_msg.id, poke="working")
while not budget.money.not_enough():
    agent.step()
    if new_msgs := mailbox.drain():
        agent.extend_new_msgs(new_msgs) ##实时追加
        integration = new_msgs[-1].source
    if history tokens > compaction tokens: #自动rollover
        agent.step("Hey请你总结一下.....")
        agent = Agent(character, history=[agent.first_user_message,"你的任务还未完成，上一轮的总结如下：" , agent.last_response, "请继续完成任务。"], pysession=agent.pysession)
    if agent.done():
        break
if not agent.done() & budget.money.not_enough():
    integration.response(new_msgs[-1].id, "没钱了任务也没完成")

if hang 1 hour: 关闭agent. 关闭其他资源例如python session.
```

关键细节在于每一步（tool execution之后）之间drain掉mailbox，如果超过了compaction tokens上限则自动总结然后rollover. 自动总结使用的是追加法(以利用prompt caching)，同时继承其他资源（例如python session）。一段时间无输入后静默。

对于每个Actor，可以看到它的下辖资源。每类Actor通常需要定制UI. 就当前来说，需要看到Actor的定时任务。

yuubot会提供一些预设Character & Agent & Actor. 这是通过直接往数据库里插入数据做到的，仅限初次启动或者恢复初始状态时触发。

#### SKILLS管理

yuubot在自身的持久化资源下自带了一个skills仓库。在前端页面中，可以方便地浏览所有SKILLS和编辑（非常类似于Character管理，都是大量条目 + description + 编辑器）

yuubot的skill管理特点在于：允许在system prompt预先展开skills以提高缓存命中率。yuubot在设计哲学上避免污染其他coding cli的skills目录，以确保信息准确隔离。

yuubot通过提供工具 load_skills 来允许Agent动态阅读skills. load_skills根据输入的skills的 名称 加载skills文档（以及元信息，例如skill路径）。这基本上是标准实践。

### 用户使用流程

#### 安装与初始化

提供一个交互式脚本，直接 `curl .... | bash`执行完事儿。用户需要做的准备：

1. 域名（or localhost）
2. TLS加密证书（非localhost情况）
3. 配置所有服务端口。
4. 配置Master Key用于登录Admin. 登陆后可以再设置密码。

然后就应当可以访问Admin页面。登录之后，用户需要去LLM Provider页面配置供应商（提供预设，只需写api key）。随后，去Actor页面配置Agent(预设) + LLM models(`provider/model:effort`格式)，即可启动Actor, 紧接着web chat就将可用。

用户再去逐个配置插件以启用他需要的功能。支持OAuth跳转认证。

#### 理想使用流程

用户在IM/Linear/Github Comment等地方 at yuubot的某个actor名字, 给出信息，actor进行排查和委托开发（例如具体编码问题委托给opencode）

yuubot的核心功能是上下文搜寻，定向，编排等，主要在于为用户提供快速的海量信息过滤，以及利用Overnight时间。编码等专业任务交给专业工具。常见的使用场景应该是数据分析和项目管理。
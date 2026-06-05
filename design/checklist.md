## Milestones

1. Web UI发布
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
    persistent-paths/ #符号链接目录。用于管理需要持久化的外部依赖，下述
```

导出功能十分简单：将data文件夹打包成压缩包。导入则是解压并替换。

###### 链接系统

1. 初始化：当用户通过admin配置一条持久化路径时（路径为部署目标上的路径），Yuubot会先将该路径复制到data*/persistent-paths 下，随后在原处创建一个符号链接，通过这种方式，该路径将可以被持久化。同时记录该映射。
2. 导入：导入时，yuubot将会检查路径映射并尝试重映射。若有，则用户必须在import时显式指定替换策略: 1. 主机优先（覆盖持久化路径） 2. 持久化优先（覆盖主机） 3. 无冲突合并. 无冲突合并指的是yuubot将尝试将两个文件夹合并，但如果有任何冲突（同层级的同名文件夹/文件），Yuubot将抛出失败（不会修改任何文件）。用户必须提前手动维护好确保两边没有冲突。导入支持dry-run，ybot会汇报所有的冲突路径。 import在不指定策略时默认为dry-run，以防止意外覆盖。

#### Gateway

Gateway支持按glob对source path分流消息

Gateway的前端面板将会比较简单：

一个表格

|Source|Pattern|Actor| (以及其他必要字段)
|telegram|group.*|Amy|

具体消息来源之类的由各Integration自己渲染好。

Gateway只负责外部Integration消息的分流。前端Gateway配置表只展示Integration source，不展示System路径。System消息不再伪装成Integration消息，也不再走Gateway glob路由；System消息由yuubot内部组件直接投递到Actor mailbox。

System消息包括但不限于：

1. Web UI消息
2. 定时任务触发
3. Admin操作触发
4. bridge回连触发
5. 后台任务状态变化

System消息应当优先复用yuuagents基础设施：

1. 统一进入`yuuagents.mailbox.MailBox`
2. 具体消息类型继承`yuuagents.mailbox.MailMessage`
3. 后台任务完成继续复用`BackgroundCompletedMessage`
4. Actor运行、Agent loop、Runtime、EventBus、trace等能力继续由yuuagents承担

yuubot只在自身边界内定义具体的System消息类型，例如Web UI消息、Admin消息、系统通知消息等。`MailMessage`本身已经是通用mailbox消息抽象，不需要为了yuubot再改yuuagents增加新的通用基类。

系统内投递通过一个很薄的System ingress完成：

```py
await system_ingress.send(actor_id, message)
```

该入口只负责Actor存活检查、权限/来源校验、trace元数据等系统边界工作；不负责Integration格式转换。

#### System/Web Chat通道

这些能力属于yuubot自身运行时：

1. Web Chat / Web UI对话
2. 定时任务触发
3. Admin操作触发
4. bridge回连触发
5. 后台任务状态变化

##### Web Chat

Web Chat属于yuubot的first-party System/UI通道。

- Admin持有浏览器WebSocket连接并负责session认证。
- 用户消息由Admin通过内部System ingress直接投递到目标Actor mailbox。
- Web UI消息使用yuubot定义的`MailMessage`子类表达，例如携带`actor_id`、`agent_name`、`session_id`、`message_id`、`content`等字段。
- Actor runtime在yuubot适配层识别Web UI消息，将其追加到目标Agent上下文并运行一轮。
- Actor回复、流式输出、复杂交互状态通过Web UI delivery/stream channel返回给Admin，再由Admin推送给浏览器WebSocket连接。

Dialog（`dialog:<uuid>`）作为Web UI session/thread标识。数据（历史消息、dialog列表、前端交互状态等）存放在Web UI/System通道自己的持久化目录中，不需要进入Integration storage，也不需要进入平台DB，除非后续明确需要资源化管理。

Web UI通道不是Integration，因此不提供`integration.response()`。它可以直接支持Integration消息难以表达的内容，例如：

1. token级流式输出
2. partial message更新
3. buttons/forms/files等复杂交互
4. cancel/interrupt/approve/continue等控制消息
5. 与前端session绑定的状态同步

#### Integration插件系统

Integration插件指连接外部平台、外部工具、外部服务或外部运行时的能力模块。它可以产生外部消息，也可以向Actor/Agent暴露一组Capability。

Integration插件分为两类：

1. 内置Integration：提前写在yuubot代码里的Integration插件，由代码中的factory注册和维护。
2. 外部Integration：安装到数据目录中的外部插件，通常通过manifest、子进程和HTTP facade与daemon通信。

System/Web Chat通道不属于Integration插件系统。Web UI、定时任务、Admin操作、bridge回连、后台任务状态变化等System能力不应出现在插件列表、Integration列表、Gateway source选择器或Actor的Integration启用列表中。

Integration插件系统规范包括 初始化（资源分配） & 调用（input/output schema） & 配置 约定。每个Integration提供一组Capability供Actor/Agent调用。

1. 无论是内置Integration还是外部Integration，都遵守相同的Integration生命周期和Capability调用边界。
2. 内置Integration可以复用代码内的实现细节，但对Actor暴露的边界仍然是Integration capability，而不是System facade。
3. 插件产生的每一条消息有一个唯一id. 插件必须提供一个response方法，该方法传入消息id和需要发送的消息，表示“对该消息id的原路返回回应”，用于系统进行反馈。api为 `response(target_msg_id, msg, react)`.  `react`对应贴表情（如果平台支持，不支持可静默），用于系统快速响应输入（以避免用户干等）。`msg`则是文字内容，通常用于报错。二者传一个即可。

Integration插件有自己的专门面板进行特定的配置。
Actor页面中，每个Actor可以选择启用哪些Integration插件（对应着它们是否能看到这些Integration对应的facade functions）。

##### 内置Integration（代码内置插件）

以下条目是计划中的内置Integration插件。它们提前写在yuubot代码里，但仍然属于Integration插件系统，和System/Web Chat通道是两类能力。

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
integration.response(new_msg.id, react="working")
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

#### Actor Facade

Actor可见的Python facade分为两类：

1. `yb`：yuubot内置、手写、first-party facade
2. `yext`：Integration/外部插件生成的facade

这是一个明确的架构边界，而不是两套实现的临时过渡：

1. 不删除自动生成框架。外部Integration和外部插件的函数集合在运行时才由manifest/capability schema确定，仍然必须通过生成代码暴露给Actor。
2. 不在生成目录里合并手写代码。生成产物可以被随时重建，手写模块不能放进`yext`包或actor-local generated package中，否则会产生覆盖、缓存、命名冲突和调试困难。
3. 手写facade只进入`yb`。所有yuubot first-party/system能力都应当通过`yb`表达。
4. 生成facade只进入`yext`。`yext`只表达Integration capability，不承载yuubot system helper。

`yb`用于暴露yuubot系统内能力，例如：

1. `yb.actor`：Actor自身状态、当前上下文、必要的控制能力
2. `yb.webui`：Web UI delivery、流式输出、复杂交互
3. `yb.tasks`：后台任务提交、取消、状态查询
4. `yb.schedule`：定时任务查询和管理
5. `yb.delegate`: 将任务代理给其他Agent. 

`yb`是手写模块。它可以直接表达yuubot内部对象和复杂交互，不需要被Integration capability schema限制。`yb`仍然需要遵守Actor权限边界，不能因为是内置模块就绕过权限控制。

`yext`继续用于Integration和外部插件。由于yuubot不能提前知道外部插件暴露的函数内容，`yext`通过capability schema生成Python facade是合理的。

Actor启动时，Python runtime同时注入`yb`和`yext`：

```py
import yb
import yext
```

`yb`和`yext`共享Actor上下文（actor id、agent name、session id、mailbox id等），但调用边界不同：

1. `yb`调用yuubot内置System bridge或直接使用内部受控接口
2. `yext`调用Integration invoke bridge，并最终进入Integration capability

因此，后台任务、Web UI streaming、Actor自身状态等system helper应当从`yext`移出，放在`yb.tasks`、`yb.webui`、`yb.actor`等手写模块中。`yext`生成代码只保留schema驱动的Integration function wrapper。

#### SKILLS管理

yuubot在自身的持久化资源下自带了一个skills仓库。在前端页面中，可以方便地浏览所有SKILLS和编辑（非常类似于Character管理，都是大量条目 + description + 编辑器）

yuubot的skill管理特点在于：允许在system prompt预先展开skills以提高缓存命中率。yuubot在设计哲学上避免污染其他coding cli的skills目录，以确保信息准确隔离。

yuubot通过提供工具 load_skills 来允许Agent动态阅读skills. load_skills根据输入的skills的 名称 加载skills文档（以及元信息，例如skill路径）。这基本上是标准实践。

#### Yuu Network

Yuu Network是Ybot bridge的进一步想法。它不应当强依赖完整ybot安装，而应当抽成一个独立的轻量工具，暂称`ynet`。

`ynet`负责把外部机器接入到某个Actor维护的资源网络中。对于很多资源节点来说，它们并不希望安装完整ybot，只需要安装`ynet client`，然后向ybot中的指定Actor公开自己的资源即可。

0.1版本只考虑一个Yuu Network绑定一个Actor。这个Actor对应网络中的agent node，也是唯一能看到和使用resource node的主体。多Actor共享、授权、隔离等问题暂不设计。

节点类型暂定如下：

1. agent node: 由ybot中的某个Actor持有，负责建立Yuu Network、接收节点加入、维护节点列表，并把资源变化通知给Actor。
2. resource node: 安装`ynet client`的外部机器，向agent node公开自己的资源。
3. relay node: 未来可选的中继节点。0.1版本可以不实现。

核心安全目标保持简单：

1. resource node需要确认自己加入的是正确的agent node，避免资源泄漏。
2. agent node需要把resource node返回的内容视作外部输入，避免把资源节点上的命令输出、文件内容、HTTP响应等当作系统指令执行。

##### 建立

当某个Actor启用Yuu Network时，ybot会为它启动agent node，并开放一个加入端点。该加入端点称为yuu network gate，记作YNetG。

YNetG可以是一个URL。用户把这个URL和一次性join token交给外部机器，外部机器通过`ynet client`加入网络。

##### 加入

一个节点可通过访问ynet gate加入网络。该节点必须向网络传递身份验证材料：

1. 在agent node上预先生成的join token。join token用于首次加入，不应复用Admin master key。

resource node加入时由`ynet client`在本机生成密钥对。私钥留在resource node本机，公钥发送给agent node。join token验证通过后，agent node记录该公钥，后续通讯改用密钥对完成身份确认和加密。

当身份验证通过之后，节点必须立刻向网络声明自己的属性。列表如下：

1. version. 表明自己的协议版本。以便于网络确定是否兼容。x.y.z格式。
2. name. 一个易读的标记名。
3. node type. 表明自己的节点类型。0.1版本主要是resource。
4. profile. 表明自己所具有的资源/能力。
    description: 字符串，用于向agent汇报可读的能力内容。例如，“This machine contains 2 cpu cores and 2 A100s. It's good for ML experiments workload.”
    resources: 一个列表，声明自己的可用资源。每个元素形如 {type: cpu, count: 2}, {type: gpu, spec: A100, count: 2}, {type: http, endpoint: ~, description: "使用post传递xx, yy参数，可以达到zz效果"}

`description`主要给Actor理解资源节点用途。实际调用资源时，仍以具体的resource条目和后续暴露的访问方式为准。

一旦校验通过，agent node将会为resource node生成一个uuid作为网内唯一索引，并生成一个<name>-<hex6>的short id便于Actor理解和检索。agent node随后返回uuid、short id和反向隧道连接信息。

随后，resource node上的`ynet client`将与agent node建立反向隧道。反向隧道成为该节点的访问路径。网络中会产生一条通知，通知Actor有新节点加入，并同时附上其profile。

##### 退出

当resource node退出时，它需要通过`ynet client`向agent node发送退出消息。agent node清理该节点的连接状态，然后通知Actor。

##### 故障

当resource node故障时（掉线，或者resources被查出来不能用，例如http端点掉线），agent node会通知Actor该节点故障；如果resource node还活着，也会向该节点发还一条提醒消息。如果该节点彻底故障了（整机挂掉），通常只能依赖人工重连。

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

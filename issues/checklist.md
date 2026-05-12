预期目标形态：

## 入口

入口通过web admin面板提供。支持登录验证。每个Actor有一个专属的对话框。有一个控制面板，用于配置是否启用integration；对每个actor配置是否启用某个integration（对应着将channel和mailbox绑定）。配置Character, LLM Provider, Agent(主要通过expand functions控制), Prompt Templates，SKILLS仓库。能够通过

prompt->character -> Agent(provider/model:effort) -> Actor 来新建actor启动服务. 提供充分的预设。

包括一个监控页面。可以看到Agent trace & cost成本分析

还包括一个pty & 文件拖拽上传到目录用于紧急Debug。

## Actor启动

启动一个Actor涉及配置特定role的Agent，对应的模型以及Budget。对于初版，只需配置main agent. 注意模型是在actor层次配置的而不是agent内部。 

### 配置Agent

配置Agent涉及到配置它的Character（人设，即完整system prompt），SKILLS（见agent skills标准），可用工具和expand_functions. 

### PromptHub & SKILL Hub

提供一个管理页面管理所有的Prompt Templates和skills以便于复制/插入。skills暂时只管理SKILL MD而没有script（环境配置有点麻烦，可以作为以后的feature）

## 监控页面

监控页面包含了一个yuutrace页面用于监控agent内部对话流细节和一个成本分析panel. 这需要底层trace/usage打通。

## TODOs

- [todo-trace-cost-backend.md](todo-trace-cost-backend.md) — Trace / Cost 后端接线关键风险；不是单纯 UI，需先打通 yuutrace、usage/cost、pricing/budget 校验
- [todo-integration-plugin-mechanism.md](todo-integration-plugin-mechanism.md) — Integration plugin 发现机制，支持第三方通过 pip install 接入新通信渠道

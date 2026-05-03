## Yuubot V1

经过长时间的反复试错之后，yuubot的基本形态已经可以确定：

一个Agent核心，位于主服务器上，包含管理页面等。可以操控该主机。

此外，还有一组扩展能力接口。目前，扩展能力依赖于自己写函数。这可以封装一些必要的需求，但是也限制了扩展能力的速度以及易用程度。

V1旨在并入两项核心概念，以及提供更成熟的管理面板和云端体验，以便于可以上机部署。

同时V1将调整yuubot的体验重心：AI-driven Project Management & 轻度闲聊。水群等功能不再作为核心推进。重点在于配合AI进行项目管理。上云将是这一段时间的主旋律。

## 热配置

目前yuubot大量依赖于文件配置。但在V1, 文件配置将只是初始配置。除infra配置以外，配置都可以在内存中进行更改（允许webui进行更改），落库持久化。得益于yuubot的prompt系统，将它们可视化将非常简单。

## Skills

https://agentskills.io/home

Agent Skills已然成为扩展能力的事实标准。Yuubot也势必要接入这一系统。

Agent Skills的标准如上所述，不再赘述，yuubot将复用它们的定义。Yuubot的特点在于可以配置预加载：在Agent Definition中可以添加SkillRefSection, bot将会预加载这些skills的定义。这同样可以通过webui进行配置，从而提高bot的专业性。

## Bridge

V1中最重要的概念登场——

假设有两个服务器 A 和 B. yuubot运行在A服务器上，它将公开一个特殊的 bridge address. 在该端口上，yuubot将使用HTTPS监听特殊的通信协议；服务器B（持有重要资源，如GPU）则访问该地址，进行握手。B必须提供A设定的管理员密钥（由管理员在启动B时提供），B必须验证A的证书以防止被中间人攻击导致资源泄露。验证通过之后，B将提供预期的标识名，资源规格等。A则分配一个端口，SSH指纹，配置SSH反向转发，使得agent可以写正常的ssh b-name代码。

以上介绍了双方认证的过程。实际Bridge协议中将会有更多语义信息，以帮助Agent了解该node能够提供什么样的资源，其来源，配额等等信息。

所以Agent有两种方式使用外部资源：1. 已知的SSH target（例如管理员手动配置的，通过api分配的GPU节点等） 2. bridge连回的（即缺乏中心化管理时的节点加入，例如一个家宽节点，提供各种IM登录服务，但是该节点PC会休眠，只能靠它自己连回来；又或者用户手机，etc）。

### Gateway

V1将让消息与qq平台解耦。 因为V1将有多种不同的消息渠道：

1. IM消息（含回显消息（Im账号在别的地方手动发的 & bot自己发的，用于bot维持消息记录完整））
2. web端消息
3. project management类app的消息, etc

### Coding CLI集成

提供专门的 python functions, 用于在远程配置coding cli（同步本地的auth文件 & skills等），将编码问题卸载到更加专业化的coding agent身上，bot自己不再负责复杂编码。

> 这里要注意计费问题。很可能有点困难。
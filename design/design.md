## yuubot的设计逻辑

yuubot主要是提供skills增强yuuagents，通过消息驱动agent, 以及对外的qq bot接口.


## 消息

yuubot提供两套消息方案。

1. 被动式。yuubot会在接收到消息的时候，根据消息内容和内置指令解析规则决定是否启动agent. 此时，yuubot将会向agent传递一个ctx id用于通信。该ctx id标定了此消息的来源，避免agent手动指定复杂的群聊/私聊。ctx id是一个自增整数（避免uuid太复杂导致llm犯错），当一个群聊/私聊的消息第一次被收到时被标记。该映射关系会存入数据库，每次启动bot时热加载到内存（因为本来就不大）。
2. 主动式。yuubot不定期拉起agent。此时agent可能通过im进行列表的检查，也就有可能（也可能不）发送一些消息。此时必须明确指定消息去向。

## skills

yuubot提供一个 `ybot skills install <skill_name>`  命令. 该命令会在当前虚拟环境中安装skill_name所需要的依赖，并配置相关的SKILLS.md至yuuagents的skills目录下。 `~/.yagents/skills/<skill_name>`. 每个skill都是一个cli命令，形如 `ybot <name> params`

## skill list

uid指的是 `im_name:type:id`，例如 `qq:group:12345`. type可以是private/group

- im: 查询im的各种信息。
    - send. `ybot im send <msg> --ctx <ctx_id>/uid`. 发送消息到ctx_id/uid。msg是一个json字符串。格式见后。
    - search. `ybot im search "<msg list>" --ctx <ctx_id>/uid`. 搜索im中包含<msg list>中的任意一个消息的消息(msg list是空格分隔的字符串)。
    - list. `ybot im list <private/group/channel>` etc. 接口还没完善。总之，就是展示1.自己的好友，2.自己的群聊3.某个群聊里面的群成员（限制人数）。
- web 使用网络功能。该skill的实现参考 [agent_read.py], 人类需要提前进行一些登录，然后bot会不断复用这些登录信息。
    - search. `ybot web search "<query>"`. 调用搜索引擎搜索query。
    - read. `ybot web read "<url>"`. 读取url的内容。
    - download. `ybot web download "<urls>" folder`. 下载url(s)的内容。folder是下载的文件夹（文件将被放入该文件夹）。特别注意的是，这个folder指的是本机路径。该工具支持批量下载，即url可以是一个多行字符串，每个文件占一行。
- mem 记忆。agent可以使用mem skill来记忆，检索之前的记忆。这允许agent记录例如群友的偏好之类的信息。每条mem都有一个唯一的id，用于检索。mem不能储存过长的内容。
    - save. `ybot mem save "<mem>" --tags <tag1>,<tag2>,... --ctx <ctx_id>/uid`. 记忆mem。tags是一个逗号分隔的字符串，用于分类记忆。ctx是可选的。
    - recall. `ybot mem recall "words" --tags "tags" --ctx <ctx_id>/uid`. 检索记忆中包含words中的任意一个单词，且包含tags中的任意一个tag的记忆。words和tags都是空格分隔的字符串。words和tags任一不为空即可。ctx是可选的。返回一列记忆id及其内容。
    - delete. `ybot mem delete <ids>`. 删除ids对应的记忆。ids是一个逗号分隔的字符串。
    - show tags `ybot mem show --tags --ctx <ctx_id>/uid`. 显示ctx下所有tag. ctx是可选的。
    虽说如此， mem具有一个自动遗忘系统。如果一个记忆长时间没有被检索到，那么它就会被自动删除。默认情况下，记忆会被保留三个月。用户可以通过 `ybot mem config --forget-days <days>` 来修改这个默认值。

## 实现

暂时只考虑对接napcat实现qq接口。Im, web, mem都被做成cli工具。yuubot会启动一个daemon（这个daemon将使用yuuagents SDK创建agent）。daemon会在有消息进入时解析消息内容，根据规则（也就是解析代码本身）决定是否唤起agent。如果有，那么daemon会将消息转换成msg格式同时包括必要的上下文（ctx id），然后丢给agent作为第一条消息。

### 消息格式

```json
[
    {"type":"text", "text":"hello"},
    {"type":"image", "url":"https://example.com/image.jpg"},
    {"type":"at", "qq":"123456"}
]
```

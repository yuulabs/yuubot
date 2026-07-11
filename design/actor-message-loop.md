# Actor Message Loop

> 本文是 [`system-design.md`](system-design.md) 的消息循环专题补充；系统整体设计与外部
> facade 以该文档为准。

尽管Actor可以任意选择如何消费消息。

但这里给出一个default示例：


```
conv = None

while not mailbox.empty():
    blocking = conv is None
    new_msg = mailbox.get(blocking)
    if new_msg.conversation_id is not None:
        explicit_conv = conversations.get_or_create(new_msg.conversation_id)
        explicit_conv.append(new_msg)
        explicit_conv.call_llm()
        continue
    if exceeding ttl:
        conv = None
    conv = conv if conv else new_conv
    conv.append(new_msg) #两次工具调用之间插入新消息
    conv.call_llm()
    conv.execute_tools()
```

该循环可以更加复杂（例如消息过多时开多个Conversation），但基本插队原理如上。该插队模式可在未来扩展至前端对话发送follow-up消息。

Cron 的 `actor_message` 与 integration webhook 一样投递为 `conversation_id=None` 的普通 user input，由该默认 loop 决定复用或新建 conversation。Cron 的 `conversation_callback` 则必须带 owner conversation，作为 developer notice + continuation 处理。

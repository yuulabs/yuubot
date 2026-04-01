"""rendering.py — 所有 LLM 可见文本的唯一出处。

每个类对应一个场景，类注释说明 prompt 构成，方法按时序排列。
调用者直接 import 对应的类，按约定调用方法，无需额外基类或注册机制。
"""


def render_system(*, persona: str, addon_docs: str) -> str:
    return f"{persona}\n\n{addon_docs}" if addon_docs else persona


class ConversationRender:
    """yllm 会话场景。

    Prompt 构成（按时序）：
      system()              → render_system(persona, addon_docs)
      user_new()            → location + msg_xml + memory_hints + ctx 指令
      user_continuation()   → count_hint? + msg_xml + memory_hints
      tool_result()         → 透传
      tool_error()          → "错误: {error}"
    """

    @staticmethod
    def system(*, persona: str, addon_docs: str) -> str:
        return render_system(persona=persona, addon_docs=addon_docs)

    @staticmethod
    def location(*, chat_type: str, group_id: int, group_name: str,
                 ctx_id: int, include_name: bool = True) -> str:
        if chat_type == "group":
            if include_name and group_name:
                return f"群聊「{group_name}」(group_id={group_id}, ctx={ctx_id})"
            return f"群聊 (group_id={group_id}, ctx={ctx_id})"
        return f"私聊 (ctx={ctx_id})"

    @staticmethod
    def memory_hint(*, snippets: list[dict]) -> str:
        """Format memory probe results as snippet hints.

        Each snippet dict has {id, content, tags}.
        """
        lines = ["相关记忆命中:"]
        for s in snippets:
            content = s["content"]
            if len(content) > 80:
                content = content[:77] + "…"
            tag_part = f" ({s['tags']})" if s.get("tags") else ""
            lines.append(f"- [mem {s['id']}]{tag_part} {content}")
        lines.append("（可用 mem recall 查看更多详情）")
        return "\n".join(lines) + "\n"

    @staticmethod
    def group_topic_hint(*, topic: str) -> str:
        return f"当前群话题: {topic}\n"

    @staticmethod
    def user_new(*, location: str, msg_xml: str, memory_hints: str, ctx_id: int) -> str:
        return f"""你收到了来自{location}的消息。
{msg_xml}
{memory_hints}
回复时使用 im send 命令发送到 ctx {ctx_id}。遇到奇怪的问题时可使用 im 工具查看上下文。你自己生成的回复不会被看到，简单输出结束即可。
"""

    @staticmethod
    def user_continuation(
        *,
        total_msgs: int,
        msg_xml: str,
        memory_hints: str,
        truncated: bool = False,
        trigger_count: int = 1,
    ) -> str:
        truncation = "（过长，已截断到最近10条）" if truncated else ""
        trigger_hint = (
            f"请回复其中所有直接 @你 或使用 /yllm 触发你的消息（共 {trigger_count} 条）。"
        )
        return (
            f"下面是你上次回复后到现在的群聊片段，共 {total_msgs} 条{truncation}:\n"
            f"{msg_xml}\n"
            f"{trigger_hint}\n"
            f"{memory_hints}"
        )

    @staticmethod
    def tool_result(result: str) -> str:
        return result  # 透传，LLM 直接看原始结果

    @staticmethod
    def tool_error(error: str) -> str:
        return f"错误: {error}"


class CronRender:
    """定时任务主动触发场景。

    Prompt 构成：
      system()          → render_system(persona, addon_docs)
      user_trigger()    → 触发说明 + ctx
      tool_result()     → 透传
      tool_error()      → "错误: {error}"
    """

    @staticmethod
    def system(*, persona: str, addon_docs: str) -> str:
        return render_system(persona=persona, addon_docs=addon_docs)

    @staticmethod
    def user_trigger(*, cron_expr: str, ctx_id: int) -> str:
        return f"定时任务触发（{cron_expr}）。当前会话 ctx={ctx_id}。"

    @staticmethod
    def tool_result(result: str) -> str:
        return result

    @staticmethod
    def tool_error(error: str) -> str:
        return f"错误: {error}"

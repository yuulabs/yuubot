# Artifact Delivery 契约 —— 对话视图内联图片投递

> 本文档定义 yuubot Agent 在 Admin 对话视图内**内联投递图片**的能力、交付管线、
> Agent 侧语法契约、前置条件与约束。它是该能力的单一事实来源：任何修改
> `_figure_delivery_bullets` 或 Agg backend / workspace browser 的变更都必须
> 遵守本契约，且不得在未更新本文档的情况下破坏内联图片投递。
>
> 关联文档：`system-prompt.md` §1.2 以一行指引指向本文档；本文档不复制五段式 prompt
> 结构契约，只描述能力本身。

---

## 1. 能力

AI 可在 Admin 对话视图**内联投递图片**，无需上传到外部图床、无需外链。
Agent 通过 `execute_python` 调用 `plt.savefig('artifacts/<name>.png')` 将图片落盘到
当前工作区，随后在回复文本中以 markdown 图片语法引用该文件；Admin 对话视图的
ReactMarkdown 渲染器将其转为 `<img>`，用户即可看到图片。

这是 `1e808b3`（fix(assembly): force Agg backend and teach figure-delivery contract）
之后新增的用户可见能力：在此之前 Agent 无法向用户展示图片，只能伪造外链。

---

## 2. 交付管线（scenario trace）

内联图片投递横跨三个独立的参与者：Agent kernel（写文件）、Admin workspace
browser（以正确 MIME 服务文件）、对话视图 ReactMarkdown（渲染 markdown 图片）。
任一环节缺失，图片都不会显示。

```text
Agent execute_python 调 plt.savefig('artifacts/x.png')
  → 文件落盘 <workspace_path>/artifacts/x.png
     （= <data_dir>/workspace/<segment>/artifacts/x.png，<segment> 即 CapabilitySet.workspace_path）
  → Agent 在回复文本中输出 ![alt](/workspace/<segment>/artifacts/x.png)
    → Admin workspace_browser 以 GET /workspace/<segment>/artifacts/x.png 服务（正确 MIME）
      → 对话视图 ReactMarkdown 将 ![alt](/workspace/<segment>/artifacts/x.png) 渲染为 <img>
        → 用户在对话流中看到图片
```

要点：

- **写入方**是 Agent 的 ipykernel 子进程，相对路径 `artifacts/...` 相对当前工作区根。
- **服务方**是 Admin 进程的 workspace_browser，只读、不修改文件，按扩展名派发 MIME。
- **渲染方**是前端 ReactMarkdown，仅当 URL 形如 `/workspace/<segment>/...` 时才会被
  workspace browser 命中并被渲染为 `<img>`；伪造的外部 URL 不会被代理，也不会展示。

---

## 3. Agent 语法契约

精确形式（绑定已配置 `workspace_path` 时）：

```python
# 保存（dpi=150 推荐）
plt.savefig('artifacts/<name>.png', dpi=150)

# 内嵌到回复（<segment> = 配置的 CapabilitySet.workspace_path）
![<alt>](/workspace/<segment>/artifacts/<name>.png)
```

`<segment>` 即 `CapabilitySetRecord.workspace_path`，由配置决定，Agent 无需也无法自行
推断，prompt bullets 在渲染时会填入真实 segment。

当 `CapabilitySet.workspace_path` 为空时，prompt 退化为相对路径 fallback 形式
（`artifacts/<name>.png`），不会出现 `/workspace//` 双斜杠 URL。

---

## 4. 前置条件

- **Agg backend**：`_tools.py::_PRELOADED_DATA_ALIASES` 已强制 matplotlib 使用 Agg
  后端。后端为 Agg 时，`plt.show()` / inline 自动展示对用户不可见、对文本 LLM 也不返回
  图片 repr；因此 Agent 必须显式 `savefig` 才能投递图片。
- **`binding.workspace_path` 已设置**：`_figure_delivery_bullets` 仅在
  `binding.workspace_path` 非空时渲染；无工作区的 Agent 不会出现 figure-delivery bullets，
  也不应尝试投递图片。
- **`artifacts/` 子目录需先创建**：`os.makedirs('artifacts', exist_ok=True)`。

---

## 5. 约束

- **禁止伪造外链**：不得使用 `quickchart.io` 等外部 URL，也不得声称“已展示图片”而实际
  未 `savefig`；若 `savefig` 失败，应如实说明，而非编造图片。
- **默认英文标注**：图表标题 / 坐标轴标签 / 图例默认使用英文；仅当用户明确要求中文时才
  切换中文文本。原因：headless 宿主对 CJK 与 emoji 的字体覆盖不保证，强行使用会触发
  `Glyph <code> missing from font(s) ...` 警告（如 `📈` → Glyph 128202）。
- **只有 workspace 下的文件可被浏览器服务**：workspace_browser 只服务 workspace 根之下
  的文件；写入 workspace 之外、或引用 workspace 之外的路径，都不会被渲染为图片。

---

## 6. 实现位置

| 关注点 | 文件 |
|---|---|
| prompt bullets（含 font 规则） | `apps/yuubot/src/yuubot/core/assembly/_prompt.py` 的 `_figure_delivery_bullets` |
| Agg backend 强制 | `apps/yuubot/src/yuubot/core/assembly/_tools.py` 的 `_PRELOADED_DATA_ALIASES` |
| workspace 文件只读服务 | `apps/yuubot/src/yuubot/runtime/admin/workspace_browser.py`（只读，不修改） |
| markdown 图片渲染 | `apps/yuubot/web/src/components/conversation/markdown-renderer.tsx` |

`system-prompt.md` §1.2 仅保留一行指向本文档的指引，不内联能力细节。

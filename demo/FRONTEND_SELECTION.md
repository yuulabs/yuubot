# Frontend Selection — yuubot Web Admin

> Status: Proposal — evaluating UI direction  
> Date: 2026-05

## 核心需求分析

根据 `design/checklist.md`，Web Admin 前端需要满足：

| 需求 | 来源 | 复杂度 |
|------|------|--------|
| JSON Schema 动态表单渲染 | integration-kinds config_schema | 高 |
| WebSocket 实时聊天 | Web Chat `/ws/chat/{dialog_id}` | 中 |
| 复杂数据表格 + 过滤 | Actors / Ingress Rules / Traces | 中 |
| 大文本编辑器 (System Prompt) | Character prompt 编辑 | 中 |
| 拖拽文件上传 | Web FS | 低 |
| 终端模拟器 | Web PTY | 中 |
| 成本仪表盘可视化 | Cost Dashboard | 低 |
| Session 认证 (Cookie) | admin auth flow | 低 |

## 方案对比

### 方案 A: React 19 + shadcn/ui + Vite (推荐)

| 维度 | 评价 |
|------|------|
| JSON Schema 表单 | ✅ `react-jsonschema-form` / `@rjsf/core` 生态成熟，可从 `GET /api/integration-kinds` 的 schema 直接渲染 |
| 数据表格 | ✅ `@tanstack/react-table` + `@tanstack/react-query` 状态管理极强 |
| 聊天组件 | ✅ 自定义或使用 `chatscope/chat-ui-kit-react` |
| 代码编辑器 | ✅ `@monaco-editor/react` 封装成熟 |
| 终端模拟器 | ✅ `@xterm/xterm` 是事实标准 |
| 拖拽上传 | ✅ `react-dropzone` |
| 图表 | ✅ `recharts` 或 `lightweight-charts` |
| 组件质量 | ✅ shadcn/ui 是当前最高质量的 React 组件库，copy-paste 模式可完全定制 |
| 包体积 | ✅ shadcn/ui 按需引入，Vite tree-shaking，首屏可 < 200KB gzip |
| TypeScript | ✅ 一等公民支持 |
| SSR 需求 | ❌ Admin UI 不需要 SSR，纯 SPA 即可 |
| 学习曲线 | 中等，需要理解 hooks + composition |
| 生态 | ✅ 最大前端生态，admin 后台模板丰富 |

### 方案 B: Vue 3 + Naive UI + Vite

| 维度 | 评价 |
|------|------|
| JSON Schema 表单 | ⚠️ 有 `formkit` 但成熟度不如 RJSF |
| 数据表格 | ✅ Naive UI DataTable 功能完整 |
| 聊天组件 | ⚠️ 需自建，社区选择较少 |
| 代码编辑器 | ✅ Monaco Vue 封装可用 |
| 终端模拟器 | ✅ xterm.js 框架无关 |
| 组件质量 | ✅ Naive UI 是 Vue 3 最佳组件库之一 |
| 学习曲线 | ✅ 略低于 React |
| 定制性 | ⚠️ Naive UI 定制能力不如 shadcn/ui 的 copy-paste 模式 |
| 中文支持 | ✅ Naive UI 原生 i18n |

### 方案 C: Svelte 5 + Skeleton UI + Vite

| 维度 | 评价 |
|------|------|
| JSON Schema 表单 | ❌ 几乎没有现成库 |
| 数据表格 | ⚠️ 需要自建或适配 |
| 包体积 | ✅ 最小 |
| 生态 | ❌ admin 工具类组件极少 |
| 结论 | 不适合，admin 面板需要大量复杂组件 |

## 推荐: 方案 A — React 19 + shadcn/ui + Vite

理由：
1. **JSON Schema 动态表单** 是 Integration 配置的核心交互，这个需求强烈指向 React 生态
2. **shadcn/ui** copy-paste 模式允许完全定制每个组件，适合 yuubot 的暗色主题和独特设计语言
3. **TanStack Query** 对 REST API 的缓存/失效/乐观更新管理是 admin UI 的刚需
4. **xterm.js** 和 **monaco-editor** 都是框架无关但 React 封装最成熟
5. 后端是 Python Starlette，前端与后端语言栈无关，选最适合的工具

## 技术栈详情

```
框架:        React 19 (函数组件 + hooks)
构建工具:    Vite 6
语言:        TypeScript 5.x
UI 组件:     shadcn/ui (copy-paste, Tailwind CSS)
路由:        TanStack Router (类型安全路由)
状态管理:    TanStack Query (服务端缓存) + Zustand (客户端状态)
表单:        react-hook-form + zod (表单验证)
JSON Schema: @rjsf/core (Integration 动态配置表单)
代码编辑器:  @monaco-editor/react (Character system prompt)
终端模拟器:  @xterm/xterm + @xterm/addon-fit (Web PTY)
文件上传:    react-dropzone (Web FS)
图表:        recharts (Cost Dashboard)
WebSocket:   native WebSocket API + 自定义 hook
样式:        Tailwind CSS 4
图标:        Lucide React
包管理:      pnpm
```

## 预期文件夹结构

```
web/
├── README.md
├── package.json
├── pnpm-lock.yaml
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
├── components.json               # shadcn/ui 配置
├── index.html                    # Vite 入口 HTML
│
├── public/
│   └── favicon.svg
│
├── src/
│   ├── main.tsx                  # React 入口
│   ├── App.tsx                   # 根组件 (RouterProvider)
│   ├── routeTree.gen.ts          # TanStack Router 自动生成
│   │
│   ├── routes/                   # 文件系统路由
│   │   ├── __root.tsx             # 全局布局 (sidebar + topbar)
│   │   ├── index.tsx             # Dashboard
│   │   ├── chat.tsx              # Web Chat
│   │   ├── chat.$dialogId.tsx    # Chat 具体 dialog
│   │   ├── actors.tsx            # Actor 列表
│   │   ├── actors.$id.tsx        # Actor 详情/编辑
│   │   ├── characters.tsx        # Character 列表
│   │   ├── characters.$id.tsx    # Character 编辑
│   │   ├── routes.tsx            # Ingress Rules
│   │   ├── providers.tsx          # LLM Backends
│   │   ├── providers.$id.tsx      # LLM Backend 编辑
│   │   ├── integrations.tsx       # Integrations 列表
│   │   ├── integrations.$id.tsx   # Integration 配置
│   │   ├── monitor.tsx            # 监控 (Trace / Cost / PTY)
│   │   └── settings.tsx           # Bootstrap Config / Export-Import
│   │
│   ├── components/               # 通用组件
│   │   ├── ui/                   # shadcn/ui 组件 (自动生成)
│   │   │   ├── button.tsx
│   │   │   ├── card.tsx
│   │   │   ├── dialog.tsx
│   │   │   ├── input.tsx
│   │   │   ├── table.tsx
│   │   │   ├── tabs.tsx
│   │   │   ├── toggle.tsx
│   │   │   ├── select.tsx
│   │   │   ├── tooltip.tsx
│   │   │   └── ...
│   │   ├── layout/
│   │   │   ├── sidebar.tsx
│   │   │   ├── topbar.tsx
│   │   │   └── auth-layout.tsx
│   │   ├── chat/
│   │   │   ├── chat-layout.tsx
│   │   │   ├── dialog-list.tsx
│   │   │   ├── message-list.tsx
│   │   │   ├── message-bubble.tsx
│   │   │   ├── chat-input.tsx
│   │   │   └── typing-indicator.tsx
│   │   ├── schema-form/
│   │   │   ├── schema-form.tsx    # JSON Schema → React 表单
│   │   │   ├── secret-field.tsx    # 密码类字段 (遮蔽/揭示)
│   │   │   └── field-renderer.tsx  # 按 JSON Schema type 渲染
│   │   ├── prompt-editor.tsx      # Monaco 编辑器封装
│   │   ├── terminal.tsx           # xterm.js 封装
│   │   ├── file-manager.tsx       # Web FS 文件管理
│   │   ├── cost-chart.tsx        # 成本仪表盘
│   │   └── status-badge.tsx       # 状态指示器
│   │
│   ├── hooks/                    # 自定义 Hooks
│   │   ├── use-auth.ts           # Session 认证
│   │   ├── use-websocket.ts      # WebSocket 连接管理
│   │   ├── use-dialog.ts         # Chat dialog 操作
│   │   └── use-schema.ts         # JSON Schema 获取与缓存
│   │
│   ├── lib/                      # 工具函数
│   │   ├── api.ts                # fetch 封装 + 拦截器
│   │   ├── auth.ts               # Cookie/Session 工具
│   │   └── utils.ts              # cn() / format 等
│   │
│   └── types/                    # TypeScript 类型
│       ├── api.ts                # API 响应类型 (从 OpenAPI/schema 生成)
│       └── websocket.ts          # WS 消息类型
│
└── e2e/                          # E2E 测试 (Playwright)
    └── ...
```

### 关键设计决策

1. **TanStack Router** (文件系统路由) — 类型安全路由，自动 code splitting，`/chat/:dialogId` 嵌套路由
2. **shadcn/ui** — 不作为 npm 依赖，而是 copy-paste 到 `components/ui/`，允许完全定制
3. **Schema-driven form** — Integration 配置表单从 `GET /api/integration-kinds` 的 `config_schema` 字段动态生成
4. **WebSocket** — 不引入 socket.io，直接用原生 WebSocket + 自定义 hook 管理 reconnect/auth
5. **Monaco Editor** — 用于 Character 的 system prompt 编辑，支持 Prompt Template 插入
6. **xterm.js** — Web PTY 独立组件，连接 Admin 的 PTY WebSocket
7. **静态托管** — 前端 build 产物由 Starlette 直接 host (`/static` 或 `/`)，不单独部署

### API 交互层

```typescript
// lib/api.ts
const BASE = '';  // 同源，Admin UI 由同进程服务

export async function fetchAPI<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'unknown' }));
    throw new ApiError(res.status, err.error ?? 'unknown');
  }
  return res.json();
}

// hooks — TanStack Query 封装
export const useActors = () => useQuery({ queryKey: ['actors'], queryFn: () => fetchAPI<ActorResource[]>('/actors') });
```

## Demo 页面说明

`demo/` 文件夹中包含 8 个独立的 HTML 页面，无需任何构建工具即可在浏览器中直接打开查看：

| 文件 | 页面 | 设计重点 |
|------|------|----------|
| `index.html` | Dashboard | 统计卡片、最近对话、Actor 状态、路由规则概览 |
| `chat.html` | Web Chat | 对话列表 + 消息流 + 打字指示器 + 新建 Dialog 弹窗 |
| `actors.html` | Actors | 列表表格 + 详情编辑面板 + 创建弹窗 |
| `characters.html` | Characters | 卡片列表 + Prompt 编辑器 + Template 插入 |
| `routes.html` | Ingress Rules | 规则表格 + 路由测试器 + 隐式规则说明 |
| `providers.html` | LLM Backends | 卡片式列表 + 创建模板选择 + 定价表格 |
| `integrations.html` | Integrations | 分类卡片 + 动态 Schema 配置 + Capability 表 |
| `monitor.html` | Monitor | 系统状态、Trace 表、Cost 仪表盘、PTY/FS 预览 |
| `settings.html` | Settings | Bootstrap Config (只读)、Export/Import、Danger Zone |

打开 `demo/index.html` 导航即可浏览所有页面。

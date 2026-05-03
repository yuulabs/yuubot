# 07. Trace 与 Observability

Trace 明确有两个端口：采集端口和 UI 端口。

## 端口定义

```yaml
trace:
  enabled: true

  # Trace ingestion endpoint, used by daemon / SDK / OTEL exporters.
  collector_host: 127.0.0.1
  collector_port: 4318

  # Trace Web UI endpoint, used by Admin Monitor iframe/proxy.
  ui_host: 127.0.0.1
  ui_port: 8782
```

- `collector_port`：接收 traces，不是浏览器 UI。
- `ui_port`：提供浏览器 UI。
- Admin `/monitor/` 代理到 `trace.ui_host:trace.ui_port`。

## 推荐访问路径

```text
daemon / sdk / OTEL exporter
  -> http://127.0.0.1:4318

browser
  -> https://yuubot.example.com/monitor/
  -> admin reverse proxy
  -> http://127.0.0.1:8782
```

`trace.ui_port` 可以存在，但不直接对公网暴露。

## Admin Monitor

Admin app 提供：

```text
GET /monitor/{path:path} -> http://{trace.ui_host}:{trace.ui_port}/{path}
```

前端使用：

```html
<iframe src="/monitor/"></iframe>
```

这样避免跨域和 referrer policy 问题。

## 部署原则

- collector port 只对内部网络开放。
- UI port 只对 admin 或反向代理开放。
- 对外只暴露统一 HTTPS 入口。
- 如果 trace service 未启动，Monitor 页面显示明确错误和启动建议。

## 网页阅读

`web.read_page(url, page=0, page_size=5000)` 返回分页内容：
- `text`：本页正文（图片和链接均为绝对 URL）
- `full_size`：文档总字符数
- `page_count` / `page`：总页数 / 当前页（0-indexed）
- `has_more`：是否有后续页

需要后续内容时用 `web.read_page(url, page=N)` 翻页。

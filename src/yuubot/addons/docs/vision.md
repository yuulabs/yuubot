---
name: vision
description: >
  图片视觉识别。
  命令: describe <path>(分析图片内容，返回详细中文描述)。
---

# Vision Addon

使用视觉模型分析图片内容，生成详细的中文描述。

## 可用命令

### 描述图片
`vision describe <path>`

- path: 图片路径，支持 `file:///path` URI 或裸路径 `/path`

返回详细的中文描述，包含：
1. 画面内容：画了什么、谁在做什么
2. 情绪/氛围：表达什么感情（无语、开心、惊讶、悲伤、嘲讽等）
3. 适用场景：什么时候会用到这张图

## 使用示例

```bash
# 描述一张图片（直接用消息中的 file:/// URI）
vision describe file:///home/user/.yuubot/media/abc123.jpg
```
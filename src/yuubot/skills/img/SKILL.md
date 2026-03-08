---
name: img
description: >
  图片/表情包库。
  命令: save(保存图片+描述), search(按描述/标签搜索), delete(删除), list(列表/标签)。
  首次使用前请先 cat 本文件查看完整参数格式。
---

# Image Skill

管理表情包和图片收藏。全局共享，不区分 ctx。

## 可用命令

### 保存图片
`ybot img save <path> --desc "描述" [--tags "猫,搞笑"]`

- path: 图片本地路径（从消息中的 `<image url="file:///path"/>` 获取）
- desc: 图片描述，用于后续搜索
- tags: 逗号分隔的标签，可选

### 搜索图片
`ybot img search "<query>" [--tags "猫"] [--limit 5]`

按描述关键词和/或标签搜索。返回图片路径和描述。

### 删除图片
`ybot img delete <id>`

按 ID 删除图片。

### 列表
`ybot img list [--tags] [--limit 20]`

- `--tags`: 显示所有标签及数量
- 不加 `--tags`: 显示最近图片

## 使用流程

### 收藏表情包
群友发了有趣的图，消息中会有 `<image url="file:///path/to/img.jpg"/>`：
1. 提取路径（去掉 `file://` 前缀）
2. `ybot img save /path/to/img.jpg --desc "猫猫表示无语" --tags "猫,无语"`

### 发送表情包
1. `ybot img search "猫"` 找到合适的图片
2. 用 im send 发送：`echo '[{"type":"image","file":"file:///path/to/img.jpg"}]' | ybot im send --ctx <ctx_id>`

### 描述建议
- 用中文描述图片内容或表达的情绪，准确识别角色名字和情感
- 标签用多个通用分类词：猫, 狗, 搞笑, 无语, 可爱, 表情包

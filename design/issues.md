# Known Issues

## BUG-001: 消息撤回导致同一条消息重复触发 agent

**状态**: In Progress
**严重度**: High
**复现路径**: 给 bot 发一条需要较长处理时间的消息（如 @bot 解释数据库的WAL），在 agent 运行期间撤回该消息。

**现象**:
- 同一条消息（相同 msg_id）触发了两个独立的 agent conversation
- 第二次触发拿到的是原始消息，不携带任何上下文
- 导致 bot 对同一问题重复回答，浪费 token 和 API 额度

**证据**:
- 案例1: msg_id=869178074, conv a813a51d (10:49:35) + conv 1f38369a (10:51:16)
- 案例2: msg_id=1114970250, conv 37654a91 (11:16:41) + conv e1b04068 (11:18:54)
- 两个案例中第二次触发的 USER prompt 与第一次完全相同

**分析**:
- 撤回事件（`post_type=notice, notice_type=group_recall`）本身被 dispatcher 正确忽略（`post_type != "message"` 直接 return）
- 但 NapCat 在撤回时疑似重新投递了原始消息（待确认：当前日志未记录原始 WS 帧的 post_type，无法区分是 NapCat 重发还是 relay 层问题）
- 整条链路（recorder → relay → daemon）没有 msg_id 去重机制

**当前进展**:
1. 已增强 recorder 日志：每条 NapCat 原始 WS payload 都会以 JSON 形式写入 `yuubot.log`，用于区分是 NapCat 重投递还是 relay/daemon 层重复处理。
2. 目前仍未做 `msg_id` 去重；等收集到下一次复现样本后再决定修复点。

1. 配置持久化似乎没有完成（线上改的ychar没落盘）（这里需要讨论！怎么处理配置问题。改原始配置文件可能不好。可能得改一下配置文件约定）
2. 配置页面太粗糙，需支持自行创建Character（用户自设）, 装载Actor，指定context路由
3. UI需要改，太简陋。还是用ts写比较好
4. dockerfile打包逻辑需要变更。提交其他几个包（并触发pypi发布），让yuubot用pyproject来管理依赖，从而让整个repo portable，而不是依赖于当前本地文件目录格局。
5. 安全性问题需要解决否则无法部署上服务器。（HTTPS加密端口。trace界面进入admin面板，内部转发而不是另公开一个端口）
6. 迁移数据库，消除掉代码里面的兼容技术债。
7. admin的文件拖拽上传需要恢复。
8. 当前chat好像是无状态的？需要持久化，支持/new之类的清空。
9. Monitor还是crash. 疑似被拦了：

```
http://localhost:8080/
Referrer Policy
strict-origin-when-cross-origin
```
按理来说转发yuutrace ui就行了。

10. 计费问题。如果会产生计费的条目没有正确配置计费（如llm price未设置），应该直接报错，yllm拒绝执行，提醒配置价格，避免账单刺客。budget必须设置美元quota。

11. 现在yuutrace导出失效了？yuubot好像没有正确使用yuutrace! 要查~/.local/share/yuubot-docker下的traces.db. 2026/5/2 23:00左右有使用，但是没有在ui中看到任何会话。

## Milestones

1. web ui发布
2. skills管理 + skills配置 + opencode集成
3. linear/plane 的python functions集成（从而使得bot可以参与project management）
4. w&b + swanlab + github集成
5. bridge功能回连
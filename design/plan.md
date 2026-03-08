# NapCat 安装引导 + Launch/Shutdown 集成

## 背景

当前状态：
- `scripts/start_napcat.sh` 是空壳
- `ybot launch` 只启动 recorder，不启动 napcat
- `ybot shutdown` 只关 recorder，不关 napcat
- 用户需要手动安装 napcat、手动配置、手动启动

目标用户流程：
1. 首次使用：`ybot setup` → 交互式完成所有配置
2. 日常使用：`ybot launch` 启动 napcat + recorder，挂着不动
3. 运维 bot：`ybot up` / `ybot down` 热插拔 daemon
4. 结束：`ybot shutdown` 关闭 napcat + recorder

## NapCat 关键信息

- 安装器：`curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh && bash napcat.sh`
- 安装位置：`$HOME/Napcat/opt/QQ`
- 启动命令：`xvfb-run -a ~/Napcat/opt/QQ/qq --no-sandbox`
- 后台运行：`screen -dmS napcat bash -c "xvfb-run -a ~/Napcat/opt/QQ/qq --no-sandbox"`
- WebUI 配置：`~/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json`（端口 6099）
- OneBot 配置：`~/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/onebot11_{QQ号}.json`
- 需要配置 reverse WS client 指向 recorder 的 `ws://0.0.0.0:8765`
- 需要配置 HTTP server 在 `:3000` 供 recorder API 代理

## 改动计划

### 1. 新增 `src/yuubot/napcat.py` — NapCat 生命周期管理

一个模块，封装 napcat 的检测、启动、停止：

```python
NAPCAT_QQ = Path.home() / "Napcat" / "opt" / "QQ" / "qq"
NAPCAT_CONFIG_DIR = Path.home() / "Napcat" / "opt" / "QQ" / "resources" / "app" / "app_launcher" / "napcat" / "config"

def is_installed() -> bool
def is_running() -> bool          # 检查 screen session "napcat" 是否存在
def start(qq: int) -> None        # screen -dmS napcat ...
def stop() -> None                # screen -S napcat -X quit
def webui_url() -> str            # 读 webui.json 拿 port
def config_dir() -> Path
```

### 2. 新增 `ybot setup` CLI 命令 — 交互式初始化

流程：
1. 检测 napcat 是否已安装（检查 `~/Napcat/opt/QQ/qq` 是否存在）
2. 未安装 → 提示用户运行安装命令，等待确认
3. 收集 bot QQ 号、master QQ 号
4. 生成 `config.yaml`（从 config.example.yaml 模板，替换 QQ 号）
5. 打印 napcat 配置引导：
   - 告诉用户 onebot11 配置文件路径
   - 告诉用户需要配置的内容（reverse WS 地址、HTTP server 端口）
   - 提供配置示例 JSON
6. 启动 napcat（screen 后台）
7. 打印 WebUI 地址，引导用户扫码登录
8. 等待用户确认登录完成

### 3. 改造 `ybot launch` — 启动 napcat + recorder

```python
@cli.command()
def launch(ctx):
    # 1. 检查 napcat 是否已安装
    # 2. 如果 napcat 没在运行，启动它（screen 后台）
    # 3. 等待 napcat 就绪（轮询 WebUI 端口或短暂 sleep）
    # 4. 启动 recorder（现有逻辑）
```

### 4. 改造 `ybot shutdown` — 关闭 recorder + napcat

```python
@cli.command()
def shutdown(ctx):
    # 1. 关闭 recorder（现有逻辑）
    # 2. 关闭 napcat screen session
```

### 5. 清理 scripts/

- 删除 `scripts/start_napcat.sh`（功能移入 Python）
- `scripts/start_recorder.sh` 保留或删除（功能已在 `ybot launch`）

## 文件变更清单

| 文件 | 操作 |
|---|---|
| `src/yuubot/napcat.py` | 新增 — napcat 生命周期管理 |
| `src/yuubot/cli.py` | 修改 — 新增 setup 命令，改造 launch/shutdown |
| `scripts/start_napcat.sh` | 删除 |
| `scripts/start_recorder.sh` | 删除 |
| `config.example.yaml` | 不变 |

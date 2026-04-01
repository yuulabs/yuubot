#!/usr/bin/env bash
# yuubot 一键安装脚本
# 用法: curl -fsSL https://raw.githubusercontent.com/yuulabs/agent-kits/main/yuubot/install.sh | bash

set -euo pipefail

# ── 颜色 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}▶${NC} $*"; }
ok()      { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✗${NC} $*" >&2; }
section() { echo -e "\n${BOLD}── $* ──────────────────────────────────${NC}"; }
ask()     { echo -ne "${BOLD}?${NC} $* "; }

# ── 常量 ────────────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.local/share/yuubot-kit"
YUUBOT_DIR="$INSTALL_DIR/yuubot"
DATA_DIR="$HOME/.yuubot"
REPO_URL="https://github.com/yuulabs/yuubot"

# ── 平台检测 ─────────────────────────────────────────────────────────────────
detect_platform() {
    OS="$(uname -s)"
    ARCH="$(uname -m)"
    IS_WSL=false

    if [[ "$OS" == "Linux" ]] && grep -qi microsoft /proc/version 2>/dev/null; then
        IS_WSL=true
    fi

    if [[ "$OS" == "Darwin" ]]; then
        warn "macOS 检测到。NapCat 在 macOS 上可能需要手动配置。"
    fi
}

# ── 检查依赖 ─────────────────────────────────────────────────────────────────
check_deps() {
    section "检查依赖"

    local missing=()
    for cmd in curl git; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "缺少必要工具: ${missing[*]}"
        info "Ubuntu/Debian: sudo apt install ${missing[*]}"
        info "CentOS/RHEL:   sudo yum install ${missing[*]}"
        exit 1
    fi

    ok "依赖检查通过"
}

# ── 安装 uv ──────────────────────────────────────────────────────────────────
install_uv() {
    section "安装 uv（Python 包管理器）"

    if command -v uv &>/dev/null; then
        ok "uv 已安装 ($(uv --version))"
        return
    fi

    info "正在安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # 刷新 PATH
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if ! command -v uv &>/dev/null; then
        # 尝试常见安装路径
        for p in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
            [[ -x "$p" ]] && export PATH="$(dirname "$p"):$PATH" && break
        done
    fi

    if ! command -v uv &>/dev/null; then
        error "uv 安装失败，请手动安装: https://docs.astral.sh/uv/"
        exit 1
    fi

    ok "uv 安装完成 ($(uv --version))"
}

# ── 克隆代码 ─────────────────────────────────────────────────────────────────
clone_repo() {
    section "获取 yuubot 代码"

    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "目录已存在，更新到最新版本..."
        git -C "$INSTALL_DIR" pull --ff-only || warn "更新失败，继续使用当前版本"
    else
        info "克隆代码库到 $INSTALL_DIR ..."
        git clone --depth=1 "$REPO_URL" "$INSTALL_DIR"
    fi

    ok "代码获取完成"
}

# ── 安装依赖 ─────────────────────────────────────────────────────────────────
install_deps() {
    section "安装 Python 依赖"
    info "这可能需要 1~3 分钟..."
    (cd "$INSTALL_DIR" && uv sync --quiet)
    ok "依赖安装完成"
}

# ── 交互式配置 ───────────────────────────────────────────────────────────────
collect_config() {
    section "配置向导"
    echo "  回答几个问题，安装脚本会自动生成配置文件。"
    echo "  （直接回车使用括号内的默认值）"
    echo

    # QQ 账号
    while true; do
        ask "Bot 的 QQ 号（专用小号，不要用主号）:"
        read -r BOT_QQ
        [[ "$BOT_QQ" =~ ^[0-9]+$ ]] && break
        error "请输入纯数字 QQ 号"
    done

    while true; do
        ask "管理员 QQ 号（你自己的 QQ）:"
        read -r MASTER_QQ
        [[ "$MASTER_QQ" =~ ^[0-9]+$ ]] && break
        error "请输入纯数字 QQ 号"
    done

    # LLM Provider
    # ADD_AIHUBMIX=true 时额外写入 aihubmix provider 用于视觉能力
    ADD_AIHUBMIX=false
    AIHUBMIX_KEY=""

    echo
    echo -e "  ${BOLD}选择 LLM 服务商：${NC}"
    echo -e "  ${GREEN}1${NC}) DeepSeek + AiHubMix  对话用 DeepSeek，图片理解用 AiHubMix（${BOLD}推荐${NC}）"
    echo -e "  ${GREEN}2${NC}) DeepSeek 仅对话       无图片理解能力，成本最低"
    echo -e "  ${GREEN}3${NC}) AiHubMix              国内支付，一个 Key 搞定对话和图片"
    echo -e "  ${GREEN}4${NC}) OpenRouter             海外聚合，灵活切换所有主流模型"
    echo

    while true; do
        ask "选择 [1-4]（默认 1）:"
        read -r PROVIDER_CHOICE
        PROVIDER_CHOICE="${PROVIDER_CHOICE:-1}"
        [[ "$PROVIDER_CHOICE" =~ ^[1-4]$ ]] && break
        error "请输入 1~4"
    done

    case "$PROVIDER_CHOICE" in
        1)
            PROVIDER="deepseek"
            PROVIDER_LABEL="DeepSeek"
            API_KEY_ENV="DEEPSEEK_API_KEY"
            API_KEY_HINT="在 https://platform.deepseek.com/api_keys 创建"
            DEFAULT_MODEL="deepseek-chat"
            AGENT_LLM_REF="deepseek/deepseek-chat"
            BASE_URL="https://api.deepseek.com/v1"
            ADD_AIHUBMIX=true
            ;;
        2)
            PROVIDER="deepseek"
            PROVIDER_LABEL="DeepSeek"
            API_KEY_ENV="DEEPSEEK_API_KEY"
            API_KEY_HINT="在 https://platform.deepseek.com/api_keys 创建"
            DEFAULT_MODEL="deepseek-chat"
            AGENT_LLM_REF="deepseek/deepseek-chat"
            BASE_URL="https://api.deepseek.com/v1"
            ;;
        3)
            PROVIDER="aihubmix"
            PROVIDER_LABEL="AiHubMix"
            API_KEY_ENV="AIHUBMIX_API_KEY"
            API_KEY_HINT="在 https://aihubmix.com 注册后创建"
            _pick_aihubmix_model
            BASE_URL="https://api.aihubmix.com/v1"
            ;;
        4)
            PROVIDER="openrouter"
            PROVIDER_LABEL="OpenRouter"
            API_KEY_ENV="OPENROUTER_API_KEY"
            API_KEY_HINT="在 https://openrouter.ai/keys 创建"
            DEFAULT_MODEL="deepseek/deepseek-chat"
            AGENT_LLM_REF="openrouter/deepseek/deepseek-chat"
            BASE_URL="https://openrouter.ai/api/v1"
            ;;
    esac

    # API Key
    echo
    info "$API_KEY_HINT"
    while true; do
        ask "$PROVIDER_LABEL API Key:"
        read -rs API_KEY; echo
        [[ -n "$API_KEY" ]] && break
        error "API Key 不能为空"
    done

    # AiHubMix Key（用于视觉能力，当选择 DeepSeek+AiHubMix 时）
    if [[ "$ADD_AIHUBMIX" == true ]]; then
        echo
        echo -e "  ${BOLD}AiHubMix — 图片理解能力${NC}"
        echo "  DeepSeek 不支持多模态，AiHubMix 提供 Gemini 视觉模型用于读图。"
        echo "  注册: https://aihubmix.com（支持支付宝，有免费额度）"
        ask "AiHubMix API Key（直接回车跳过，之后可在 .env 里补充）:"
        read -rs AIHUBMIX_KEY; echo
        AIHUBMIX_KEY="${AIHUBMIX_KEY:-}"
        if [[ -n "$AIHUBMIX_KEY" ]]; then
            ok "已配置图片理解能力"
        else
            warn "跳过 AiHubMix，Bot 将无法理解图片内容"
        fi
    fi

    # Tavily（可选）
    echo
    echo -e "  ${BOLD}Tavily 搜索（可选）${NC}"
    echo "  提供实时网页搜索能力。免费套餐每月 1000 次，https://tavily.com"
    ask "Tavily API Key（直接回车跳过）:"
    read -rs TAVILY_KEY; echo
    TAVILY_KEY="${TAVILY_KEY:-}"
}

_pick_aihubmix_model() {
    echo
    echo -e "  ${BOLD}AiHubMix 模型选择：${NC}"
    echo -e "  ${GREEN}a${NC}) DeepSeek Chat   极低成本，缓存命中率高（推荐）"
    echo -e "  ${GREEN}b${NC}) Claude Sonnet 4.6  最强推理，适合复杂任务"
    echo

    while true; do
        ask "选择 [a/b]（默认 a）:"
        read -r MODEL_CHOICE
        MODEL_CHOICE="${MODEL_CHOICE:-a}"
        [[ "$MODEL_CHOICE" =~ ^[ab]$ ]] && break
        error "请输入 a 或 b"
    done

    case "$MODEL_CHOICE" in
        a) DEFAULT_MODEL="deepseek-chat"; AGENT_LLM_REF="aihubmix/deepseek-chat" ;;
        b) DEFAULT_MODEL="claude-sonnet-4-6"; AGENT_LLM_REF="aihubmix/claude-sonnet-4-6" ;;
    esac
}

# ── 生成配置文件 ──────────────────────────────────────────────────────────────
write_configs() {
    section "生成配置文件"

    mkdir -p "$DATA_DIR"

    # config.yaml
    cat > "$YUUBOT_DIR/config.yaml" <<EOF
# yuubot 配置文件（由安装脚本生成）
bot:
  qq: ${BOT_QQ}
  master: ${MASTER_QQ}
  entries:
    - "/y"
    - "/yuu"

recorder:
  napcat_ws:
    host: "0.0.0.0"
    port: 8765
  relay_ws:
    host: "127.0.0.1"
    port: 8766
  api:
    host: "127.0.0.1"
    port: 8767
  napcat_http: "http://127.0.0.1:3000"
  napcat_webui_port: 6099

daemon:
  recorder_ws: "ws://127.0.0.1:8766"
  recorder_api: "http://127.0.0.1:8767"
  api:
    host: "127.0.0.1"
    port: 8780

database:
  path: "~/.yuubot/yuubot.db"

yuuagents:
  db:
    url: "sqlite+aiosqlite:///~/.yagents/tasks.sqlite3"
  yuutrace:
    db_path: "~/.yagents/traces.db"
    ui_port: 8080
    server_port: 4318
  docker:
    image: "yuuagents-runtime:latest"
  tavily:
    api_key_env: "TAVILY_API_KEY"
  skills:
    paths:
      - "~/.yagents/skills"

memory:
  forget_days: 90
  max_length: 500

response:
  group_default: "at"
  dm_whitelist: []

session:
  ttl: 300
  max_tokens: 60000
  summarize_steps_span: 8
EOF

    # 决定 vision role 的 provider
    local vision_role="gemini-2.0-flash-lite"
    if [[ "$ADD_AIHUBMIX" == true && -n "$AIHUBMIX_KEY" ]]; then
        vision_role="aihubmix/gemini-2.0-flash-lite"
    elif [[ "$PROVIDER" == "aihubmix" ]]; then
        vision_role="aihubmix/gemini-2.0-flash-lite"
    elif [[ "$PROVIDER" == "openrouter" ]]; then
        vision_role="openrouter/google/gemini-2.0-flash-lite-001"
    fi

    # llm.yaml
    cat > "$YUUBOT_DIR/llm.yaml" <<EOF
# yuubot LLM 配置（由安装脚本生成）

families:
  claude:
    vision: true
  gpt:
    vision: true
  gemini:
    vision: true
  deepseek:
    vision: false

provider_priorities:
  aihubmix: 120
  deepseek: 110
  openrouter: 80

llm_roles:
  vision: "${vision_role}"
  selector: "${DEFAULT_MODEL}"
  summarizer: "${DEFAULT_MODEL}"

agent_llm_refs:
  main: "${AGENT_LLM_REF}"
  general: "${AGENT_LLM_REF}"
  researcher: "${AGENT_LLM_REF}"
  coder: "${AGENT_LLM_REF}"
  mem_curator: "${AGENT_LLM_REF}"
  ops: "${AGENT_LLM_REF}"

yuuagents:
  provider_aliases:
    or: openrouter
    ahm: aihubmix
    dpsk: deepseek

  providers:
    ${PROVIDER}:
      api_type: "openai-chat-completion"
      api_key_env: "${API_KEY_ENV}"
      default_model: "${DEFAULT_MODEL}"
      base_url: "${BASE_URL}"
EOF

    # DeepSeek + AiHubMix 组合：追加 aihubmix provider 用于视觉
    if [[ "$ADD_AIHUBMIX" == true && -n "$AIHUBMIX_KEY" ]]; then
        cat >> "$YUUBOT_DIR/llm.yaml" <<'EOF'

    aihubmix:
      api_type: "openai-chat-completion"
      api_key_env: "AIHUBMIX_API_KEY"
      default_model: "gemini-2.0-flash-lite"
      base_url: "https://api.aihubmix.com/v1"
EOF
    fi

    # .env
    {
        echo "# yuubot API Keys（请妥善保管，不要泄露）"
        echo "${API_KEY_ENV}=${API_KEY}"
        if [[ "$ADD_AIHUBMIX" == true && -n "$AIHUBMIX_KEY" ]]; then
            echo "AIHUBMIX_API_KEY=${AIHUBMIX_KEY}"
        fi
        if [[ -n "$TAVILY_KEY" ]]; then
            echo "TAVILY_API_KEY=${TAVILY_KEY}"
        fi
    } > "$YUUBOT_DIR/.env"
    chmod 600 "$YUUBOT_DIR/.env"

    ok "配置文件已写入 $YUUBOT_DIR/"
}

# ── 设置 PATH ─────────────────────────────────────────────────────────────────
setup_path() {
    section "配置 PATH"

    local bin_dir="$INSTALL_DIR/.venv/bin"
    local path_line="export PATH=\"$bin_dir:\$PATH\"  # yuubot"

    # 检查是否已经在 PATH 中
    if echo "$PATH" | grep -q "$bin_dir"; then
        ok "PATH 已包含 yuubot bin 目录"
        return
    fi

    local added=false
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [[ -f "$rc" ]]; then
            if ! grep -q "# yuubot" "$rc"; then
                echo "" >> "$rc"
                echo "$path_line" >> "$rc"
                ok "已添加到 $rc"
                added=true
            fi
        fi
    done

    if [[ "$added" == false ]]; then
        # 创建 .bashrc 如果不存在
        echo "$path_line" >> "$HOME/.bashrc"
        ok "已创建 ~/.bashrc 并添加 PATH"
    fi

    # 当前 shell 立即生效
    export PATH="$bin_dir:$PATH"
}

# ── 验证安装 ──────────────────────────────────────────────────────────────────
verify_install() {
    section "验证安装"

    local ybot_bin="$INSTALL_DIR/.venv/bin/ybot"
    if [[ -x "$ybot_bin" ]]; then
        ok "ybot 命令可用: $ybot_bin"
    else
        warn "ybot 命令未找到，请运行: source ~/.bashrc"
    fi

    ok "配置文件: $YUUBOT_DIR/config.yaml"
    ok "LLM 配置:  $YUUBOT_DIR/llm.yaml"
    ok "API Keys:  $YUUBOT_DIR/.env"
}

# ── 完成提示 ──────────────────────────────────────────────────────────────────
print_done() {
    local bin_dir="$INSTALL_DIR/.venv/bin"

    echo
    echo -e "${GREEN}${BOLD}╔═══════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║         yuubot 安装完成 🎉            ║${NC}"
    echo -e "${GREEN}${BOLD}╚═══════════════════════════════════════╝${NC}"
    echo
    echo -e "  ${BOLD}下一步：${NC}"
    echo
    echo -e "  1. 刷新终端环境："
    echo -e "     ${BLUE}source ~/.bashrc${NC}  (或重新打开终端)"
    echo
    echo -e "  2. 启动 QQ 协议端并扫码登录："
    echo -e "     ${BLUE}ybot launch${NC}"
    echo
    echo -e "  3. 启动 Bot 主程序："
    echo -e "     ${BLUE}ybot up${NC}"
    echo
    echo -e "  4. 在群里发送 ${BLUE}/y on${NC} 启用 Bot"
    echo
    echo -e "  ${BOLD}配置文件位置：${NC}"
    echo -e "  ${YELLOW}$YUUBOT_DIR/${NC}"
    echo
    echo -e "  问题与反馈：https://github.com/yuulabs/yuubot/issues"
    echo
}

# ── 主流程 ────────────────────────────────────────────────────────────────────
main() {
    echo
    echo -e "${BOLD}yuubot 安装向导${NC}"
    echo -e "基于大语言模型的 QQ 群聊 Agent"
    echo

    detect_platform
    check_deps
    install_uv
    clone_repo
    install_deps
    collect_config
    write_configs
    setup_path
    verify_install
    print_done
}

main "$@"

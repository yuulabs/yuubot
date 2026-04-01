"""Interactive first-time setup for yuubot."""

import os
import platform
import subprocess
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import click
import yaml

from yuubot import napcat

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_EXAMPLE = PROJECT_ROOT / "config.example.yaml"

# libsimple release info
_SIMPLE_VERSION = "v0.5.2"
_SIMPLE_ASSETS = {
    ("Linux", "x86_64"): "libsimple-linux-ubuntu-latest.zip",
    ("Linux", "aarch64"): "libsimple-linux-ubuntu-24.04-arm.zip",
    ("Darwin", "x86_64"): "libsimple-osx-x64.zip",
}
_SIMPLE_URL = "https://github.com/wangfenjin/simple/releases/download/{version}/{asset}"


def _parse_port(url: str) -> int:
    """Extract port from a URL like 'http://127.0.0.1:3000'."""
    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    return 80 if parsed.scheme == "http" else 443


def _check_dependency(name: str) -> bool:
    return shutil.which(name) is not None


def _step_check_deps() -> None:
    """Check required system dependencies."""
    missing = []
    for dep in ("screen", "xvfb-run", "curl", "docker"):
        if not _check_dependency(dep):
            missing.append(dep)
    if missing:
        click.echo(f"缺少系统依赖: {', '.join(missing)}")
        click.echo("请安装后重试:")
        click.echo(f"  sudo apt install {' '.join(missing)}")
        raise SystemExit(1)
    click.echo("✓ 系统依赖检查通过")


def _font_packages_for_language(language: str) -> list[str]:
    lang = language.strip().lower()
    if lang in {"skip", "en"}:
        return []
    if lang in {"zh-cn", "zh-tw", "ja", "ko"}:
        return ["fonts-noto-cjk", "fonts-noto-color-emoji"]
    raise ValueError(f"unsupported language: {language!r}")


def _has_apt_get() -> bool:
    return shutil.which("apt-get") is not None


def _sudo_prefix() -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    return ["sudo"]


def _ensure_sudo() -> None:
    if not _sudo_prefix():
        return
    if shutil.which("sudo") is None:
        raise SystemExit("缺少 sudo，无法自动安装系统依赖/字体。")
    subprocess.run(["sudo", "-v"], check=True)


def _apt_install(packages: list[str]) -> None:
    if not packages:
        return
    if not _has_apt_get():
        click.echo("未检测到 apt-get，跳过自动安装字体。")
        click.echo(f"请手动安装: {' '.join(packages)}")
        return
    _ensure_sudo()
    prefix = _sudo_prefix()
    subprocess.run([*prefix, "apt-get", "update"], check=True)
    subprocess.run([*prefix, "apt-get", "install", "-y", *packages], check=True)


def _step_install_playwright() -> None:
    click.echo()
    click.echo("=" * 60)
    click.echo("  Playwright 安装")
    click.echo("=" * 60)
    click.echo()
    if not click.confirm(
        "是否安装 Playwright Chromium 浏览器与系统依赖?", default=True
    ):
        click.echo("跳过 Playwright 安装。")
        return

    _ensure_sudo()
    subprocess.run(
        [sys.executable, "-m", "playwright", "install-deps", "chromium"], check=True
    )
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"], check=True
    )
    click.echo("✓ Playwright Chromium 已就绪")


def _step_install_fonts() -> None:
    click.echo()
    click.echo("=" * 60)
    click.echo("  字体安装")
    click.echo("=" * 60)
    click.echo()
    language = click.prompt(
        "请选择界面语言/字体",
        type=click.Choice(
            ["zh-cn", "zh-tw", "ja", "ko", "en", "skip"], case_sensitive=False
        ),
        default="zh-cn",
        show_choices=True,
    )
    packages = _font_packages_for_language(language)
    if not packages:
        click.echo("跳过字体安装。")
        return
    click.echo(f"将安装字体包: {' '.join(packages)}")
    _apt_install(packages)
    click.echo("✓ 字体已安装")


def _step_install_napcat() -> None:
    """Guide user through NapCat installation."""
    if napcat.is_installed():
        click.echo(f"✓ NapCat 已安装 ({napcat.NAPCAT_QQ_BIN})")
        return

    click.echo("NapCat 未安装。请在另一个终端中运行以下命令安装:")
    click.echo()
    click.echo(f"  {napcat.INSTALLER_CMD}")
    click.echo()
    click.echo("安装时建议选择:")
    click.echo("  - Shell 模式 (非 Docker)")
    click.echo("  - 安装 CLI 工具 (可选)")
    click.echo()
    click.confirm("安装完成后按回车继续", default=True, abort=True)

    if not napcat.is_installed():
        click.echo(f"未检测到 NapCat ({napcat.NAPCAT_QQ_BIN})，请确认安装成功。")
        raise SystemExit(1)
    click.echo("✓ NapCat 安装确认")


def _step_collect_info() -> tuple[int, int]:
    """Collect bot QQ and master QQ."""
    qq = click.prompt("Bot QQ 号", type=int)
    master = click.prompt("Master QQ 号 (管理员)", type=int)
    return qq, master


def _step_generate_config(qq: int, master: int, config_path: str | None) -> Path:
    """Generate config.yaml from template."""
    target = Path(config_path) if config_path else Path("config.yaml")

    if target.exists():
        if not click.confirm(f"{target} 已存在，是否覆盖?", default=False):
            click.echo("跳过配置生成。")
            return target

    if not CONFIG_EXAMPLE.exists():
        click.echo(f"找不到配置模板: {CONFIG_EXAMPLE}")
        raise SystemExit(1)

    raw = yaml.safe_load(CONFIG_EXAMPLE.read_text())
    raw["bot"]["qq"] = qq
    raw["bot"]["master"] = master
    target.write_text(
        yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False)
    )
    click.echo(f"✓ 配置已生成: {target}")
    return target


def _step_write_napcat_config(qq: int, ws_port: int, http_port: int) -> None:
    """Write NapCat OneBot11 config to connect to Recorder."""
    click.echo()
    click.echo("=" * 60)
    click.echo("  NapCat OneBot11 配置")
    click.echo("=" * 60)
    click.echo()

    path = napcat.write_onebot_config(qq, ws_port, http_port)

    click.echo(f"✓ 已写入 NapCat 配置: {path}")
    click.echo(f"  反向 WS → ws://127.0.0.1:{ws_port}")
    click.echo(f"  HTTP API → 0.0.0.0:{http_port}")
    click.echo()


def _step_start_and_login() -> None:
    """Start NapCat and guide QR login."""
    if not napcat.is_running():
        click.echo("启动 NapCat...")
        napcat.start()
        if not napcat.wait_ready():
            click.echo("NapCat 启动失败，请检查: screen -r napcat")
            raise SystemExit(1)
        click.echo("✓ NapCat 已启动")
    else:
        click.echo("✓ NapCat 已在运行中")

    url = napcat.webui_url()
    token = napcat.webui_token()
    click.echo()
    click.echo(f"请在浏览器中打开 NapCat WebUI: {url}")
    if token:
        click.echo(f"登录 Token: {token}")
    else:
        click.echo("⚠ 未能读取 WebUI Token，请查看 screen -r napcat 日志获取。")
    click.echo("在 WebUI 中输入 Token 后扫码登录 QQ。")
    click.echo()
    click.confirm("登录完成后按回车继续", default=True, abort=True)
    click.echo("✓ 登录完成")


def _step_install_libsimple() -> None:
    """Download libsimple SQLite extension for Chinese FTS5 support."""
    click.echo()
    click.echo("=" * 60)
    click.echo("  libsimple 中文全文搜索扩展")
    click.echo("=" * 60)
    click.echo()

    vendor_dir = PROJECT_ROOT / "vendor"
    # Check if already installed
    existing = list(vendor_dir.glob("*/libsimple.so")) + list(
        vendor_dir.glob("*/libsimple.dylib")
    )
    if existing:
        click.echo(f"✓ libsimple 已安装: {existing[0].parent}")
        return

    system = platform.system()
    machine = platform.machine()
    asset = _SIMPLE_ASSETS.get((system, machine))
    if not asset:
        click.echo(f"⚠ 当前平台 ({system}/{machine}) 无预编译 libsimple，跳过。")
        click.echo("  记忆搜索将使用默认 FTS5 tokenizer（中文支持有限）。")
        click.echo(
            f"  可手动从 https://github.com/wangfenjin/simple/releases 下载并放入 {vendor_dir}/"
        )
        return

    url = _SIMPLE_URL.format(version=_SIMPLE_VERSION, asset=asset)
    click.echo(f"下载 {asset}...")

    zip_path = vendor_dir / "libsimple.zip"
    vendor_dir.mkdir(parents=True, exist_ok=True)

    try:
        import urllib.request

        urllib.request.urlretrieve(url, zip_path)
    except Exception as e:
        click.echo(f"⚠ 下载失败: {e}")
        click.echo(f"  请手动下载: {url}")
        click.echo(f"  解压到: {vendor_dir}/")
        return

    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(vendor_dir)
        zip_path.unlink()
        click.echo("✓ libsimple 已安装")
    except Exception as e:
        click.echo(f"⚠ 解压失败: {e}")
        zip_path.unlink(missing_ok=True)


def run_setup(config_path: str | None = None) -> None:
    """Run the full interactive setup."""
    click.echo()
    click.echo("=" * 60)
    click.echo("  yuubot 初始化向导")
    click.echo("=" * 60)
    click.echo()

    _step_check_deps()

    _step_install_playwright()
    _step_install_fonts()
    _step_install_libsimple()

    _step_install_napcat()

    qq, master = _step_collect_info()

    cfg_path = _step_generate_config(qq, master, config_path)

    # Load config to get the correct ports
    from yuubot.config import load_config, write_yagents_config

    cfg = load_config(str(cfg_path))
    yagents_path = write_yagents_config(cfg)
    click.echo(f"✓ 已生成 yuuagents 运行配置: {yagents_path}")
    click.echo("  该文件由 yuubot 自动维护，不需要手动编辑。")
    _step_write_napcat_config(
        qq,
        ws_port=cfg.recorder.napcat_ws.port,
        http_port=_parse_port(cfg.recorder.napcat_http),
    )
    napcat.write_webui_port(cfg.recorder.napcat_webui_port)

    _step_start_and_login()

    click.echo()
    click.echo("=" * 60)
    click.echo("  ✓ 初始化完成!")
    click.echo("=" * 60)
    click.echo()
    click.echo("日常使用:")
    click.echo("  ybot launch     # 启动 NapCat + Recorder (保持运行)")
    click.echo("  ybot up          # 启动 Bot Daemon")
    click.echo("  ybot down        # 停止 Bot Daemon")
    click.echo("  ybot shutdown    # 关闭 NapCat + Recorder")
    click.echo()

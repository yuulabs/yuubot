"""NapCat lifecycle management — detect, start, stop."""

import os
import json
import re
import subprocess
import time
from pathlib import Path

from loguru import logger

NAPCAT_HOME = Path.home() / "Napcat"
NAPCAT_QQ_BIN = NAPCAT_HOME / "opt" / "QQ" / "qq"
NAPCAT_CONFIG_DIR = (
    NAPCAT_HOME / "opt" / "QQ" / "resources" / "app" / "app_launcher" / "napcat" / "config"
)
SCREEN_SESSION = "napcat"
NAPCAT_BOOT_LOG = Path("/tmp/napcat_boot.log")

INSTALLER_CMD = (
    "curl -o napcat.sh "
    "https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh "
    "&& bash napcat.sh"
)

_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_LOCAL_NO_PROXY = ("localhost", "127.0.0.1", "::1")


def is_installed() -> bool:
    return NAPCAT_QQ_BIN.exists()


def is_running() -> bool:
    """Check if the napcat screen session exists."""
    r = subprocess.run(
        ["screen", "-ls"],
        capture_output=True, text=True,
    )
    return SCREEN_SESSION in r.stdout


def _build_launch_env(*, qq_direct: bool) -> dict[str, str]:
    env = os.environ.copy()
    if not qq_direct:
        return env

    for key in _PROXY_ENV_VARS:
        env.pop(key, None)

    existing = [part.strip() for part in env.get("NO_PROXY", "").split(",") if part.strip()]
    for host in _LOCAL_NO_PROXY:
        if host not in existing:
            existing.append(host)
    env["NO_PROXY"] = ",".join(existing)
    env["no_proxy"] = env["NO_PROXY"]
    return env


def start(*, qq_direct: bool = False) -> None:
    """Start napcat in a detached screen session with boot log capture."""
    if is_running():
        logger.info("NapCat already running in screen session '{}'", SCREEN_SESSION)
        return
    if not is_installed():
        raise RuntimeError("NapCat is not installed. Run `ybot setup` first.")
    NAPCAT_BOOT_LOG.unlink(missing_ok=True)
    inner = f"xvfb-run -a {NAPCAT_QQ_BIN} --no-sandbox"
    # Wrap with script -qf to capture output in real-time for QR code detection
    cmd = f"script -qfc '{inner}' {NAPCAT_BOOT_LOG}"
    subprocess.run(
        ["screen", "-dmS", SCREEN_SESSION, "bash", "-c", cmd],
        check=True,
        env=_build_launch_env(qq_direct=qq_direct),
    )
    logger.info("NapCat started in screen session '{}'", SCREEN_SESSION)


def stop() -> None:
    """Stop the napcat screen session."""
    if not is_running():
        logger.info("NapCat is not running.")
        return
    subprocess.run(
        ["screen", "-S", SCREEN_SESSION, "-X", "quit"],
        check=True,
    )
    logger.info("NapCat stopped.")


def wait_ready(timeout: int = 30) -> bool:
    """Wait for napcat to be ready by checking the screen session stays alive."""
    deadline = time.monotonic() + timeout
    # Give it a moment to boot
    time.sleep(3)
    while time.monotonic() < deadline:
        if is_running():
            return True
        time.sleep(1)
    return False


def config_dir() -> Path:
    return NAPCAT_CONFIG_DIR


def webui_json_path() -> Path:
    return NAPCAT_CONFIG_DIR / "webui.json"


def _read_webui_json() -> dict:
    p = webui_json_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def webui_url() -> str:
    """Read webui.json and return the URL."""
    data = _read_webui_json()
    host = data.get("host", "0.0.0.0")
    port = data.get("port", 6099)
    if host == "0.0.0.0":
        host = "localhost"
    return f"http://{host}:{port}"


def write_webui_port(port: int) -> None:
    """Update the port in webui.json (create minimal config if missing)."""
    p = webui_json_path()
    data = _read_webui_json()
    if data.get("port") == port:
        return
    data["port"] = port
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    logger.info("Updated NapCat WebUI port to {} in {}", port, p)


def webui_token() -> str | None:
    """Read webui.json and return the login token, or None if unavailable."""
    return _read_webui_json().get("token") or None


def capture_qrcode(timeout: int = 30) -> str | None:
    """Poll NapCat boot log for QR code output.

    Returns the QR code block (with URL) if found, None if login
    succeeded without QR or timeout reached.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if NAPCAT_BOOT_LOG.exists():
            try:
                content = _strip_ansi(NAPCAT_BOOT_LOG.read_text(errors="replace"))
            except OSError:
                time.sleep(1)
                continue
            if "二维码解码URL" in content:
                return _extract_qr_block(content)
            # Quick-login or already logged in — no QR needed
            if "登录成功" in content or "Bot已登录" in content:
                return None
        if not is_running():
            return None
        time.sleep(1)
    return None


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and carriage returns."""
    return re.sub(r'\x1b[\[\(][0-9;]*[a-zA-Z]|\r', '', text)


def _extract_qr_block(content: str) -> str:
    """Extract QR code block and decode URL from NapCat log output."""
    lines = content.splitlines()
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        if "请扫描下面的二维码" in line and start_idx is None:
            start_idx = i
        if start_idx is not None and "如果控制台二维码无法扫码" in line:
            end_idx = i + 1
            break

    if start_idx is None:
        return ""
    if end_idx is None:
        # Fallback: capture through URL line
        for i in range(start_idx, len(lines)):
            if "二维码解码URL" in lines[i]:
                end_idx = i + 1
                break
    if end_idx is None:
        end_idx = len(lines)

    result = []
    for line in lines[start_idx:end_idx]:
        # Strip NapCat timestamp prefix: "MM-DD HH:MM:SS [level] "
        cleaned = re.sub(r'^\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[\w+\]\s*', '', line)
        result.append(cleaned)
    return "\n".join(result)


def onebot_config_path(qq: int) -> Path:
    return NAPCAT_CONFIG_DIR / f"onebot11_{qq}.json"


def write_onebot_config(qq: int, ws_port: int, http_port: int) -> Path:
    """Write NapCat OneBot11 config so it connects to Recorder correctly.

    Returns the path written to.
    """
    cfg = {
        "network": {
            "httpServers": [
                {
                    "name": "httpServer",
                    "enable": True,
                    "port": http_port,
                    "host": "0.0.0.0",
                    "enableCors": True,
                    "enableWebsocket": False,
                    "messagePostFormat": "array",
                    "token": "",
                    "debug": False,
                }
            ],
            "httpClients": [],
            "websocketServers": [],
            "websocketClients": [
                {
                    "name": "yuubotWS",
                    "enable": True,
                    "url": f"ws://127.0.0.1:{ws_port}",
                    "messagePostFormat": "array",
                    "reportSelfMessage": False,
                    "reconnectInterval": 5000,
                    "token": "",
                    "debug": False,
                    "heartInterval": 30000,
                }
            ],
        },
        "musicSignUrl": "",
        "enableLocalFile2Url": True,
        "parseMultMsg": False,
    }
    path = onebot_config_path(qq)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    logger.info("Wrote NapCat OneBot11 config: {}", path)
    return path

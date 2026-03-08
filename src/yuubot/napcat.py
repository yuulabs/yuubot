"""NapCat lifecycle management — detect, start, stop."""

import json
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

NAPCAT_HOME = Path.home() / "Napcat"
NAPCAT_QQ_BIN = NAPCAT_HOME / "opt" / "QQ" / "qq"
NAPCAT_CONFIG_DIR = (
    NAPCAT_HOME / "opt" / "QQ" / "resources" / "app" / "app_launcher" / "napcat" / "config"
)
SCREEN_SESSION = "napcat"

INSTALLER_CMD = (
    "curl -o napcat.sh "
    "https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh "
    "&& bash napcat.sh"
)


def is_installed() -> bool:
    return NAPCAT_QQ_BIN.exists()


def is_running() -> bool:
    """Check if the napcat screen session exists."""
    r = subprocess.run(
        ["screen", "-ls"],
        capture_output=True, text=True,
    )
    return SCREEN_SESSION in r.stdout


def start() -> None:
    """Start napcat in a detached screen session."""
    if is_running():
        log.info("NapCat already running in screen session '%s'", SCREEN_SESSION)
        return
    if not is_installed():
        raise RuntimeError("NapCat is not installed. Run `ybot setup` first.")
    cmd = f"xvfb-run -a {NAPCAT_QQ_BIN} --no-sandbox"
    subprocess.run(
        ["screen", "-dmS", SCREEN_SESSION, "bash", "-c", cmd],
        check=True,
    )
    log.info("NapCat started in screen session '%s'", SCREEN_SESSION)


def stop() -> None:
    """Stop the napcat screen session."""
    if not is_running():
        log.info("NapCat is not running.")
        return
    subprocess.run(
        ["screen", "-S", SCREEN_SESSION, "-X", "quit"],
        check=True,
    )
    log.info("NapCat stopped.")


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
    log.info("Updated NapCat WebUI port to %d in %s", port, p)


def webui_token() -> str | None:
    """Read webui.json and return the login token, or None if unavailable."""
    return _read_webui_json().get("token") or None


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
    log.info("Wrote NapCat OneBot11 config: %s", path)
    return path

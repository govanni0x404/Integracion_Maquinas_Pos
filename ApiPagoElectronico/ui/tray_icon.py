"""
Icono en la bandeja del sistema (pystray + PIL).
En Windows es la interfaz principal del servicio; en Linux solo se usa en desarrollo.
"""
import os
import sys
import logging
import threading
import platform
import subprocess
import webbrowser
from pathlib import Path
from config.settings import APP_NAME, HTTP_PORT
from core.logging_config import logger, current_log_file, LOG_DIR


def _find_dotenv() -> str | None:
    candidates: list[Path] = []
    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / ".env")
        candidates.append(exe_dir.parent.parent / ".env")
    except Exception:
        pass

    try:
        candidates.append(Path(sys.argv[0]).resolve().parent / ".env")
    except Exception:
        pass

    try:
        candidates.append(Path(os.getcwd()).resolve() / ".env")
    except Exception:
        pass

    for p in candidates:
        if p.exists():
            return str(p)
    return None

IS_WINDOWS = platform.system() == "Windows"

try:
    import pystray
    from PIL import Image, ImageDraw
    SYSTRAY_AVAILABLE = True
    logger.info("pystray disponible")
except Exception as e:
    SYSTRAY_AVAILABLE = False
    logger.warning("pystray no disponible: %s", e)


def _open_path(path: str):
    """Abre un archivo o carpeta con la aplicación por defecto del sistema."""
    if IS_WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


def _create_pystray_image():
    """Crea imagen 64×64 para el ícono de bandeja."""
    from PIL import Image, ImageDraw, ImageFont
    size = (64, 64)
    img = Image.new("RGBA", size, (0, 100, 200, 255))
    dc = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font = None
    text = "TM"
    if font:
        dc.text((8, 16), text, font=font, fill=(255, 255, 255, 255))
    else:
        dc.text((10, 18), text, fill=(255, 255, 255, 255))
    return img


def _run_pystray(pos_module):
    """Ejecuta pystray. Bloqueante en el hilo llamante."""

    def on_panel(icon, item):
        webbrowser.open(f"http://localhost:{HTTP_PORT}/panel")

    def on_status(icon, item):
        webbrowser.open(f"http://localhost:{HTTP_PORT}/status")

    def on_log(icon, item):
        _open_path(current_log_file())

    def on_logs_folder(icon, item):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _open_path(str(LOG_DIR))

    def on_env(icon, item):
        env_path = _find_dotenv()
        if env_path:
            _open_path(env_path)

    def on_env_folder(icon, item):
        env_path = _find_dotenv()
        if env_path:
            _open_path(str(Path(env_path).resolve().parent))

    def on_quit(icon, item):
        logger.info("Salir solicitado desde el menú (pystray)")
        try:
            pos_module.stop_monitor()
        except Exception:
            pass
        from core.singleton import cleanup_lock
        cleanup_lock()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(f"● {APP_NAME}", lambda: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Abrir panel de control", on_panel),
        pystray.MenuItem("Ver estado (JSON)", on_status),
        pystray.MenuItem("Ver log", on_log),
        pystray.MenuItem("Abrir carpeta de logs", on_logs_folder),
        pystray.MenuItem("Abrir .env", on_env),
        pystray.MenuItem("Ver carpeta .env", on_env_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Verificar estado", on_status),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir", on_quit),
    )
    image = _create_pystray_image()
    icon = pystray.Icon(APP_NAME, image, APP_NAME, menu=menu)
    logger.info("Iniciando pystray icon...")
    icon.run()  # bloqueante


class TrayIcon:
    """Ícono de bandeja del sistema."""

    def __init__(self, pos_module):
        self.pos_module = pos_module

    def run_blocking(self):
        """Ejecuta el ícono en el hilo ACTUAL (bloqueante). Llamar desde el main thread."""
        if SYSTRAY_AVAILABLE:
            _run_pystray(self.pos_module)
        else:
            logger.warning("Sin backend de GUI disponible — fallback a espera indefinida")
            threading.Event().wait()  # Mantener vivo el main thread

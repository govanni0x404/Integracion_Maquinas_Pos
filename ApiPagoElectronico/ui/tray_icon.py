"""
Módulo para mostrar un icono en la bandeja/barra del sistema.
- macOS:   usa `rumps` (nativo AppKit, sin conflictos con PyInstaller)
- Windows: usa `pystray` + PIL
- Linux:   usa `pystray` + PIL
"""
import os
import sys
import logging
import threading
import platform
import subprocess
from pathlib import Path
from config.settings import APP_NAME, HTTP_PORT
from core.logging_config import logger, LOG_FILE


def _find_asset(filename: str) -> str | None:
    """
    Busca un archivo de asset en múltiples ubicaciones:
    1. assets/ relativo al script principal (desarrollo)
    2. assets/ relativo al ejecutable PyInstaller
    3. _MEIPASS (recursos empaquetados por PyInstaller)
    """
    candidates = []

    # Junto al script principal (app.py)
    try:
        script_assets = Path(sys.argv[0]).resolve().parent / "assets" / filename
        candidates.append(script_assets)
    except Exception:
        pass

    # Junto al ejecutable (PyInstaller onedir)
    try:
        exe_assets = Path(sys.executable).parent / "assets" / filename
        candidates.append(exe_assets)
        # Si está dentro de .app, subir afuera
        exe_dir = Path(sys.executable).parent
        if "Contents" in exe_dir.parts and "MacOS" in exe_dir.parts:
            outer = exe_dir.parent.parent.parent
            candidates.append(outer / "assets" / filename)
    except Exception:
        pass

    # PyInstaller _MEIPASS
    try:
        meipass = Path(getattr(sys, "_MEIPASS", "")) / "assets" / filename
        candidates.append(meipass)
    except Exception:
        pass

    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _find_dotenv() -> str | None:
    candidates: list[Path] = []
    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / ".env")
        if "Contents" in exe_dir.parts and "MacOS" in exe_dir.parts:
            candidates.append(exe_dir.parent.parent.parent / ".env")
        else:
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

IS_MACOS   = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"

# ── Backend macOS: rumps ──────────────────────────────────────
RUMPS_AVAILABLE = False
if IS_MACOS:
    try:
        import rumps
        RUMPS_AVAILABLE = True
        logger.info("rumps disponible — usando backend nativo macOS")
    except Exception as e:
        logger.warning("rumps no disponible: %s", e)

# ── Backend Windows/Linux: pystray ───────────────────────────
PYSTRAY_AVAILABLE = False
if not RUMPS_AVAILABLE:
    try:
        import pystray
        from PIL import Image, ImageDraw
        PYSTRAY_AVAILABLE = True
        logger.info("pystray disponible")
    except Exception as e:
        logger.warning("pystray no disponible: %s", e)

# Variable de compatibilidad para app.py
SYSTRAY_AVAILABLE = RUMPS_AVAILABLE or PYSTRAY_AVAILABLE


# ════════════════════════════════════════════════════════════════
#  Backend macOS — rumps
# ════════════════════════════════════════════════════════════════
if RUMPS_AVAILABLE:
    class _RumpsApp(rumps.App):
        """Aplicación rumps para barra de menú macOS."""

        def __init__(self, pos_module):
            # Intentar cargar ícono PNG para la barra de menú
            icon_path = _find_asset("icon_22.png") or _find_asset("icon_32.png")
            if icon_path:
                logger.info("Ícono de barra de menú: %s", icon_path)
            else:
                logger.warning("No se encontró ícono PNG, usando texto")

            super().__init__(
                name=APP_NAME,
                title=None if icon_path else "💳 POS",
                icon=icon_path,
                quit_button=None,
            )
            self.pos_module = pos_module
            self._build_menu()

        def _build_menu(self):
            # IMPORTANTE: usar SOLO callbacks en MenuItem, sin @rumps.clicked decoradores
            self.menu = [
                rumps.MenuItem(f"● {APP_NAME}", callback=None),
                rumps.separator,
                rumps.MenuItem("🖥  Abrir panel de control", callback=self._cb_abrir_panel),
                rumps.MenuItem("📊  Ver estado (JSON)",     callback=self._cb_abrir_estado),
                rumps.MenuItem("📋  Ver log",               callback=self._cb_ver_log),
                rumps.MenuItem("🧾  Abrir .env",             callback=self._cb_abrir_env),
                rumps.MenuItem("📁  Ver carpeta .env",       callback=self._cb_abrir_carpeta_env),
                rumps.separator,
                rumps.MenuItem("🔄  Verificar estado",      callback=self._cb_estado),
                rumps.separator,
                rumps.MenuItem("⏹  Salir",                  callback=self._cb_salir),
            ]

        def _cb_abrir_panel(self, sender):
            """Abre el panel de control en el navegador."""
            subprocess.Popen(["open", f"http://localhost:{HTTP_PORT}/panel"])

        def _cb_abrir_estado(self, sender):
            """Abre el estado JSON en el navegador."""
            subprocess.Popen(["open", f"http://localhost:{HTTP_PORT}/status"])

        def _cb_ver_log(self, sender):
            """Abre el log en Console.app o el editor de texto por defecto."""
            try:
                subprocess.Popen(["open", "-a", "Console", LOG_FILE])
            except Exception:
                try:
                    subprocess.Popen(["open", LOG_FILE])
                except Exception as e:
                    logger.error("No se pudo abrir el log: %s", e)

        def _cb_abrir_env(self, sender):
            env_path = _find_dotenv()
            if not env_path:
                rumps.notification(
                    title=APP_NAME,
                    subtitle="Configuración",
                    message="No se encontró .env",
                )
                return
            try:
                subprocess.Popen(["open", env_path])
            except Exception as e:
                rumps.notification(
                    title=APP_NAME,
                    subtitle="Error abriendo .env",
                    message=str(e),
                )

        def _cb_abrir_carpeta_env(self, sender):
            env_path = _find_dotenv()
            if not env_path:
                rumps.notification(
                    title=APP_NAME,
                    subtitle="Configuración",
                    message="No se encontró .env",
                )
                return
            try:
                subprocess.Popen(["open", str(Path(env_path).resolve().parent)])
            except Exception as e:
                rumps.notification(
                    title=APP_NAME,
                    subtitle="Error abriendo carpeta",
                    message=str(e),
                )

        def _cb_estado(self, sender):
            """Muestra el estado actual del servicio como notificación."""
            try:
                import urllib.request
                resp = urllib.request.urlopen(
                    f"http://localhost:{HTTP_PORT}/status", timeout=2
                ).read().decode()
                rumps.notification(
                    title=APP_NAME,
                    subtitle="Estado del servicio",
                    message=resp,
                )
            except Exception as e:
                rumps.notification(
                    title=APP_NAME,
                    subtitle="Error",
                    message=str(e),
                )

        def _cb_salir(self, sender):
            """Detiene el servicio y cierra la aplicación."""
            logger.info("Salir solicitado desde el menú")
            try:
                self.pos_module.stop_monitor()
            except Exception:
                pass
            from core.singleton import cleanup_lock
            cleanup_lock()
            rumps.quit_application()


# ════════════════════════════════════════════════════════════════
#  Backend Windows / Linux — pystray
# ════════════════════════════════════════════════════════════════
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
    """Ejecuta pystray (Windows/Linux). Bloqueante en el hilo llamante."""
    import pystray

    def on_panel(icon, item):
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{HTTP_PORT}/panel")
        except Exception:
            if IS_WINDOWS:
                os.startfile(f"http://localhost:{HTTP_PORT}/panel")
            else:
                subprocess.Popen(["xdg-open", f"http://localhost:{HTTP_PORT}/panel"])

    def on_status(icon, item):
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{HTTP_PORT}/status")
        except Exception:
            if IS_WINDOWS:
                os.startfile(f"http://localhost:{HTTP_PORT}/status")
            else:
                subprocess.Popen(["xdg-open", f"http://localhost:{HTTP_PORT}/status"])

    def on_log(icon, item):
        if IS_WINDOWS:
            os.startfile(LOG_FILE)
        else:
            subprocess.Popen(["xdg-open", LOG_FILE])

    def on_env(icon, item):
        env_path = _find_dotenv()
        if not env_path:
            return
        if IS_WINDOWS:
            os.startfile(env_path)
        else:
            subprocess.Popen(["xdg-open", env_path])

    def on_env_folder(icon, item):
        env_path = _find_dotenv()
        if not env_path:
            return
        folder = str(Path(env_path).resolve().parent)
        if IS_WINDOWS:
            os.startfile(folder)
        else:
            subprocess.Popen(["xdg-open", folder])

    def on_check(icon, item):
        on_status(icon, item)

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
        pystray.MenuItem("Abrir .env", on_env),
        pystray.MenuItem("Ver carpeta .env", on_env_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Verificar estado", on_check),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir", on_quit),
    )
    image = _create_pystray_image()
    icon = pystray.Icon(APP_NAME, image, APP_NAME, menu=menu)
    logger.info("Iniciando pystray icon (Windows/Linux)...")
    icon.run()  # bloqueante en main thread


# ════════════════════════════════════════════════════════════════
#  Clase pública TrayIcon
# ════════════════════════════════════════════════════════════════
class TrayIcon:
    """
    Interfaz unificada para el ícono de bandeja/barra del sistema.
    Selecciona automáticamente el backend correcto según el SO.
    """

    def __init__(self, pos_module):
        self.pos_module = pos_module
        self._thread = None
        self.icon = None
        self._rumps_app = None

    def run_blocking(self):
        """
        Ejecuta el ícono en el hilo ACTUAL (bloqueante).
        Debe llamarse desde el MAIN THREAD.
        En macOS usa rumps; en otros SO usa pystray.
        """
        if IS_MACOS and RUMPS_AVAILABLE:
            self._run_rumps_blocking()
        elif PYSTRAY_AVAILABLE:
            _run_pystray(self.pos_module)
        else:
            logger.warning("Sin backend de GUI disponible — fallback a espera indefinida")
            threading.Event().wait()  # Mantener vivo el main thread

    def _run_rumps_blocking(self):
        """Lanza la app rumps en el hilo actual (main thread requerido)."""
        logger.info("Lanzando rumps app en main thread...")
        self._rumps_app = _RumpsApp(self.pos_module)
        self._rumps_app.run()   # Bloqueante — start NSRunLoop
        logger.info("rumps app terminó")

    def run(self):
        """
        Inicia el ícono en un hilo daemon (NO bloqueante).
        Usar solo cuando no es posible llamar al main thread (ej. CLI sin GUI).
        """
        if IS_MACOS and RUMPS_AVAILABLE:
            logger.warning(
                "run() no bloqueante en macOS → el ícono puede no aparecer. "
                "Prefiere run_blocking() desde el main thread."
            )
        self._thread = threading.Thread(
            target=self.run_blocking, daemon=True, name="TrayThread"
        )
        self._thread.start()

    def stop(self):
        """Detiene el ícono si está activo."""
        try:
            if IS_MACOS and self._rumps_app and RUMPS_AVAILABLE:
                rumps.quit_application()
            elif self.icon:
                self.icon.stop()
        except Exception as e:
            logger.debug("Error deteniendo tray: %s", e)

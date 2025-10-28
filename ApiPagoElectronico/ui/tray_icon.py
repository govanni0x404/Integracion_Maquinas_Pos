# ui/tray_icon.py
import os
import logging
import threading
import platform

from config.settings import APP_NAME

logger = logging.getLogger(APP_NAME)

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    from plyer import notification
    SYSTRAY_AVAILABLE = True
except Exception as e:
    logger.warning("Dependencias tray no disponibles: %s", e)
    SYSTRAY_AVAILABLE = False


class TrayIcon:
    def __init__(self, pos_module):
        self.icon = None
        self.pos_module = pos_module
        self._thread = None

    def create_image(self):
        """Crea imagen simple (TM)."""
        try:
            size = (64, 64)
            img = Image.new("RGBA", size, (255, 255, 255, 255))
            dc = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("arial.ttf", 28)
            except Exception:
                font = None
            dc.text((8, 10), "T", fill=(128, 0, 128, 255), font=font)
            dc.text((36, 10), "M", fill=(255, 215, 0, 255), font=font)
            return img
        except Exception as e:
            logger.error("Error creando imagen tray: %s", e)
            return None

    def notificacion_inicio(self):
        """Muestra notificación (plyer)."""
        try:
            if platform.system() == "Windows" or platform.system() == "Linux" or platform.system() == "Darwin":
                # plyer notificará en sistema adecuado (en exe requiere incluir plyer/platforms)
                notification.notify(
                    title="Servicio Iniciado",
                    message=f"{APP_NAME} se está ejecutando en segundo plano",
                    timeout=5
                )
                logger.info("Notificación de inicio mostrada")
            else:
                logger.info("Notificación: %s - %s", "Servicio Iniciado", APP_NAME)
        except Exception as e:
            logger.warning("No se pudo mostrar notificación: %s", e)

    def on_open_log(self, icon, item):
        try:
            log_file = os.environ.get("LOG_FILE", "pos_gateway.log")
            path = os.path.abspath(os.path.join(os.getcwd(), log_file))
            logger.info("Abriendo log: %s", path)
            if os.path.exists(path):
                os.startfile(path)
            else:
                logger.warning("Archivo de log no encontrado: %s", path)
        except Exception as e:
            logger.error("Error abriendo log: %s", e)

    def on_restart_pos(self, icon, item):
        try:
            logger.info("Tray -> reiniciar POS solicitado")
            if self.pos_module:
                new_port = self.pos_module.restart()
                logger.info("Nuevo puerto detectado: %s", new_port)
                self.notificacion_inicio()  # opcional: notificar reinicio
        except Exception as e:
            logger.error("Error reiniciando POS desde tray: %s", e)

    def on_quit(self, icon, item):
        logger.info("Tray -> salir solicitado")
        try:
            from core.singleton import cleanup_lock
            cleanup_lock()
        except Exception:
            pass
        try:
            icon.stop()
        except Exception:
            pass
        try:
            os._exit(0)
        except Exception:
            pass

    def _run_icon(self):
        """Función que corre el icon (en hilo)."""
        if not SYSTRAY_AVAILABLE:
            logger.info("pystray no disponible")
            return

        try:
            # Mostrar notificación al iniciar
            self.notificacion_inicio()

            menu = (
                pystray.MenuItem(APP_NAME, lambda: None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Ver Log", self.on_open_log),
                #pystray.MenuItem("Reiniciar POS", self.on_restart_pos),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", self.on_quit),
            )

            image = self.create_image()
            icon = pystray.Icon(APP_NAME, image, APP_NAME, menu=pystray.Menu(*menu))
            self.icon = icon  # mantener referencia en self evita GC
            logger.info("Iniciando icono de bandeja")
            icon.run()
            logger.info("Icono de bandeja terminado")
        except Exception as e:
            logger.error("Error en tray _run_icon: %s", e)

    def run(self):
        """Inicia el icon en un hilo demonio (si no está ya corriendo)."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_icon, daemon=True)
        self._thread.start()

import os
import logging
import threading

from config.settings import APP_NAME
logger = logging.getLogger()

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    SYSTRAY_AVAILABLE = True
except Exception:
    SYSTRAY_AVAILABLE = False

class TrayIcon:
    def __init__(self, pos_module):
        self.icon = None
        self.pos_module = pos_module

    def create_image(self):
        size = (64, 64)
        try:
            img = Image.new("RGBA", size, (255, 255, 255, 0))
            dc = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("arial.ttf", 28)
            except Exception:
                font = None
            dc.text((10, 15), "T", fill=(128, 0, 128, 255), font=font)
            dc.text((35, 15), "M", fill=(255, 215, 0, 255), font=font)
            return img
        except Exception:
            # Si PIL no está disponible, retornar None
            return None

    def on_quit(self, icon, item):
        logger.info("Tray -> salir solicitado")
        try:
            from core.singleton import cleanup_lock
            cleanup_lock()
        finally:
            try:
                # Forzar salida del proceso
                os._exit(0)
            except Exception:
                pass

    def on_open_log(self, icon, item):
        try:
            os.startfile(os.path.abspath(os.getcwd() + "/" + os.environ.get("LOG_FILE", "pos_gateway.log")))
        except Exception as e:
            logger.error("No se pudo abrir log: %s", e)

    def on_restart_pos(self, icon, item):
        try:
            logger.info("Tray -> Reiniciar POS manualmente")
            new_port = self.pos_module.restart()
            logger.info("Nuevo puerto detectado: %s", new_port)
        except Exception as e:
            logger.error("Error reiniciando POS desde tray: %s", e)

    def run(self):
        if not SYSTRAY_AVAILABLE:
            logger.info("pystray no disponible, no se muestra icono")
            return

        try:
            menu = (
                pystray.MenuItem(f"{APP_NAME}", lambda: None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Ver Log", self.on_open_log),
                # pystray.MenuItem("Reiniciar POS", self.on_restart_pos),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", self.on_quit)
            )

            icon = pystray.Icon(APP_NAME, self.create_image(), APP_NAME, menu=pystray.Menu(*menu))
            self.icon = icon
            icon.run()
        except Exception as e:
            logger.error("Tray icon error: %s", e)

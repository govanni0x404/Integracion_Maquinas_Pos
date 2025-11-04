"""
Módulo para mostrar un icono en la bandeja del sistema (system tray).
Usa plyer para notificaciones multiplataforma más confiables.
"""
import os
import logging
import threading
import platform
from config.settings import APP_NAME, LOG_FILE
from core.logging_config import logger
from core.singleton import ensure_single_instance,cleanup_lock

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    SYSTRAY_AVAILABLE = True
except Exception as e:
    logger.warning("Dependencias tray no disponibles: %s", e)
    SYSTRAY_AVAILABLE = False

try:
    from plyer import notification
    PLYER_AVAILABLE = True
except Exception:
    PLYER_AVAILABLE = False
    logger.warning("No disponible - notificaciones deshabilitadas")


class TrayIcon:
    """
    Clase que maneja el icono en la bandeja del sistema.
    Muestra un menú con opciones básicas y notificaciones.
    """

    def __init__(self, pos_module):
        self.icon = None
        self.pos_module = pos_module
        self._thread = None

    def create_image(self):
        try:
            size = (64, 64)
            img = Image.new("RGBA", size, (255, 255, 255, 255))
            dc = ImageDraw.Draw(img)
            
            try:
                font = ImageFont.truetype("arial.ttf", 28)
            except Exception:
                font = None
            
            # T morado, M amarillo
            dc.text((8, 10), "T", fill=(128, 0, 128, 255), font=font)
            dc.text((36, 10), "M", fill=(255, 215, 0, 255), font=font)
            
            return img
        except Exception as e:
            logger.error("Error creando imagen tray: %s", e)
            return None

    def notificacion_inicio(self):
        try:
            if not PLYER_AVAILABLE:
                logger.info("Servicio iniciado: %s (notificaciones no disponibles)", APP_NAME)
                return
            
            # Usar plyer para notificaciones (más confiable que win10toast)
            notification.notify(
                title="Servicio Iniciado",
                message=f"{APP_NAME} se está ejecutando en segundo plano",
                app_name=APP_NAME,
                timeout=5  # segundos
            )
            logger.info("Notificación Archivo de inicio mostrada")
            
        except Exception as e:
            logger.warning("No se pudo mostrar notificación: %s", e)
            logger.info("Servicio iniciado: %s", APP_NAME)

    def notificacion_custom(self, titulo, mensaje, timeout=3):
        try:
            if not PLYER_AVAILABLE:
                logger.info("Notificación: %s - %s", titulo, mensaje)
                return
            
            notification.notify(
                title=titulo,
                message=mensaje,
                app_name=APP_NAME,
                timeout=timeout
            )
            logger.info("Notificación mostrada: %s", titulo)
            
        except Exception as e:
            logger.warning("Error mostrando notificación: %s", e)

    def on_open_log(self, icon, item):
        try:
            path = os.path.abspath(os.path.join(os.getcwd(), LOG_FILE))
            logger.info("Abriendo log: %s", path)
            
            if not os.path.exists(path):
                logger.warning("Archivo de log no encontrado: %s", path)
                self.notificacion_custom("Error", "Archivo de log no encontrado")
                return
            
            # Multiplataforma
            sistema = platform.system()
            if sistema == "Windows":
                os.startfile(path)
            elif sistema == "Darwin":  # macOS
                import subprocess
                subprocess.call(['open', path])
            else:  # Linux
                import subprocess
                subprocess.call(['xdg-open', path])
                    
        except Exception as e:
            logger.error("Error abriendo log: %s", e)
            self.notificacion_custom("Error", "No se pudo abrir el log")

    def on_restart_pos(self, icon, item):
        """Handler para el menú 'Reiniciar POS'"""
        try:
            logger.info("Tray -> reiniciar POS solicitado")
            if self.pos_module:
                new_port = self.pos_module.restart()
                logger.info("Nuevo puerto detectado: %s", new_port)
                
                # Notificar resultado del reinicio
                if new_port:
                    self.notificacion_custom(
                        "POS Reiniciado",
                        f"Puerto detectado: {new_port}"
                    )
                else:
                    self.notificacion_custom(
                        "POS Reiniciado",
                        "No se detectó puerto COM"
                    )
                    
        except Exception as e:
            logger.error("Error reiniciando POS desde tray: %s", e)
            self.notificacion_custom("Error", "No se pudo reiniciar el POS")

    def on_quit(self, icon, item):
        """Handler para el menú 'Salir'"""
        logger.info("Tray -> salir solicitado")
        try:
            cleanup_lock()
        except Exception:
            pass
        try:
            if icon:
                icon.stop()
        except Exception:
            pass
        try:
            os._exit(0)
        except Exception:
            pass

    def _run_icon(self):
        """
        Función que corre el icono (en hilo).
        Maneja la creación y ejecución del icono de bandeja.
        """
        if not SYSTRAY_AVAILABLE:
            logger.info("pystray no disponible, no se muestra icono")
            return

        try:
            # Mostrar notificación al iniciar
            self.notificacion_inicio()

            # Crear menú (compatible con diferentes versiones de pystray)
            menu_items = [
                pystray.MenuItem(APP_NAME, lambda: None, enabled=False),
                pystray.MenuItem("Ver Log", self.on_open_log),
                # Descomentar para habilitar reinicio de POS desde el menú
                # pystray.MenuItem("Reiniciar POS", self.on_restart_pos),
                pystray.MenuItem("Salir", self.on_quit),
            ]

            # Intentar crear menú (versión nueva de pystray)
            try:
                menu = pystray.Menu(*menu_items)
            except Exception:
                # Fallback para versiones antiguas
                menu = tuple(menu_items)

            # Crear imagen
            image = self.create_image()
            if not image:
                logger.error("No se pudo crear imagen para icono")
                return

            # Crear icono
            icon = pystray.Icon(APP_NAME, image, APP_NAME, menu=menu)
            self.icon = icon  # Mantener referencia para evitar GC
            
            logger.info("Iniciando icono de bandeja del sistema...")
            icon.run()  # Bloqueante
            logger.info("Icono de bandeja terminado")
            
        except Exception as e:
            logger.error("Error en tray _run_icon: %s", e)
            import traceback
            logger.debug(traceback.format_exc())

    def run(self):
        """
        Inicia el icono en un hilo daemon (si no está ya corriendo).
        Este método es NO bloqueante.
        """
        if self._thread and self._thread.is_alive():
            logger.debug("Thread de tray ya está corriendo")
            return
            
        self._thread = threading.Thread(target=self._run_icon, daemon=True)
        self._thread.start()
        logger.info("Thread de icono de bandeja iniciado")

    def stop(self):
        """
        Detiene el icono de bandeja si está corriendo.
        """
        try:
            if self.icon:
                self.icon.stop()
                logger.info("Icono de bandeja detenido")
        except Exception as e:
            logger.debug("Error deteniendo icono: %s", e)
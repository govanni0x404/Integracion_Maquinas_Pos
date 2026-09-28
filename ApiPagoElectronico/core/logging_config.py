import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from config.settings import APP_NAME

def _get_log_dir():
    """
    Determina el directorio para el archivo de log.

    Orden de prioridad:
    1. LOG_FILE absoluto en variables de entorno
    2. Raíz del proyecto Python (directorio de app.py / main script)
    3. CWD (en el .exe compilado es la carpeta del .env, junto al ejecutable)
    """
    # 1. LOG_FILE absoluto en env vars
    env_log = os.environ.get("LOG_FILE", "")
    if env_log and os.path.isabs(env_log):
        return Path(env_log).parent, Path(env_log).name

    # 2. Modo desarrollo: usar el directorio del script principal (sys.argv[0])
    #    sys.executable en venv apunta a venv/bin/python3 — NO es útil para paths
    try:
        main_script = Path(sys.argv[0]).resolve()
        if main_script.suffix == ".py":
            # app.py → project_root/
            return main_script.parent, "pos_gateway.log"
    except Exception:
        pass

    # 3. CWD fallback
    return Path(os.getcwd()), env_log or "pos_gateway.log"

_log_dir, _log_filename = _get_log_dir()

LOG_DIR = _log_dir
# Nombre base sin extensión (ej. "pos_gateway"), usado para armar
# "<base>-<YYYY-MM-DD>.log" — un archivo de log distinto por día.
LOG_BASENAME = Path(_log_filename).stem


def daily_log_path(base_name: str = None, log_dir=None) -> Path:
    """
    Ruta del log de HOY para `base_name` (ej. 'pos_gateway', 'getnet',
    'transbank', 'mercadopago'). Cambia automáticamente al cruzar la
    medianoche — llamar de nuevo cada vez que se necesite el path vigente,
    no cachear el resultado en un proceso de larga duración.
    """
    base_name = base_name or LOG_BASENAME
    log_dir = Path(log_dir) if log_dir else LOG_DIR
    today = datetime.now().strftime("%Y-%m-%d")
    return log_dir / f"{base_name}-{today}.log"


def current_log_file(base_name: str = None) -> str:
    """Path (string) del log técnico/de negocio que se está escribiendo AHORA MISMO (el de hoy)."""
    return str(daily_log_path(base_name))


class DailyFileHandler(logging.Handler):
    """
    Handler de logging que escribe en un archivo distinto cada día
    (<base>-<YYYY-MM-DD>.log), para que ningún log crezca indefinidamente.

    Revisa la fecha en cada emit(), así que si el proceso queda corriendo
    durante el cambio de día (este es un servicio de larga duración, sin
    reinicio diario garantizado), el primer evento después de medianoche
    abre solo el archivo correspondiente sin perder ni duplicar líneas.
    """

    def __init__(self, log_dir, base_name: str, encoding="utf-8"):
        super().__init__()
        self._log_dir = Path(log_dir)
        self._base_name = base_name
        self._encoding = encoding
        self._current_date = None
        self._inner = None
        self._open_today()  # falla rápido acá si el directorio no es escribible

    def _open_today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today == self._current_date and self._inner is not None:
            return
        path = self._log_dir / f"{self._base_name}-{today}.log"
        new_inner = logging.FileHandler(str(path), encoding=self._encoding)
        if self.formatter:
            new_inner.setFormatter(self.formatter)
        old_inner = self._inner
        self._inner = new_inner
        self._current_date = today
        if old_inner is not None:
            old_inner.close()

    def setFormatter(self, fmt):
        super().setFormatter(fmt)
        if self._inner is not None:
            self._inner.setFormatter(fmt)

    def emit(self, record):
        try:
            self._open_today()
            self._inner.emit(record)
        except Exception:
            self.handleError(record)

    def close(self):
        try:
            if self._inner is not None:
                self._inner.close()
        finally:
            super().close()


# Se mantiene por compatibilidad (algunos módulos lo importan como referencia
# informativa): snapshot del log técnico de HOY tomado al iniciar el proceso.
# Para el path vigente en cualquier momento (incluso tras cruzar medianoche
# en un proceso de larga duración) usar current_log_file().
LOG_FILE = str(daily_log_path(LOG_BASENAME))

# Logger global
logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)

if not logger.handlers:
    try:
        fh = DailyFileHandler(LOG_DIR, LOG_BASENAME)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as e:
        # Fallback: log en home del usuario
        fallback_dir = Path.home()
        fh = DailyFileHandler(fallback_dir, LOG_BASENAME)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.warning(f"No se pudo crear log en {LOG_DIR}: {e}. Usando {fallback_dir}")

logger.info(f"Logger inicializado — log de hoy: {current_log_file()}")

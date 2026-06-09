import logging
import sys
import os
from pathlib import Path
from config.settings import APP_NAME

def _get_log_dir():
    """
    Determina el directorio para el archivo de log.

    Orden de prioridad:
    1. LOG_FILE absoluto en variables de entorno
    2. Junto al ejecutable IF es bundle PyInstaller (.app/Contents/MacOS/)
    3. Raíz del proyecto Python (directorio de app.py / main script)
    4. CWD
    """
    # 1. LOG_FILE absoluto en env vars
    env_log = os.environ.get("LOG_FILE", "")
    if env_log and os.path.isabs(env_log):
        return Path(env_log).parent, Path(env_log).name

    exe_path = Path(sys.executable)
    exe_dir  = exe_path.parent

    # 2. Bundle PyInstaller en .app macOS → subir afuera del bundle
    parts = exe_dir.parts
    if "Contents" in parts and "MacOS" in parts:
        outside_dir = exe_dir.parent.parent.parent  # MacOS→Contents→.app→dist/
        return outside_dir, "pos_gateway.log"

    # 3. Modo desarrollo: usar el directorio del script principal (sys.argv[0])
    #    sys.executable en venv apunta a venv/bin/python3 — NO es útil para paths
    try:
        main_script = Path(sys.argv[0]).resolve()
        if main_script.suffix == ".py":
            # app.py → project_root/
            return main_script.parent, "pos_gateway.log"
    except Exception:
        pass

    # 4. CWD fallback
    return Path(os.getcwd()), env_log or "pos_gateway.log"

_log_dir, _log_filename = _get_log_dir()
LOG_FILE = str(_log_dir / _log_filename)

# Logger global
logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)

if not logger.handlers:
    try:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as e:
        # Fallback: log en home del usuario
        fallback = Path.home() / "pos_gateway.log"
        fh = logging.FileHandler(str(fallback), encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.warning(f"No se pudo crear log en {LOG_FILE}: {e}. Usando {fallback}")

logger.info(f"Logger inicializado — log: {LOG_FILE}")

import os
import psutil
import logging
from pathlib import Path
from config.settings import APP_NAME    

logger = logging.getLogger(APP_NAME)
LOCK_FILE = "pos_gateway.lock"

def ensure_single_instance():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip())
            # Verifica si existe el proceso
            if pid and pid != os.getpid():
                logger.info(f"Instancia existente PID={pid}")
                return False
        except:
            pass

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    logger.info(f"Lock file creado: {LOCK_FILE} (PID={os.getpid()})")
    return True

def cleanup_lock():
    if os.path.exists(LOCK_FILE):
        try:
            os.remove(LOCK_FILE)
            logger.info(f"Lock file eliminado: {LOCK_FILE}")
        except Exception as e:
            logger.error(f"No se pudo eliminar lock file: {e}")

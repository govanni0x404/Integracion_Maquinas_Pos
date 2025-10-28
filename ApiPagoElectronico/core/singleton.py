import os
import psutil
import logging
from pathlib import Path
from config.settings import LOCK_FILE

logger = logging.getLogger()

def ensure_single_instance():
    """
    Verifica si ya hay otra instancia corriendo.
    Usa un archivo de lock con el PID del proceso.
    Retorna True si puede correr, False si ya hay otra instancia.
    """
    lock_path = Path(LOCK_FILE)
    current_pid = os.getpid()

    if lock_path.exists():
        try:
            with open(lock_path, "r") as f:
                old_pid = int(f.read().strip())

            # Verifica si el proceso existe
            if psutil.pid_exists(old_pid):
                try:
                    proc = psutil.Process(old_pid)
                    # Aquí se puede ajustar la detección de proceso
                    if "python" in proc.name().lower():
                        logger.error("Otra instancia detectada (PID=%s)", old_pid)
                        return False
                except psutil.NoSuchProcess:
                    pass

            # Si el proceso no existe, limpia el lock
            lock_path.unlink()
        except Exception as e:
            logger.warning("Error verificando lock file: %s", e)
            lock_path.unlink(missing_ok=True)

    # Crea el nuevo lock
    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(current_pid))
        logger.info("Lock file creado: %s (PID=%s)", lock_path, current_pid)
        return True
    except Exception as e:
        logger.error("No se pudo crear lock file: %s", e)
        return False

def cleanup_lock():
    try:
        Path(LOCK_FILE).unlink(missing_ok=True)
    except Exception:
        pass

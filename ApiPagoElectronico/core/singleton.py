import os
import psutil
import logging
import atexit
import signal
import sys
from pathlib import Path
from config.settings import APP_NAME
from typing import Optional

logger = logging.getLogger(APP_NAME)
LOCK_FILE = "pos_gateway.lock"

def _write_lock_for_current_pid():
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    logger.info(f"Lock file creado: {LOCK_FILE} (PID={os.getpid()})")

def _read_lock_pid():
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                return int(f.read().strip() or "0")
    except Exception as e:
        logger.warning(f"No se pudo leer el lock file: {e}")
    return 0

def _process_info(pid):
    try:
        p = psutil.Process(pid)
        return p, f"{p.name()} (pid={pid})"
    except Exception:
        return None, f"(pid={pid})"



def _ask_yes_no(prompt: str, default: Optional[bool] = None) -> bool:
    # Si no hay stdin interactivo, no preguntar
    if not sys.stdin or not sys.stdin.isatty():
        logger.info("Sin stdin interactivo. Usando valor por defecto.")
        return bool(default) if default is not None else False

    suffix = " [s/n]" if default is None else (" [S/n]" if default else " [s/N]")
    while True:
        try:
            ans = input(prompt + suffix + " ").strip().lower()
        except (EOFError, RuntimeError):
            return bool(default) if default is not None else False

        if not ans:
            if default is not None:
                return default
            continue

        if ans in ("s", "si", "sí", "y", "yes"):
            return True
        if ans in ("n", "no"):
            return False

        logger.info("Por favor responde 's' o 'n'.")

def ensure_single_instance_interactive(stop_existing_default=None, wait_seconds=5) -> bool:
    """
    Garantiza instancia única. Si hay otra instancia viva:
      - Pregunta al usuario si desea terminarla y continuar.
      - Si acepta: intenta terminarla y toma el lock.
      - Si rechaza: retorna False.
    stop_existing_default: bool|None — default de la pregunta (None = obliga respuesta).
    wait_seconds: tiempo de espera tras terminate() antes de forzar kill().
    """
    existing_pid = _read_lock_pid()

    # Si no hay lock o el lock apunta a este mismo proceso, escribe nuevo lock y sigue.
    if not existing_pid or existing_pid == os.getpid():
        _write_lock_for_current_pid()
        return True

    # Verifica si el proceso realmente existe
    if not psutil.pid_exists(existing_pid):
        logger.info(f"Se encontró lock huérfano de un PID inexistente ({existing_pid}). Se sobreescribe.")
        _write_lock_for_current_pid()
        return True

    # Hay un proceso vivo: informar y preguntar
    proc, label = _process_info(existing_pid)
    logger.info(f"Instancia existente detectada: {label}")

    if not _ask_yes_no(
        f"Ya hay otra instancia corriendo {label}. ¿Deseas terminarla y continuar?",
        default=stop_existing_default
    ):
        logger.info("Usuario eligió cancelar el arranque de la nueva instancia.")
        return False

    # Intentar terminación ordenada
    try:
        if proc is None:
            # Sin handle del proceso, intentar señal genérica
            os.kill(existing_pid, signal.SIGTERM if hasattr(signal, "SIGTERM") else 15)
        else:
            logger.info(f"Enviando terminate() a {label}...")
            proc.terminate()

        gone, alive = psutil.wait_procs([proc] if proc else [], timeout=wait_seconds)
        if alive:
            logger.warning(f"{label} no terminó en {wait_seconds}s. Forzando kill().")
            for p in alive:
                try:
                    p.kill()
                except Exception as e:
                    logger.error(f"No se pudo forzar kill() a {p.pid}: {e}")
        else:
            logger.info(f"{label} terminado correctamente.")
    except psutil.NoSuchProcess:
        logger.info(f"El proceso {existing_pid} ya no existe.")
    except PermissionError:
        logger.error("Permiso denegado para terminar el proceso existente. Ejecuta con privilegios adecuados.")
        return False
    except Exception as e:
        logger.error(f"Error al intentar terminar la instancia existente: {e}")
        return False

    # Tomar el lock para esta instancia
    _write_lock_for_current_pid()
    return True

def cleanup_lock():
    if os.path.exists(LOCK_FILE):
        try:
            # Evita borrar un lock que no nos pertenece (carrera entre procesos)
            pid_in_lock = _read_lock_pid()
            if pid_in_lock and pid_in_lock != os.getpid():
                logger.warning(f"Omitiendo eliminación del lock: pertenece a otro PID ({pid_in_lock}).")
                return
            os.remove(LOCK_FILE)
            logger.info(f"Lock file eliminado: {LOCK_FILE}")
        except Exception as e:
            logger.error(f"No se pudo eliminar lock file: {e}")

# Registrar limpieza en salida y señales comunes
def _register_lock_cleanup_handlers():
    atexit.register(cleanup_lock)

    def _handler(signum, frame):
        logger.info(f"Recibida señal {signum}. Limpiando lock y saliendo.")
        cleanup_lock()
        # Vuelve a la acción por defecto de la señal
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (getattr(signal, "SIGINT", None),
                getattr(signal, "SIGTERM", None),
                getattr(signal, "SIGHUP", None)):
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except Exception:
                pass



# --- Compatibilidad con código existente ---
def ensure_single_instance(stop_existing_default=None, wait_seconds=5):
    """
    Wrapper para mantener compat con importaciones antiguas.
    Se limita a delegar en ensure_single_instance_interactive().
    """
    return ensure_single_instance_interactive(
        stop_existing_default=stop_existing_default,
        wait_seconds=wait_seconds
    )

# opcional, para dejar claro qué exporta el módulo
_all_ = [
    "ensure_single_instance_interactive",
    "ensure_single_instance",
    "cleanup_lock",
]

_register_lock_cleanup_handlers()
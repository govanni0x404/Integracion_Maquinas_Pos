import os
import sys  # ← AGREGAR ESTA LÍNEA
import time
import logging
import atexit
import signal
from pathlib import Path
from config.settings import APP_NAME, LOCK_FILE
from typing import Optional

logger = logging.getLogger(APP_NAME)

try:
    import psutil  # type: ignore
    _PSUTIL_AVAILABLE = True
except Exception:
    psutil = None
    _PSUTIL_AVAILABLE = False

def _pid_exists(pid: int) -> bool:
    if not pid:
        return False
    if _PSUTIL_AVAILABLE and psutil is not None:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def _terminate_pid(pid: int, wait_seconds: float) -> bool:
    if not pid:
        return True
    if _PSUTIL_AVAILABLE and psutil is not None:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            gone, alive = psutil.wait_procs([proc], timeout=wait_seconds)
            if alive:
                for p in alive:
                    try:
                        p.kill()
                    except Exception:
                        pass
            return not _pid_exists(pid)
        except Exception:
            return not _pid_exists(pid)
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_TERMINATE = 0x0001
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, wintypes.DWORD(pid))
            if not handle:
                return not _pid_exists(pid)
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
            start = time.time()
            while time.time() - start < wait_seconds:
                if not _pid_exists(pid):
                    return True
                time.sleep(0.2)
            return not _pid_exists(pid)
        except Exception:
            return not _pid_exists(pid)
    try:
        os.kill(pid, signal.SIGTERM if hasattr(signal, "SIGTERM") else 15)
    except Exception:
        return not _pid_exists(pid)
    start = time.time()
    while time.time() - start < wait_seconds:
        if not _pid_exists(pid):
            return True
        time.sleep(0.2)
    try:
        if hasattr(signal, "SIGKILL"):
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    return not _pid_exists(pid)

def _es_nuestra_instancia(pid: int) -> bool:
    """
    ¿El PID del lock es realmente otra instancia de esta app? Tras un corte de luz
    el lock queda huérfano y Windows puede reasignar ese PID a cualquier otro
    programa: sin esta verificación se lo terminaba al arrancar.
    Sin psutil no se puede verificar y se asume que sí (comportamiento anterior).
    """
    if not (_PSUTIL_AVAILABLE and psutil is not None):
        return True
    try:
        proc = psutil.Process(pid)
        nombre = (proc.name() or "").lower()
        if getattr(sys, "frozen", False):
            return nombre == Path(sys.executable).name.lower()
        return "python" in nombre and any("app.py" in str(arg) for arg in proc.cmdline())
    except Exception:
        return False

def _write_lock_for_current_pid():
    os.makedirs(os.path.dirname(os.path.abspath(LOCK_FILE)), exist_ok=True)  # data/
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
    if _PSUTIL_AVAILABLE and psutil is not None:
        try:
            p = psutil.Process(pid)
            return p, f"{p.name()} (pid={pid})"
        except Exception:
            return None, f"(pid={pid})"
    return None, f"(pid={pid})"

def _ask_yes_no(prompt: str, default: Optional[bool] = None) -> bool:
    """Pregunta sí/no. Si no hay terminal interactiva, usa el default."""
    try:
        # Si no hay terminal interactiva o stdin, usar default inmediatamente
        if not sys.stdin or not sys.stdin.isatty():
            logger.info(f"Sin terminal interactiva, usando default={default}")
            return bool(default) if default is not None else True
    except Exception as e:
        logger.info(f"Error verificando terminal: {e}, usando default={default}")
        return bool(default) if default is not None else True
    
    # Hay terminal interactiva: preguntar
    suffix = " [s/n]: " if default is None else (" [S/n]: " if default else " [s/N]: ")
    
    for attempt in range(3):
        try:
            print(prompt + suffix, end='', flush=True)
            ans = sys.stdin.readline().strip().lower()
            
            if not ans:
                if default is not None:
                    return default
                else:
                    print("Por favor responde 's' o 'n'.")
                    continue

            if ans in ("s", "si", "sí", "y", "yes"):
                return True
            if ans in ("n", "no"):
                return False
            
            print("Por favor responde 's' o 'n'.")
            
        except (EOFError, KeyboardInterrupt):
            logger.info("\nEntrada interrumpida")
            return bool(default) if default is not None else True
        except Exception as e:
            logger.warning(f"Error leyendo entrada: {e}")
            return bool(default) if default is not None else True
    
    # Después de 3 intentos, usar default
    logger.warning("Demasiados intentos fallidos, usando default")
    return bool(default) if default is not None else True

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
    if not _pid_exists(existing_pid):
        logger.info(f"Se encontró lock huérfano de un PID inexistente ({existing_pid}). Se sobreescribe.")
        _write_lock_for_current_pid()
        return True

    if not _es_nuestra_instancia(existing_pid):
        _, label = _process_info(existing_pid)
        logger.warning(f"Lock huérfano: el PID {existing_pid} ahora es otro programa ({label}). No se toca; se sobreescribe el lock.")
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
        logger.info(f"Terminando instancia existente: {label}...")
        ok = _terminate_pid(existing_pid, float(wait_seconds))
        if not ok and _pid_exists(existing_pid):
            logger.warning(f"{label} no terminó correctamente.")
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

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except Exception:
            pass

    hup = getattr(signal, "SIGHUP", None)
    if hup is not None:
        try:
            signal.signal(hup, signal.SIG_IGN)
            logger.info("SIGHUP será ignorada para evitar cierres inesperados.")
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
__all__ = [
    "ensure_single_instance_interactive",
    "ensure_single_instance",
    "cleanup_lock",
]

_register_lock_cleanup_handlers()

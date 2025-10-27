import os
import subprocess
import traceback
import psutil
from pathlib import Path
from config import LOCK_FILE, APP_NAME
from logger_config import logger


def ensure_single_instance():
    lock_path = Path(LOCK_FILE)
    current_pid = os.getpid()
    
    if lock_path.exists():
        try:
            # Lee el PID del archivo de lock
            with open(lock_path, "r") as f:
                old_pid = int(f.read().strip())
            
            # Verifica si el proceso existe
            if psutil.pid_exists(old_pid):
                try:
                    proc = psutil.Process(old_pid)
                    # Verifica que sea el mismo programa
                    if "ApiPagoElectronico" in proc.name() or "python" in proc.name():
                        logger.error("Otra instancia detectada (PID=%s)", old_pid)
                        return False
                except psutil.NoSuchProcess:
                    # El proceso no existe, continúa
                    pass
            
            # Si llegamos aquí, el proceso no existe - limpia el lock
            lock_path.unlink()
            
        except Exception as e:
            logger.warning("Error verificando lock file: %s", e)
            lock_path.unlink(missing_ok=True)
    
    # Crea el nuevo lock file
    try:
        with open(lock_path, "w") as f:
            f.write(str(current_pid))
        logger.info("Lock file creado: %s (PID=%s)", lock_path, current_pid)
        return True
    except Exception as e:
        logger.error("No se pudo crear lock file: %s", e)
        return False


def cleanup_lock():
    try:
        Path(LOCK_FILE).unlink(missing_ok=True)
        logger.info("Lock file eliminado")
    except Exception as e:
        logger.debug("Error eliminando lock: %s", e)


def open_firewall_port(port):
    try:
        rule_name = f"{APP_NAME}_Port_{port}"
        
        def run_netsh(cmd):
            """Helper para ejecutar comandos netsh"""
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            logger.debug("Netsh: %s | Return: %s", cmd[:50], result.returncode)
            return result
        
        # ===== REGLA INBOUND (entrada) =====
        check_in = run_netsh(f'netsh advfirewall firewall show rule name="{rule_name}_Inbound"')
        
        # Si existe, la elimina para evitar duplicados
        if "No rules match" not in check_in.stdout:
            run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}_Inbound"')
            logger.info("Regla inbound duplicada eliminada para puerto %s", port)
        
        # Crea la regla inbound
        add_in = run_netsh(
            f'netsh advfirewall firewall add rule '
            f'name="{rule_name}_Inbound" '
            f'dir=in action=allow protocol=TCP localport={port} '
            f'profile=any'
        )
        
        if add_in.returncode == 0:
            logger.info("Firewall inbound configurado para puerto %s", port)
        else:
            logger.warning("Error creando regla inbound (admin required): %s", add_in.stderr[:100])
        
        # ===== REGLA OUTBOUND (salida) =====
        check_out = run_netsh(f'netsh advfirewall firewall show rule name="{rule_name}_Outbound"')
        
        if "No rules match" not in check_out.stdout:
            run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}_Outbound"')
            logger.info("Regla outbound duplicada eliminada para puerto %s", port)
        
        # Crea la regla outbound
        add_out = run_netsh(
            f'netsh advfirewall firewall add rule '
            f'name="{rule_name}_Outbound" '
            f'dir=out action=allow protocol=TCP localport={port} '
            f'profile=any'
        )
        
        if add_out.returncode == 0:
            logger.info("Firewall outbound configurado para puerto %s", port)
        else:
            logger.warning("Error creando regla outbound: %s", add_out.stderr[:100])
        
    except Exception as e:
        logger.error("Error configurando firewall: %s\n%s", e, traceback.format_exc())


def format_bytes(bytes_size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


def get_system_info():
    try:
        import platform
        
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "platform": platform.system(),
            "platform_version": platform.version(),
            "architecture": platform.machine(),
            "cpu_percent": cpu_percent,
            "memory_total": format_bytes(memory.total),
            "memory_used": format_bytes(memory.used),
            "memory_percent": memory.percent,
            "disk_total": format_bytes(disk.total),
            "disk_used": format_bytes(disk.used),
            "disk_percent": disk.percent
        }
    except Exception as e:
        logger.error("Error obteniendo info del sistema: %s", e)
        return {}
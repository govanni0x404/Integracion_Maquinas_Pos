import logging
import traceback    
from core.logging_config import APP_NAME

logger = logging.getLogger(APP_NAME)

def open_firewall_port(port):
    """
    Intenta abrir el puerto en Windows Firewall usando netsh.
    Crea reglas inbound y outbound específicas para el puerto.
    Limpia duplicados si existen.
    """
    try:
        import subprocess
        rule_name = f"{APP_NAME}_Port_{port}"

        # Función helper para ejecutar netsh y loggear
        def run_netsh(cmd):
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            logger.debug("Netsh cmd: %s | Return: %s | Stdout: %s | Stderr: %s", cmd, res.returncode, res.stdout.strip()[:100], res.stderr.strip()[:100])
            return res

        # Chequea si regla inbound existe
        check_in_cmd = f'netsh advfirewall firewall show rule name="{rule_name}_Inbound"'
        result_in = run_netsh(check_in_cmd)

        # Si existe, borra para evitar duplicados
        if "No rules match" not in result_in.stdout:
            delete_in_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}_Inbound"'
            run_netsh(delete_in_cmd)
            logger.info("Regla inbound duplicada eliminada para puerto %s", port)

        # Añade regla inbound (entrada) específica
        add_in_cmd = f'netsh advfirewall firewall add rule name="{rule_name}_Inbound" dir=in action=allow protocol=TCP localport={port} remoteport=any profile=any'
        res_in = run_netsh(add_in_cmd)

        if res_in.returncode == 0:
            logger.info("Regla inbound creada para puerto %s (TCP, local={port})", port)
        else:
            logger.warning("Error creando inbound para %s: %s", port, res_in.stderr)

        # Chequea si regla outbound existe
        check_out_cmd = f'netsh advfirewall firewall show rule name="{rule_name}_Outbound"'
        result_out = run_netsh(check_out_cmd)

        # Si existe, borra para evitar duplicados
        if "No rules match" not in result_out.stdout:
            delete_out_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}_Outbound"'
            run_netsh(delete_out_cmd)
            logger.info("Regla outbound duplicada eliminada para puerto %s", port)

        # Añade regla outbound (salida) específica
        add_out_cmd = f'netsh advfirewall firewall add rule name="{rule_name}_Outbound" dir=out action=allow protocol=TCP localport={port} remoteport=any profile=any'
        res_out = run_netsh(add_out_cmd)

        if res_out.returncode == 0:
            logger.info("Regla outbound creada para puerto %s (TCP, local={port})", port)
        else:
            logger.warning("Error creando outbound para %s: %s", port, res_out.stderr)

    except Exception as e:
        logger.error("Error en open_firewall_port: %s\n%s", e, traceback.format_exc())

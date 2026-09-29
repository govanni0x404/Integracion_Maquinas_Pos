"""
Logs de negocio: uno por integración de pago (Getnet, Transbank, Mercado Pago).

A diferencia del log técnico general (pos_gateway.log, que mezcla arranque,
firewall, panel, HTTP, etc.), estos archivos existen para que cualquiera
pueda abrirlos y entender en español simple qué pasó con las ventas de ESA
máquina/integración puntual: cuándo se inició una venta, qué se le pidió al
POS o a la pasarela, qué respondió, y cómo terminó. No reemplazan al log
técnico (que sigue recibiendo todo, incluyendo estos mismos eventos) — son
una vista narrada aparte, pensada para revisar rápido "qué pasó" sin tener
que interpretar JSON crudo ni tracebacks.
"""
import logging
from pathlib import Path

from core.logging_config import DailyFileHandler, log_dir_for

_loggers = {}


def _build_logger(nombre):
    log = logging.getLogger(f"negocio.{nombre}")
    log.setLevel(logging.INFO)
    log.propagate = False  # no duplicar estas líneas en el log técnico general

    if not log.handlers:
        # Un archivo por día (<nombre>-<YYYY-MM-DD>.log), igual que el log técnico.
        try:
            fh = DailyFileHandler(log_dir_for(nombre), nombre)  # logs/<nombre>/
        except Exception:
            fh = DailyFileHandler(Path.home(), nombre)
        fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        log.addHandler(fh)

    return log


def get_business_logger(nombre: str):
    """nombre: 'getnet' | 'transbank' | 'mercadopago'"""
    if nombre not in _loggers:
        _loggers[nombre] = _build_logger(nombre)
    return _loggers[nombre]


def fmt_monto(amount):
    """Formatea un monto en pesos chilenos legible: 15000 -> '$15.000'"""
    try:
        return "${:,.0f}".format(float(amount)).replace(",", ".")
    except (TypeError, ValueError):
        return f"${amount}"


getnet_log = get_business_logger("getnet")
transbank_log = get_business_logger("transbank")
mercadopago_log = get_business_logger("mercadopago")

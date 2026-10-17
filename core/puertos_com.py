"""
Coordinación de la detección de puertos COM entre Transbank y Getnet.

Las dos máquinas van por cable serie y suelen quedar en puertos parecidos
(COM3/COM4...). Cada detección prueba un puerto escribiéndole su propio
protocolo, así que dos detecciones en paralelo podían pisarse en el mismo
puerto o mandarle tramas a la máquina equivocada. Con este lock se detecta
de a una: la segunda, al entrar, ya ve el puerto que tomó la primera y lo
excluye.
"""
import threading

SONDEO_LOCK = threading.Lock()


def _venta_sin_confirmar(tipo):
    from core import transaction_store as tx_store
    try:
        return any(t.get("tipo") == tipo for t in tx_store.listar_no_resueltas())
    except Exception:
        return False


def vincular(pos, getnet, usar_transbank=True):
    """Cada detección excluye el puerto que ya tomó la otra máquina. Si la otra
    tiene una venta sin confirmar (ej. se cortó su cable y se espera que vuelva)
    también se le reserva su último puerto, para que al reconectar el cable
    nadie más le escriba antes de que recupere el resultado."""
    def puertos_de(maquina, tipo):
        puertos = [maquina.get_current_port()]
        if getattr(maquina, "_ultimo_puerto", None) and _venta_sin_confirmar(tipo):
            puertos.append(maquina._ultimo_puerto)
        return puertos

    getnet.puertos_ajenos = lambda: puertos_de(pos, "transbank") if usar_transbank else []
    pos.puertos_ajenos = lambda: puertos_de(getnet, "getnet")

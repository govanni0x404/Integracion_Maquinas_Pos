"""
Orden de carpetas: logs/<general|getnet|transbank|mercadopago|diagnostico>/ y data/.
Los archivos sueltos de versiones anteriores se mueven solos al arrancar.
"""
from core.estructura import migrar_archivos_sueltos
from core import logging_config
from core.business_logging import getnet_log, mercadopago_log


def _crear(path, texto="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(texto)
    return path


def _migrar(base):
    return migrar_archivos_sueltos(base, base / "data" / "pos_gateway.db", base / "data" / "pos_gateway.lock",
                                   base / "logs", "pos_gateway")


def test_mueve_la_base_con_su_wal_y_los_logs(tmp_path):
    for nombre in ("pos_gateway.db", "pos_gateway.db-wal", "pos_gateway.db-shm", "pos_gateway.lock",
                   "pos_gateway-2026-09-01.log", "getnet-2026-09-01.log", "transbank-2026-09-01.log",
                   "mercadopago-2026-09-01.log", "getnet.log", "auth_diag.log", ".env", "otro.log"):
        _crear(tmp_path / nombre, nombre)

    info = _migrar(tmp_path)
    assert info["errores"] == []

    data = tmp_path / "data"
    assert (data / "pos_gateway.db").read_text() == "pos_gateway.db"
    assert (data / "pos_gateway.db-wal").exists() and (data / "pos_gateway.db-shm").exists()
    assert (data / "pos_gateway.lock").exists()
    logs = tmp_path / "logs"
    assert (logs / "general" / "pos_gateway-2026-09-01.log").exists()
    assert (logs / "getnet" / "getnet-2026-09-01.log").exists()
    assert (logs / "getnet" / "getnet.log").exists()
    assert (logs / "transbank" / "transbank-2026-09-01.log").exists()
    assert (logs / "mercadopago" / "mercadopago-2026-09-01.log").exists()
    assert (logs / "diagnostico" / "auth_diag.log").exists()
    # Lo que no es nuestro o es configuración se queda donde está
    assert (tmp_path / ".env").exists() and (tmp_path / "otro.log").exists()
    assert not (tmp_path / "pos_gateway.db").exists()


def test_nunca_pisa_una_base_nueva(tmp_path):
    _crear(tmp_path / "pos_gateway.db", "vieja")
    _crear(tmp_path / "pos_gateway.db-wal", "wal-viejo")
    _crear(tmp_path / "data" / "pos_gateway.db", "nueva")
    info = _migrar(tmp_path)
    assert (tmp_path / "data" / "pos_gateway.db").read_text() == "nueva"
    assert (tmp_path / "pos_gateway.db").read_text() == "vieja"
    assert (tmp_path / "pos_gateway.db-wal").exists()  # el wal no se separa de su base
    assert info["omitidos"]


def test_segunda_ejecucion_no_hace_nada(tmp_path):
    _crear(tmp_path / "getnet-2026-09-01.log")
    _migrar(tmp_path)
    assert _migrar(tmp_path) == {"movidos": [], "errores": [], "omitidos": []}


def test_cada_log_escribe_en_su_carpeta():
    raiz = logging_config.LOG_DIR
    assert logging_config.current_log_file().startswith(str(raiz / "general"))
    assert logging_config.current_log_file("getnet").startswith(str(raiz / "getnet"))
    getnet_log.info("prueba getnet")
    mercadopago_log.info("prueba mp")
    for h in getnet_log.handlers + mercadopago_log.handlers:
        h.flush()
    assert "prueba getnet" in open(logging_config.current_log_file("getnet"), encoding="utf-8").read()
    assert "prueba mp" in open(logging_config.current_log_file("mercadopago"), encoding="utf-8").read()

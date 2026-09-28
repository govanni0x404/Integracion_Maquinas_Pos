"""
Getnet: una venta que ya se envió al POS nunca debe quedar con un estado final
(ERROR/RECHAZADO) si no hay confirmación real de la máquina. Tiene que quedar
INDETERMINADA para poder validarla después (Command 101 / panel).
"""
import time

import pytest

from conftest import AUTH, FakePOS
from core import transaction_store as tx_store
from pos import getnet_module as gm
from pos.getnet_module import GetnetModule

AHORA = time.time()


def _fecha(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


@pytest.fixture
def getnet(monkeypatch):
    mod = GetnetModule()
    monkeypatch.setattr(mod, "connect", lambda: True)
    monkeypatch.setattr(mod, "disconnect", lambda: None)
    return mod


def _envio(mod, monkeypatch, ok=True, incierto=False):
    def send(command):
        mod._send_uncertain = incierto
        return ok
    monkeypatch.setattr(mod, "_send_command", send)


# ── Venta (Command 100) ───────────────────────────────────────

def test_aprobada(getnet, monkeypatch):
    _envio(getnet, monkeypatch)
    monkeypatch.setattr(getnet, "_read_response", lambda **kw: {"functionCode": 100, "responseCode": 0})
    assert getnet.do_sale_with_timeout(1000, timeout=5, ticket="1")["status"] == "success"


def test_cancelada_por_pos_1006(getnet, monkeypatch):
    _envio(getnet, monkeypatch)
    monkeypatch.setattr(getnet, "_read_response", lambda **kw: {"FunctionCode": 100, "ResponseCode": 1006})
    assert getnet.do_sale_with_timeout(1000, timeout=5, ticket="1")["status"] == "failed"


def test_no_enviada_es_error_seguro(getnet, monkeypatch):
    _envio(getnet, monkeypatch, ok=False, incierto=False)
    res = getnet.do_sale_with_timeout(1000, timeout=5, ticket="1")
    assert res["status"] == "error"


def test_corte_durante_el_envio_queda_indeterminada(getnet, monkeypatch):
    _envio(getnet, monkeypatch, ok=False, incierto=True)
    res = getnet.do_sale_with_timeout(1000, timeout=5, ticket="1")
    assert res["status"] == "indeterminada"
    assert res["ticket"] == "1"


def test_corte_esperando_respuesta_queda_indeterminada(getnet, monkeypatch):
    _envio(getnet, monkeypatch)
    monkeypatch.setattr(getnet, "_read_response", lambda **kw: None)
    assert getnet.do_sale_with_timeout(1000, timeout=5, ticket="1")["status"] == "indeterminada"


def test_excepcion_despues_de_enviar_queda_indeterminada(getnet, monkeypatch):
    _envio(getnet, monkeypatch)

    def explota(**kw):
        raise OSError("device disconnected")
    monkeypatch.setattr(getnet, "_read_response", explota)
    assert getnet.do_sale_with_timeout(1000, timeout=5, ticket="1")["status"] == "indeterminada"


def test_error_de_impresion_queda_indeterminada(getnet, monkeypatch):
    _envio(getnet, monkeypatch)
    monkeypatch.setattr(getnet, "_read_response", lambda **kw: {"FunctionCode": 100, "ResponseCode": 1004})
    res = getnet.do_sale_with_timeout(1000, timeout=5, ticket="1")
    assert res["status"] == "indeterminada"
    assert res["response"]["ResponseCode"] == 1004


def test_send_command_marca_envio_incierto():
    mod = GetnetModule()

    class PuertoQueSeCae:
        is_open = True

        def write(self, data):
            raise OSError("se desconectó el USB")

        def flush(self):
            pass

    mod.serial_connection = PuertoQueSeCae()
    assert mod._send_command({"Command": 100}) is False
    assert mod._send_uncertain is True

    mod.serial_connection = None
    assert mod._send_command({"Command": 100}) is False
    assert mod._send_uncertain is False


# ── Reconciliación (Command 101) ──────────────────────────────

def _ultimo(mod, monkeypatch, resultado):
    monkeypatch.setattr(mod, "get_last_receipt", lambda timeout=15: resultado)


def _found(**campos):
    return {"status": "found", "response": {"FunctionCode": 101, **campos}}


def test_recon_pos_sin_conexion(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, {"status": "error", "message": "sin puerto"})
    assert getnet.reconciliar_ticket("1", 1000, sent_at=AHORA)["status"] == "no_resuelto"


def test_recon_comprobante_nuevo_por_fecha_aprobado(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000, AccountingDate=_fecha(AHORA + 30)))
    assert getnet.reconciliar_ticket("1", 1000, sent_at=AHORA)["status"] == "aprobado"


def test_recon_comprobante_nuevo_por_fecha_cancelado(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, _found(ResponseCode=1006, Amount=1000, AccountingDate=_fecha(AHORA + 30)))
    assert getnet.reconciliar_ticket("1", 1000, sent_at=AHORA)["status"] == "rechazado"


def test_recon_venta_anterior_mismo_monto_no_se_confunde(getnet, monkeypatch):
    # Antes: se comparaba solo por monto y esto daba APROBADO una venta no pagada.
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000, OperationId=55,
                                        AccountingDate=_fecha(AHORA - 3600)))
    res = getnet.reconciliar_ticket("1", 1000, sent_at=AHORA, known_operation_ids={"55"})
    assert res["status"] == "no_resuelto"  # la venta podría seguir en curso


def test_recon_venta_anterior_por_operation_id_aunque_la_fecha_sea_reciente(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000, OperationId=55,
                                        AccountingDate=_fecha(AHORA - 20)))
    res = getnet.reconciliar_ticket("1", 1000, sent_at=AHORA, known_operation_ids={"55"})
    assert res["status"] == "no_resuelto"


def test_recon_venta_anterior_pasada_la_ventana_se_descarta(getnet, monkeypatch):
    enviado = AHORA - gm.RECON_VENTANA_SEGUNDOS - 10
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000, OperationId=55))
    res = getnet.reconciliar_ticket("1", 1000, sent_at=enviado, known_operation_ids={"55"})
    assert res["status"] == "rechazado"


def test_recon_operation_id_nuevo_sin_fecha(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000, OperationId=56))
    res = getnet.reconciliar_ticket("1", 1000, sent_at=AHORA, known_operation_ids={"55"})
    assert res["status"] == "aprobado"


def test_recon_sin_datos_para_correlacionar_va_al_panel(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000))
    assert getnet.reconciliar_ticket("1", 1000, sent_at=AHORA)["status"] == "no_resuelto"


def test_recon_monto_distinto_va_al_panel(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=999, AccountingDate=_fecha(AHORA + 30)))
    assert getnet.reconciliar_ticket("1", 1000, sent_at=AHORA)["status"] == "no_resuelto"


def test_recon_monto_como_texto(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000, AccountingDate=_fecha(AHORA + 30)))
    assert getnet.reconciliar_ticket("1", "1000", sent_at=AHORA)["status"] == "aprobado"


def test_recon_ticket_coincide(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000, Ticket="1"))
    assert getnet.reconciliar_ticket("1", 1000, sent_at=AHORA)["status"] == "aprobado"


def test_recon_sin_comprobantes_dentro_de_la_ventana(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, {"status": "not_found", "response": {"ResponseCode": 99}})
    assert getnet.reconciliar_ticket("1", 1000, sent_at=AHORA)["status"] == "no_resuelto"


def test_recon_sin_comprobantes_pasada_la_ventana(getnet, monkeypatch):
    _ultimo(getnet, monkeypatch, {"status": "not_found", "response": {"ResponseCode": 99}})
    enviado = AHORA - gm.RECON_VENTANA_SEGUNDOS - 10
    assert getnet.reconciliar_ticket("1", 1000, sent_at=enviado)["status"] == "rechazado"


# ── Módulo: cierre de puerto y respuesta pendiente ────────────

def test_disconnect_con_cable_cortado_no_lanza():
    mod = GetnetModule()

    class PuertoMuerto:
        is_open = True

        def close(self):
            raise OSError("El dispositivo no está conectado")

    mod.serial_connection = PuertoMuerto()
    mod.disconnect()  # antes lanzaba y la venta terminaba en ERROR
    assert mod.serial_connection is None


def test_venta_indeterminada_aunque_falle_el_cierre(monkeypatch):
    mod = GetnetModule()

    class PuertoMuerto:
        is_open = True

        def close(self):
            raise OSError("El dispositivo no está conectado")

    def conectar():
        mod.serial_connection = PuertoMuerto()
        return True

    monkeypatch.setattr(mod, "connect", conectar)
    _envio(mod, monkeypatch)
    monkeypatch.setattr(mod, "_read_response", lambda **kw: None)
    assert mod.do_sale_with_timeout(1000, timeout=5, ticket="1")["status"] == "indeterminada"


def test_resolver_pendiente_toma_la_respuesta_que_entrega_el_pos(getnet, monkeypatch):
    monkeypatch.setattr(getnet, "_read_response", lambda **kw: {
        "FunctionCode": 100, "ResponseCode": 0, "Amount": 1000, "AccountingDate": _fecha(AHORA + 20)})
    res = getnet.resolver_venta_pendiente("1", 1000, sent_at=AHORA)
    assert res["status"] == "aprobado"


def test_resolver_pendiente_descarta_respuesta_vieja_y_consulta_101(getnet, monkeypatch):
    monkeypatch.setattr(getnet, "_read_response", lambda **kw: {
        "FunctionCode": 100, "ResponseCode": 0, "Amount": 1000, "OperationId": 55})
    _ultimo(getnet, monkeypatch, _found(ResponseCode=0, Amount=1000, OperationId=56))
    res = getnet.resolver_venta_pendiente("1", 1000, sent_at=AHORA, known_operation_ids={"55"})
    assert res["status"] == "aprobado"
    assert res["response"]["OperationId"] == 56


def test_resolver_pendiente_sin_conexion(monkeypatch):
    mod = GetnetModule()
    monkeypatch.setattr(mod, "connect", lambda: False)
    assert mod.resolver_venta_pendiente("1", 1000, sent_at=AHORA)["status"] == "no_resuelto"


def test_puerto_presente(monkeypatch):
    mod = GetnetModule()
    assert mod.puerto_presente() is None
    mod._ultimo_puerto = "COM7"
    monkeypatch.setattr(gm.serial.tools.list_ports, "comports",
                        lambda: [type("P", (), {"device": "COM7"})()])
    assert mod.puerto_presente() is True
    monkeypatch.setattr(gm.serial.tools.list_ports, "comports", lambda: [])
    assert mod.puerto_presente() is False


# ── Flujo completo /pago: la petición sigue abierta hasta que vuelve el POS ──

import functools  # noqa: E402
import threading  # noqa: E402


class FakeGetnet:
    def __init__(self):
        self.venta = {"status": "indeterminada"}
        self.conectado = False
        self.ultimo = {"status": "error", "message": "POS desconectado"}
        self.recon_calls = []

    def do_sale_with_timeout(self, amount, timeout=30, ticket=None):
        return {**self.venta, "ticket": ticket}

    def puerto_presente(self):
        return self.conectado

    def resolver_venta_pendiente(self, ticket, **kw):
        self.recon_calls.append({"ticket": ticket, **kw})
        mod = GetnetModule()
        mod.get_last_receipt = lambda timeout=15: self.ultimo
        return mod.reconciliar_ticket(ticket, **kw)

    def is_online(self):
        return self.conectado

    def solicitar_cancelacion(self):
        return False

    def get_current_port(self):
        return "COM_GETNET"


VENTA = {"type": "getnet", "id_sucursal": "1", "nombre_caja": "CAJA_TEST",
         "terminal_id": "POS_TEST", "amount": 1000, "timeout": 30}


@pytest.fixture
def getnet_server():
    from server.api_server import APIServer
    fake = FakeGetnet()
    srv = APIServer(FakePOS(), fake)
    srv.esperar_resolucion_getnet = functools.partial(
        srv.esperar_resolucion_getnet, intervalo=0.02, espera_tras_intento=0.02, reescaneo=0.05)
    yield srv, fake
    srv._stop_local_worker.set()
    srv._stop_cleanup.set()
    srv._stop_recon_monitor.set()


def _pago_en_segundo_plano(srv, body):
    out = {}

    def run():
        out["resp"] = srv.app.test_client().post("/pago", json=body, headers=AUTH)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _esperar(cond, segundos=3.0):
    fin = time.time() + segundos
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_cable_cortado_la_peticion_sigue_abierta_y_se_aprueba_al_reconectar(getnet_server):
    srv, fake = getnet_server
    t, out = _pago_en_segundo_plano(srv, {**VENTA, "tx_id": "venta-1"})

    assert _esperar(lambda: (tx_store.obtener("venta-1") or {}).get("estado") == "INDETERMINADA")
    time.sleep(0.3)
    assert t.is_alive()  # la petición sigue abierta con el cable desconectado
    estado = srv.app.test_client().get("/pago/estado/venta-1", headers=AUTH).get_json()
    assert estado["estado"] == "ESPERANDO_POS"
    assert fake.recon_calls == []  # sin puerto no se toca nada

    # Se reconecta la máquina: el cliente ya pagó y el POS tiene el comprobante nuevo
    fake.ultimo = _found(ResponseCode=0, Amount=1000, AccountingDate=_fecha(time.time()))
    fake.conectado = True
    t.join(3)
    assert not t.is_alive()
    body = out["resp"].get_json()
    assert body["estado"] == "APROBADO"
    assert body["transaction_id"] == "venta-1"
    tx = tx_store.obtener("venta-1")
    assert tx["estado"] == "APROBADO"
    assert tx["resuelto_por"] == "POS tras reconexión"


def test_reconecta_con_la_venta_aun_en_curso_sigue_esperando(getnet_server):
    srv, fake = getnet_server
    # Venta previa aprobada con comprobante 55
    fake.venta = {"status": "success", "response": {"ResponseCode": 0, "OperationId": 55, "Amount": 1000}}
    assert srv.app.test_client().post("/pago", json=VENTA, headers=AUTH).get_json()["estado"] == "APROBADO"

    fake.venta = {"status": "indeterminada"}
    fake.conectado = True
    fake.ultimo = _found(ResponseCode=0, Amount=1000, OperationId=55)  # todavía el de la venta anterior
    t, out = _pago_en_segundo_plano(srv, {**VENTA, "tx_id": "venta-2"})
    assert _esperar(lambda: len(fake.recon_calls) >= 3)
    assert t.is_alive()  # no se confundió con la venta anterior del mismo monto

    fake.ultimo = _found(ResponseCode=1006, Amount=1000, OperationId=56)  # el POS terminó: cancelada
    t.join(3)
    assert out["resp"].get_json()["estado"] == "RECHAZADO"
    assert tx_store.obtener("venta-2")["estado"] == "RECHAZADO"


def test_cancelar_la_espera_deja_la_venta_indeterminada(getnet_server):
    srv, fake = getnet_server
    t, out = _pago_en_segundo_plano(srv, {**VENTA, "tx_id": "venta-3"})
    assert _esperar(lambda: (tx_store.obtener("venta-3") or {}).get("estado") == "INDETERMINADA")

    r = srv.app.test_client().post("/pago/cancelar/venta-3", headers=AUTH)
    assert r.get_json()["estado"] == "INDETERMINADA"
    t.join(3)
    body = out["resp"].get_json()
    assert body["estado"] == "INDETERMINADA"
    assert "/pago/estado/" in body["message"]
    assert tx_store.obtener("venta-3")["estado"] == "INDETERMINADA"

    # La caja sigue bloqueada: el cliente pudo haber pagado
    r = srv.app.test_client().post("/pago", json=VENTA, headers=AUTH)
    assert r.status_code == 409
    assert r.get_json()["pending_transaction_id"] == "venta-3"


def test_resolver_desde_el_panel_libera_la_peticion(getnet_server):
    srv, fake = getnet_server
    t, out = _pago_en_segundo_plano(srv, {**VENTA, "tx_id": "venta-4"})
    assert _esperar(lambda: (tx_store.obtener("venta-4") or {}).get("estado") == "INDETERMINADA")

    srv.app.test_client().post("/panel/pendientes/venta-4/resolver", json={"estado": "APROBADO"})
    t.join(3)
    assert out["resp"].get_json()["estado"] == "APROBADO"
    tx = tx_store.obtener("venta-4")
    assert tx["estado"] == "APROBADO" and tx["resuelto_por"] == "panel"


def test_tx_id_del_cliente_validado(getnet_server):
    srv, fake = getnet_server
    c = srv.app.test_client()
    assert c.post("/pago", json={**VENTA, "tx_id": "con espacios"}, headers=AUTH).status_code == 400
    tx_store.registrar_intento("usado", "getnet", "1", "POS_TEST", "1", "CAJA_TEST", 1000)
    tx_store.actualizar_estado("usado", "APROBADO")
    assert c.post("/pago", json={**VENTA, "tx_id": "usado"}, headers=AUTH).status_code == 409


def test_limite_de_espera_responde_indeterminada(getnet_server):
    srv, fake = getnet_server
    srv.esperar_resolucion_getnet = functools.partial(srv.esperar_resolucion_getnet, max_espera=0.3)
    inicio = time.time()
    t, out = _pago_en_segundo_plano(srv, {**VENTA, "tx_id": "venta-5"})
    t.join(3)
    assert not t.is_alive()
    assert time.time() - inicio < 2
    body = out["resp"].get_json()
    assert body["estado"] == "INDETERMINADA"
    assert "/pago/estado/venta-5" in body["message"]
    # Sigue sin confirmar y bloqueando la caja; el monitor la puede retomar
    assert tx_store.obtener("venta-5")["estado"] == "INDETERMINADA"
    assert "venta-5" not in srv._esperando_getnet
    assert srv.app.test_client().post("/pago", json=VENTA, headers=AUTH).status_code == 409


def test_limite_cero_es_sin_limite(getnet_server):
    srv, fake = getnet_server
    srv.esperar_resolucion_getnet = functools.partial(srv.esperar_resolucion_getnet, max_espera=0)
    t, out = _pago_en_segundo_plano(srv, {**VENTA, "tx_id": "venta-6"})
    time.sleep(0.5)
    assert t.is_alive()
    srv.app.test_client().post("/pago/cancelar/venta-6", headers=AUTH)
    t.join(3)
    assert out["resp"].get_json()["estado"] == "INDETERMINADA"


# ── Cancelar Venta (Command 116) ──────────────────────────────

import json  # noqa: E402


def _trama(**resp):
    inner = json.dumps(resp, separators=(",", ":"))
    return json.dumps({"JsonSerialized": inner, "Sign": "X"}, separators=(",", ":")) + "\r\n"


class PosSimulado:
    """Puerto serie de un POS Getnet: la caja pide cancelar apenas empieza a
    esperar la venta, y el POS contesta el 116 con `al_116`."""
    is_open = True

    def __init__(self, mod, al_116):
        self.mod = mod
        self.al_116 = al_116
        self.buf = ""
        self.enviados = []
        self.cancelacion_pedida = False

    @property
    def in_waiting(self):
        if self.enviados == [100] and not self.cancelacion_pedida:
            self.cancelacion_pedida = True
            assert self.mod.solicitar_cancelacion() is True
        return len(self.buf)

    def read(self, n):
        data, self.buf = self.buf[:n], self.buf[n:]
        return data.encode()

    def write(self, data):
        comando = json.loads(json.loads(data)["JsonSerialized"])["Command"]
        self.enviados.append(comando)
        if comando == 116:
            self.buf += "".join(self.al_116)

    def flush(self):
        pass


def _venta_con_pos(monkeypatch, al_116, timeout=5):
    mod = GetnetModule()
    pos = PosSimulado(mod, al_116)

    def conectar():
        mod.serial_connection = pos
        return True
    monkeypatch.setattr(mod, "connect", conectar)
    monkeypatch.setattr(mod, "disconnect", lambda: None)
    monkeypatch.setattr(gm, "ESPERA_TRAS_CANCELAR_SEGUNDOS", 0.3)
    return mod.do_sale_with_timeout(1000, timeout=timeout, ticket="1"), pos, mod


def test_cancelar_venta_aceptada_por_el_pos(monkeypatch):
    res, pos, mod = _venta_con_pos(monkeypatch, [_trama(FunctionCode=116, ResponseCode=0, ResponseMessage="Aprobado")])
    assert pos.enviados == [100, 116]
    assert res["status"] == "failed"
    assert res["cancelada_desde_caja"] is True
    assert mod.solicitar_cancelacion() is False  # la venta ya terminó


def test_cancelar_venta_con_respuesta_1006_del_pos(monkeypatch):
    res, pos, _ = _venta_con_pos(monkeypatch, [
        _trama(FunctionCode=116, ResponseCode=0, ResponseMessage="Aprobado"),
        _trama(FunctionCode=100, ResponseCode=1006, ResponseMessage="Cancelado por POS"),
    ])
    assert res["status"] == "failed"
    assert res["response"]["ResponseCode"] == 1006


def test_cancelacion_rechazada_sigue_esperando_la_venta(monkeypatch):
    # El cliente ya ingresó el PIN: el POS no cancela y la venta se aprueba igual
    res, _, _ = _venta_con_pos(monkeypatch, [
        _trama(FunctionCode=116, ResponseCode=1, ResponseMessage="No se puede cancelar"),
        _trama(FunctionCode=100, ResponseCode=0, AuthorizationCode="AB12"),
    ])
    assert res["status"] == "success"


def test_cancelacion_rechazada_sin_resultado_queda_indeterminada(monkeypatch):
    res, _, _ = _venta_con_pos(monkeypatch, [_trama(FunctionCode=116, ResponseCode=1)], timeout=1)
    assert res["status"] == "indeterminada"


def test_solicitar_cancelacion_sin_venta_en_curso():
    assert GetnetModule().solicitar_cancelacion() is False


# ── Detección de puerto ───────────────────────────────────────

def test_is_online_no_sondea_si_el_puerto_esta_tomado(monkeypatch):
    mod = GetnetModule()
    mod.port = None
    mod._ultimo_puerto = "COM7"
    monkeypatch.setattr(gm.serial.tools.list_ports, "comports",
                        lambda: [type("P", (), {"device": "COM7"})()])

    def no_sondear(*a, **kw):
        raise AssertionError("no se debe sondear con una venta en curso")
    monkeypatch.setattr(mod, "_find_getnet_port", no_sondear)
    with mod._serial_lock:
        assert mod.is_online() is True


def test_deteccion_nunca_sondea_puertos_ajenos(monkeypatch):
    mod = GetnetModule()
    mod._port_cache = None
    mod.puertos_ajenos = lambda: ["COM1"]
    puerto = lambda dev: type("P", (), {"device": dev, "description": "USB Serial"})()
    monkeypatch.setattr(gm.serial.tools.list_ports, "comports", lambda: [puerto("COM1"), puerto("COM2")])
    sondeados = []
    monkeypatch.setattr(mod, "_test_port_connection", lambda p: sondeados.append(p) or p == "COM2")
    assert mod._find_getnet_port() == "COM2"
    assert sondeados == ["COM2"]


def test_api_excluye_el_puerto_de_transbank(monkeypatch):
    from server.api_server import APIServer
    monkeypatch.setattr("server.api_server.USAR_POS_FISICO", True)
    fake = FakeGetnet()
    srv = APIServer(FakePOS(), fake)
    try:
        assert fake.puertos_ajenos() == ["COM_TEST"]
    finally:
        srv._stop_local_worker.set()
        srv._stop_cleanup.set()
        srv._stop_recon_monitor.set()


class FakeGetnetCancelable(FakeGetnet):
    """La venta queda esperando en el POS hasta que la caja pide cancelarla."""

    def __init__(self):
        super().__init__()
        self.en_curso = threading.Event()
        self.cancelar = threading.Event()

    def do_sale_with_timeout(self, amount, timeout=30, ticket=None):
        self.en_curso.set()
        if self.cancelar.wait(3):
            return {"status": "failed", "cancelada_desde_caja": True, "ticket": ticket,
                    "response": {"FunctionCode": 116, "ResponseCode": 0}}
        return {"status": "indeterminada", "ticket": ticket}

    def solicitar_cancelacion(self):
        if not self.en_curso.is_set():
            return False
        self.cancelar.set()
        return True


def test_cancelar_pide_116_y_libera_la_caja():
    from server.api_server import APIServer
    fake = FakeGetnetCancelable()
    srv = APIServer(FakePOS(), fake)
    try:
        t, out = _pago_en_segundo_plano(srv, {**VENTA, "tx_id": "venta-116"})
        assert fake.en_curso.wait(3)

        r = srv.app.test_client().post("/pago/cancelar/venta-116", headers=AUTH)
        assert r.status_code == 200
        assert r.get_json()["estado"] == "RECHAZADO"
        t.join(3)
        assert out["resp"].get_json()["estado"] == "RECHAZADO"
        assert tx_store.obtener("venta-116")["estado"] == "RECHAZADO"

        # No quedó nada sin confirmar: la caja puede vender de nuevo
        fake.venta = {"status": "success", "response": {"ResponseCode": 0}}
        fake.do_sale_with_timeout = lambda amount, timeout=30, ticket=None: {**fake.venta, "ticket": ticket}
        assert srv.app.test_client().post("/pago", json=VENTA, headers=AUTH).get_json()["estado"] == "APROBADO"
    finally:
        srv._stop_local_worker.set()
        srv._stop_cleanup.set()
        srv._stop_recon_monitor.set()

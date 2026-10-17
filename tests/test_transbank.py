import pytest
import threading
import time

from conftest import AUTH
from core import transaction_store as tx_store
from pos.pos_module import POSModule

VENTA = {"type": "transbank", "id_sucursal": "1", "nombre_caja": "CAJA_TEST",
         "terminal_id": "POS_TEST", "amount": 1500, "timeout": 30}


def _esperar(cond, segundos=3.0):
    fin = time.time() + segundos
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ── POSModule.do_sale_with_timeout ────────────────────────────

def test_modulo_respuesta_a_tiempo(monkeypatch):
    pm = POSModule("COM_TEST")
    pm.current_port = "COM_TEST"
    monkeypatch.setattr(pm, "open_port_and_sale", lambda port, amount, ticket=None: {"status": "success"})
    tardio = []
    res = pm.do_sale_with_timeout(1000, timeout=1, ticket="120000", on_late_result=tardio.append)
    assert res == {"status": "success"}
    time.sleep(0.05)
    assert tardio == []


def test_modulo_timeout_indeterminada_y_resultado_tardio(monkeypatch):
    pm = POSModule("COM_TEST")
    pm.current_port = "COM_TEST"

    def venta_lenta(port, amount, ticket=None):
        time.sleep(0.3)
        return {"status": "success", "ticket": ticket}

    monkeypatch.setattr(pm, "open_port_and_sale", venta_lenta)
    llegado = threading.Event()
    tardio = []

    def on_late(res):
        tardio.append(res)
        llegado.set()

    res = pm.do_sale_with_timeout(1000, timeout=0.05, ticket="120001", on_late_result=on_late)
    assert res["status"] == "indeterminada"
    assert res["ticket"] == "120001"
    assert llegado.wait(2)
    assert tardio == [{"status": "success", "ticket": "120001"}]


# ── Flujo /pago transbank ─────────────────────────────────────

def test_venta_aprobada_queda_registrada(client, fake_pos):
    r = client.post("/pago", json=VENTA, headers=AUTH)
    body = r.get_json()
    assert r.status_code == 200
    assert body["estado"] == "APROBADO"
    tx = tx_store.obtener(body["transaction_id"])
    assert tx["tipo"] == "transbank"
    assert tx["estado"] == "APROBADO"
    assert tx["ticket"] == fake_pos.calls[0]["ticket"]


def test_venta_rechazada(client, fake_pos):
    fake_pos.behavior = lambda **kw: {"status": "failed", "response": {"response_code": "5"}}
    body = client.post("/pago", json=VENTA, headers=AUTH).get_json()
    assert body["estado"] == "RECHAZADO"
    assert tx_store.obtener(body["transaction_id"])["estado"] == "RECHAZADO"


def test_indeterminada_bloquea_caja_hasta_resolver(client, fake_pos):
    fake_pos.behavior = lambda **kw: {"status": "indeterminada", "ticket": kw["ticket"]}
    body = client.post("/pago", json=VENTA, headers=AUTH).get_json()
    tx_id = body["transaction_id"]
    assert body["estado"] == "INDETERMINADA"
    assert tx_store.obtener(tx_id)["estado"] == "INDETERMINADA"

    fake_pos.behavior = lambda **kw: {"status": "success"}
    r = client.post("/pago", json=VENTA, headers=AUTH)
    assert r.status_code == 409
    assert r.get_json()["pending_transaction_id"] == tx_id
    assert len(fake_pos.calls) == 1  # no se le pidió nada al POS

    r = client.post(f"/pago/resolver/{tx_id}", json={"estado": "RECHAZADO", "resuelto_por": "test"}, headers=AUTH)
    assert r.status_code == 200
    assert client.post("/pago", json=VENTA, headers=AUTH).get_json()["estado"] == "APROBADO"


def test_resultado_tardio_reconcilia_y_desbloquea(client, fake_pos):
    def indeterminada_con_respuesta_tardia(**kw):
        cb = kw["on_late_result"]
        threading.Timer(0.2, cb, args=({"status": "success", "response": {"response_code": "0"}},)).start()
        return {"status": "indeterminada", "ticket": kw["ticket"]}

    fake_pos.behavior = indeterminada_con_respuesta_tardia
    tx_id = client.post("/pago", json=VENTA, headers=AUTH).get_json()["transaction_id"]

    assert _esperar(lambda: tx_store.obtener(tx_id)["estado"] == "APROBADO")
    tx = tx_store.obtener(tx_id)
    assert tx["resuelto_por"] == "respuesta tardía del POS"

    fake_pos.behavior = lambda **kw: {"status": "success"}
    assert client.post("/pago", json=VENTA, headers=AUTH).status_code == 200


def test_resultado_tardio_no_pisa_resolucion_manual(client, fake_pos, server):
    fake_pos.behavior = lambda **kw: {"status": "indeterminada", "ticket": kw["ticket"]}
    tx_id = client.post("/pago", json=VENTA, headers=AUTH).get_json()["transaction_id"]
    client.post(f"/pago/resolver/{tx_id}", json={"estado": "RECHAZADO", "resuelto_por": "cajero"}, headers=AUTH)

    server.aplicar_resultado_tardio(tx_id, {"status": "success"})
    tx = tx_store.obtener(tx_id)
    assert tx["estado"] == "RECHAZADO"
    assert tx["resuelto_por"] == "cajero"


def test_pago_iniciar_registra_y_consulta_estado(client, fake_pos):
    r = client.post("/pago/iniciar", json={"type": "transbank", "id_sucursal": "1",
                                           "nombre_caja": "CAJA_TEST", "amount": 900}, headers=AUTH)
    assert r.status_code == 202
    tx_id = r.get_json()["transaction_id"]
    assert tx_store.obtener(tx_id) is not None
    assert _esperar(lambda: client.get(f"/pago/estado/{tx_id}", headers=AUTH).get_json()["estado"] == "APROBADO")
    assert tx_store.obtener(tx_id)["estado"] == "APROBADO"


def test_pago_iniciar_bloqueado_por_pendiente(client):
    tx_store.registrar_intento("tx-viejo", "transbank", "1", "POS_TEST", "1", "CAJA_TEST", 1000)
    r = client.post("/pago/iniciar", json={"type": "transbank", "id_sucursal": "1",
                                           "nombre_caja": "CAJA_TEST", "amount": 900}, headers=AUTH)
    assert r.status_code == 409
    assert r.get_json()["pending_transaction_id"] == "tx-viejo"


def test_validaciones(client):
    assert client.post("/pago", json={**VENTA, "terminal_id": "OTRO"}, headers=AUTH).status_code == 403
    assert client.post("/pago", json={**VENTA, "id_sucursal": "9"}, headers=AUTH).status_code == 403
    sin_monto = {k: v for k, v in VENTA.items() if k != "amount"}
    assert client.post("/pago", json=sin_monto, headers=AUTH).status_code == 400


def test_transbank_y_getnet_tienen_terminal_propio(client, monkeypatch):
    import server.api_server as api
    monkeypatch.setattr(api, "TRANSBANK_TERMINAL_ID", "TBK_01")
    monkeypatch.setattr(api, "GETNET_TERMINAL_ID", "GET_01")
    # El terminal de Getnet no sirve para cobrar en Transbank
    assert client.post("/pago", json={**VENTA, "terminal_id": "GET_01"}, headers=AUTH).status_code == 403
    assert client.post("/pago", json={**VENTA, "terminal_id": "TBK_01"}, headers=AUTH).status_code == 200


# ── Cancelar (error 2) ────────────────────────────────────────

def test_cancelar_en_la_cola_no_envia_la_venta_al_pos(server, fake_pos):
    server._stop_local_worker.set()
    server.local_worker_thread.join(3)  # la venta queda en la cola
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", server.app.test_client().post("/pago", json=VENTA, headers=AUTH)),
                         daemon=True)
    t.start()
    assert _esperar(lambda: len(server.tasks) == 1)
    tx_id = next(iter(server.tasks))

    r = server.app.test_client().post(f"/pago/cancelar/{tx_id}", headers=AUTH)
    assert r.status_code == 200 and r.get_json()["estado"] == "CANCELADO"
    t.join(3)
    assert out["r"].get_json()["estado"] == "CANCELADO"
    assert tx_store.obtener(tx_id)["estado"] == "RECHAZADO"

    server.start_local_worker()
    time.sleep(1.5)
    assert fake_pos.calls == []  # nunca se envió al POS


def test_cancelar_con_la_venta_en_el_pos_no_dice_cancelado(server, fake_pos):
    """El SDK de Transbank no puede cancelar: responder CANCELADO y liberar la
    caja dejaba al cliente pagando mientras el sistema decía que no hubo venta."""
    liberar = threading.Event()

    def venta_lenta(**kw):
        liberar.wait(3)
        return {"status": "success", "response": {"response_code": "0"}}
    fake_pos.behavior = venta_lenta
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", server.app.test_client().post("/pago", json=VENTA, headers=AUTH)),
                         daemon=True)
    t.start()
    assert _esperar(lambda: len(fake_pos.calls) == 1)
    tx_id = next(iter(server.tasks))

    r = server.app.test_client().post(f"/pago/cancelar/{tx_id}", headers=AUTH)
    assert r.status_code == 409
    assert "no se puede cancelar" in r.get_json()["message"]
    # La caja sigue ocupada mientras el POS procesa
    assert server.app.test_client().post("/pago", json=VENTA, headers=AUTH).status_code in (409, 429)

    liberar.set()
    t.join(3)
    assert out["r"].get_json()["estado"] == "APROBADO"


# ── Detalle de ventas (error 5) ───────────────────────────────

def _trama_detalle(ticket, autorizacion, monto):
    campos = ["0261", "00", "597012345678", "12345678", ticket, autorizacion, str(monto), "1234", "10", "DB",
              "16102026", "", "MC", "16102026", "152300", "1", "0", "0", "0"]
    return ("\x02" + "|".join(campos) + "\x03").encode() + b"x"


class FakeSDKDetalle:
    """Puerto serie de un POS Transbank que responde al 0260: una venta por
    mensaje y un mensaje final sin código de autorización."""
    comandos = []

    def open_port(self, port):
        return True

    def close_port(self):
        return True

    def _send_command(self, command, intermediate_messages=False, sales_detail=False, print_on_pos=False, callback=None):
        FakeSDKDetalle.comandos.append(command)
        return [_trama_detalle("152300", "AUTH01", 1500), _trama_detalle("153010", "AUTH02", 2000),
                _trama_detalle("", " ", 0)]


def test_detalle_de_ventas_transbank(monkeypatch):
    import pos.pos_module as pm
    monkeypatch.setattr(pm, "POSIntegrado", FakeSDKDetalle)
    monkeypatch.setattr(pm, "TRANSBANK_AVAILABLE", True)
    FakeSDKDetalle.comandos = []
    mod = POSModule("COM_TEST")
    mod.current_port = "COM_TEST"
    for imprimir in (False, True):
        res = mod.do_details_with_timeout(print_on_pos=imprimir, timeout=5)
        assert res["status"] == "success", res
        assert res["cantidad"] == 2
        assert [v["authorization_code"] for v in res["response"]] == ["AUTH01", "AUTH02"]
        assert res["response"][0]["amount"] == "1500"
    assert FakeSDKDetalle.comandos == ["0260|0|", "0260|1|"]


# ── Corte de cable y reconexión ───────────────────────────────

import functools  # noqa: E402

from transbank.error.transbank_exception import TransbankException  # noqa: E402


class FakeSDK:
    """POSIntegrado simulado: `venta` decide qué pasa al enviar la venta."""
    venta = staticmethod(lambda amount, ticket: {"response_code": "00", "ticket": ticket, "amount": str(amount)})
    ultima = {}
    ventas_enviadas = []

    def open_port(self, port):
        return True

    def close_port(self):
        return True

    def sale(self, amount, ticket):
        FakeSDK.ventas_enviadas.append(ticket)
        return FakeSDK.venta(amount, ticket)

    def last_sale(self):
        return FakeSDK.ultima


@pytest.fixture
def sdk(monkeypatch):
    import pos.pos_module as pm
    monkeypatch.setattr(pm, "POSIntegrado", FakeSDK)
    monkeypatch.setattr(pm, "TRANSBANK_AVAILABLE", True)
    FakeSDK.ventas_enviadas = []
    FakeSDK.venta = staticmethod(lambda amount, ticket: {"response_code": "00", "ticket": ticket})
    mod = POSModule("COM_TEST")
    mod.current_port = "COM_TEST"
    return mod


def _cable_cortado(amount, ticket):
    try:
        raise OSError("ClearCommError failed (dispositivo desconectado)")
    except OSError as e:
        raise TransbankException("Unable to send Sale command") from e


def test_corte_de_cable_durante_la_venta_queda_indeterminada(sdk):
    """Antes quedaba ERROR: la caja se liberaba aunque el cliente hubiera pagado."""
    FakeSDK.venta = staticmethod(_cable_cortado)
    res = sdk.open_port_and_sale("COM_TEST", 1500, ticket="120000")
    assert res["status"] == "indeterminada"
    assert res["ticket"] == "120000"


def test_nack_del_pos_es_error_seguro(sdk):
    def nack(amount, ticket):
        try:
            raise TransbankException("NACK received, check the message sent to the POS")
        except TransbankException as e:
            raise TransbankException("Unable to send Sale command") from e
    FakeSDK.venta = staticmethod(nack)
    assert sdk.open_port_and_sale("COM_TEST", 1500, ticket="120000")["status"] == "error"


def test_monto_bajo_el_minimo_no_se_envia(sdk):
    res = sdk.open_port_and_sale("COM_TEST", 30, ticket="120000")
    assert res["status"] == "error"
    assert FakeSDK.ventas_enviadas == []


def _ultima(ticket="120000", amount="1500", code="00", fecha=None):
    return {"ticket": ticket, "amount": amount, "response_code": code, "authorization_code": "AUTH99",
            "real_date": fecha or time.strftime("%d%m%Y")}


def test_ultima_venta_es_la_nuestra_aprobada(sdk):
    FakeSDK.ultima = _ultima()
    res = sdk.consultar_ultima_venta("120000", amount=1500, sent_at=time.time() - 30)
    assert res["status"] == "aprobado"


def test_ultima_venta_es_la_nuestra_rechazada(sdk):
    FakeSDK.ultima = _ultima(code="05")
    assert sdk.consultar_ultima_venta("120000", amount=1500, sent_at=time.time() - 30)["status"] == "rechazado"


def test_ultima_venta_es_otra(sdk):
    FakeSDK.ultima = _ultima(ticket="110000")
    # Recién enviada: la nuestra podría seguir en curso en la máquina
    assert sdk.consultar_ultima_venta("120000", amount=1500, sent_at=time.time() - 30)["status"] == "no_resuelto"
    # Pasada la ventana: el POS nunca la registró, no se cobró
    import pos.pos_module as pm
    viejo = time.time() - pm.RECON_VENTANA_SEGUNDOS - 10
    assert sdk.consultar_ultima_venta("120000", amount=1500, sent_at=viejo)["status"] == "rechazado"


def test_ultima_venta_mismo_ticket_otro_monto_u_otro_dia_no_es_la_nuestra(sdk):
    FakeSDK.ultima = _ultima(amount="9990")
    assert sdk.consultar_ultima_venta("120000", amount=1500, sent_at=time.time() - 30)["status"] == "no_resuelto"
    FakeSDK.ultima = _ultima(fecha="01012020")
    assert sdk.consultar_ultima_venta("120000", amount=1500, sent_at=time.time() - 30)["status"] == "no_resuelto"


def test_no_consulta_mientras_la_venta_tiene_el_puerto(sdk):
    sdk._port_lock.acquire()
    try:
        res = sdk.consultar_ultima_venta("120000", amount=1500, sent_at=time.time())
        assert res["status"] == "no_resuelto"
        assert "puerto en uso" in res["message"]
    finally:
        sdk._port_lock.release()


@pytest.fixture
def espera_rapida(server):
    server.esperar_resolucion_transbank = functools.partial(
        server.esperar_resolucion_transbank, intervalo=0.02, espera_tras_intento=0.02, reescaneo=0.05, max_espera=5)
    return server


def _pago_bg(server, body=VENTA):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", server.app.test_client().post("/pago", json=body, headers=AUTH)),
                         daemon=True)
    t.start()
    return t, out


def test_peticion_viva_hasta_que_vuelve_el_cable(espera_rapida, fake_pos):
    """Se corta el cable, el cliente paga en la máquina y al reconectar la misma
    petición /pago termina con el resultado real."""
    server = espera_rapida
    fake_pos.behavior = lambda **kw: {"status": "indeterminada", "ticket": kw["ticket"]}
    fake_pos.presente = False
    t, out = _pago_bg(server)
    assert _esperar(lambda: any(e.get("estado") == "ESPERANDO_POS" for e in server.tasks.values()))
    tx_id = next(iter(server.tasks))
    assert tx_store.obtener(tx_id)["estado"] == "INDETERMINADA"
    time.sleep(0.3)
    assert t.is_alive()  # la petición sigue abierta con el cable desconectado

    fake_pos.ultima_venta = {"status": "aprobado", "response": {"response_code": "00"}, "message": "ok"}
    fake_pos.presente = True  # se reconecta el cable
    t.join(3)
    assert out["r"].get_json()["estado"] == "APROBADO"
    tx = tx_store.obtener(tx_id)
    assert tx["estado"] == "APROBADO" and tx["resuelto_por"] == "POS tras reconexión"
    assert fake_pos.consultas[0]["ticket"] == tx["ticket"]


def test_cancelar_mientras_espera_el_cable(espera_rapida, fake_pos):
    server = espera_rapida
    fake_pos.behavior = lambda **kw: {"status": "indeterminada", "ticket": kw["ticket"]}
    t, out = _pago_bg(server)
    assert _esperar(lambda: any(e.get("estado") == "ESPERANDO_POS" for e in server.tasks.values()))
    tx_id = next(iter(server.tasks))

    r = server.app.test_client().post(f"/pago/cancelar/{tx_id}", headers=AUTH)
    assert r.status_code == 200 and r.get_json()["estado"] == "INDETERMINADA"
    t.join(3)
    assert out["r"].get_json()["estado"] == "INDETERMINADA"
    # La caja sigue bloqueada: el cliente pudo haber pagado
    assert server.app.test_client().post("/pago", json=VENTA, headers=AUTH).status_code == 409


def test_reconciliar_transbank_desde_el_panel(client, fake_pos):
    fake_pos.behavior = lambda **kw: {"status": "indeterminada", "ticket": kw["ticket"]}
    tx_id = client.post("/pago", json=VENTA, headers=AUTH).get_json()["transaction_id"]
    assert tx_store.obtener(tx_id)["estado"] == "INDETERMINADA"

    fake_pos.ultima_venta = {"status": "rechazado", "message": "El POS no registró esta venta; no se cobró."}
    r = client.post(f"/panel/pendientes/{tx_id}/reconciliar").get_json()
    assert r["estado"] == "RECHAZADO"
    assert client.post("/pago", json=VENTA, headers=AUTH).status_code == 200  # caja libre


def test_resultado_tardio_sin_confirmacion_no_resuelve(client, fake_pos, server):
    fake_pos.behavior = lambda **kw: {"status": "indeterminada", "ticket": kw["ticket"]}
    tx_id = client.post("/pago", json=VENTA, headers=AUTH).get_json()["transaction_id"]
    server.aplicar_resultado_tardio(tx_id, {"status": "indeterminada", "message": "se cortó el cable"})
    tx = tx_store.obtener(tx_id)
    assert tx["estado"] == "INDETERMINADA" and tx["resuelto_por"] is None
    assert client.post("/pago", json=VENTA, headers=AUTH).status_code == 409


# ── Detección de puerto compartida con Getnet ─────────────────

class FakeSDKSondeo:
    """POSIntegrado que solo responde el poll en COM2; registra qué puertos se abrieron."""
    abiertos = []

    def open_port(self, port):
        FakeSDKSondeo.abiertos.append(port)
        self.port = port
        return True

    def poll(self):
        return self.port == "COM2"

    def close_port(self):
        pass


@pytest.fixture
def sondeo(monkeypatch):
    import pos.pos_module as pm
    monkeypatch.setattr(pm, "POSIntegrado", FakeSDKSondeo)
    monkeypatch.setattr(pm, "USAR_POS_FISICO", True)
    FakeSDKSondeo.abiertos = []
    mod = POSModule("COM1,COM2")
    monkeypatch.setattr(mod, "list_ports", lambda: ["COM1", "COM2", "COM3"])
    return mod


def test_deteccion_transbank_nunca_sondea_el_puerto_de_getnet(sondeo):
    sondeo.puertos_ajenos = lambda: ["COM1"]
    assert sondeo.detect_port() == "COM2"
    assert FakeSDKSondeo.abiertos == ["COM2"]


def test_deteccion_espera_a_la_otra_y_excluye_lo_que_tomo(sondeo):
    from core.puertos_com import SONDEO_LOCK
    getnet = {"port": None}
    sondeo.puertos_ajenos = lambda: [getnet["port"]]
    resultado = []
    with SONDEO_LOCK:  # Getnet detectando
        t = threading.Thread(target=lambda: resultado.append(sondeo.detect_port()))
        t.start()
        time.sleep(0.1)
        assert FakeSDKSondeo.abiertos == []  # no sondea en paralelo
        getnet["port"] = "COM1"  # Getnet quedó en COM1
    t.join(2)
    assert resultado == ["COM2"]
    assert "COM1" not in FakeSDKSondeo.abiertos


class _Maquina:
    def __init__(self, port, ultimo):
        self.port, self._ultimo_puerto = port, ultimo

    def get_current_port(self):
        return self.port


def test_vincular_reserva_el_ultimo_puerto_si_hay_venta_sin_confirmar():
    from core.puertos_com import vincular
    pos, getnet = _Maquina("COM2", "COM2"), _Maquina(None, "COM1")  # cable Getnet cortado
    vincular(pos, getnet)
    assert pos.puertos_ajenos() == [None]  # sin venta pendiente, COM1 queda libre
    tx_store.registrar_intento("gn_pend", "getnet", "1", "POS_TEST", "1", "CAJA_TEST", 1000)
    try:
        assert pos.puertos_ajenos() == [None, "COM1"]
        assert getnet.puertos_ajenos() == ["COM2"]
    finally:
        tx_store.actualizar_estado("gn_pend", "RECHAZADO")


def test_puerto_presente_false_si_el_ultimo_puerto_ahora_es_de_getnet(sondeo):
    sondeo._ultimo_puerto = "COM1"
    sondeo.puertos_ajenos = lambda: ["COM1"]
    assert sondeo.puerto_presente() is False

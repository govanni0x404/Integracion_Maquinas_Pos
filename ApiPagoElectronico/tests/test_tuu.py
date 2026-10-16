"""
Pruebas del flujo TUU Pago remoto (/online y /pago con type=tuu) contra una API
simulada. Las respuestas imitan las observadas con la máquina real: mientras el
cobro no se completa, amount viene en 0 y sequenceNumber en null.
"""
import logging
import threading
import time
import types

import pytest

from conftest import AUTH
from config.settings import APP_NAME
from core import transaction_store as tx_store
import server.api_server as api
from pos import tuu_module as tuu

API_KEY = "TEST-TUU-KEY-SECRETA"
KEY_REQUEST = "KEY-DE-LA-MAQUINA-EN-REQUEST"
PAGO = {"type": "tuu", "id_sucursal": "1", "nombre_caja": "CAJA_TEST", "terminal_id": "SERIE_TEST",
        "amount": 1500, "timeout": 30}


def respuesta(status, amount=0, seq=None):
    """Body de GetPaymentRequest."""
    return {"idempotencyKey": "x", "status": status, "sequenceNumber": seq, "amount": amount,
            "dteType": 48, "device": "SERIE_TEST", "paymentMethod": 2 if seq else 0,
            "tipAmount": -1, "cashbackAmount": -1, "installments": 0, "extraData": None,
            "transactionReference": None, "acquirerId": None, "bin": "411111" if seq else None,
            "bank": None, "country": "CHL" if seq else None, "isWallet": False}


PENDING = respuesta("Pending")
COMPLETED = respuesta("Completed", 1500, "120001405935")


class FakeTUU:
    """
    estados: respuestas sucesivas del GET (la última se repite). Cada una es un dict
    (HTTP 200), 404, ("HTTP", codigo) o una excepción de red.
    post: respuestas sucesivas al crear (201, (codigo, body) o excepción; la última se repite).
    """

    def __init__(self, estados, post=None):
        self.estados = list(estados)
        self.post = list(post) if post is not None else [201]
        self.requests = []

    def __call__(self, method, url, headers=None, payload=None, timeout=15):
        self.requests.append({"method": method, "url": url, "headers": headers, "payload": payload})
        if method == "POST":
            resp = self.post.pop(0) if len(self.post) > 1 else self.post[0]
            if isinstance(resp, Exception):
                raise resp
            if resp == 201:
                return 201, {"idempotencyKey": payload["idempotencyKey"], "status": "Pending",
                             "amount": payload["amount"], "deviceId": payload["device"]}
            return resp
        estado = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
        if isinstance(estado, Exception):
            raise estado
        if estado == 404:
            return 404, {"code": "MR-404", "message": "not found"}
        if isinstance(estado, tuple):
            return estado[1], {"message": "error"}
        return 200, estado

    def posts(self):
        return [r for r in self.requests if r["method"] == "POST"]

    def gets(self):
        return [r for r in self.requests if r["method"] == "GET"]


@pytest.fixture
def reloj_rapido(monkeypatch):
    """Evita esperas reales: sleep no bloquea y el reloj avanza 5 s por consulta."""
    t = {"now": time.time()}

    def fake_time():
        t["now"] += 5
        return t["now"]

    monkeypatch.setattr(tuu, "time", types.SimpleNamespace(time=fake_time, sleep=lambda s: None))


def _tuu(monkeypatch, fake):
    monkeypatch.setattr(tuu, "_http_json", fake)
    return fake


# ── /online ──────────────────────────────────────────────────

def test_online_tuu_configurado(client, monkeypatch):
    fake = _tuu(monkeypatch, FakeTUU([PENDING]))
    r = client.get("/online?type=tuu", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()["online"] is True
    assert r.get_json()["dispositivos"] == ["SERIE_TEST"]
    assert fake.requests == []  # no gasta cuota de la API


def test_online_tuu_sin_api_key(client, monkeypatch):
    monkeypatch.setattr(api, "TUU_API_KEY", "")
    r = client.get("/online?type=tuu", headers=AUTH)
    assert r.status_code == 503
    assert "TUU_API_KEY" in r.get_json()["message"]


def test_online_tuu_con_key_por_request(client, monkeypatch):
    monkeypatch.setattr(api, "TUU_API_KEY", "")
    monkeypatch.setattr(api, "ALLOW_TUU_KEY_IN_REQUEST", True)
    assert client.get("/online?type=tuu", headers=AUTH).status_code == 200


# ── Mapeo del resultado ──────────────────────────────────────

def test_completed_con_monto_correcto_aprobado(client, monkeypatch, reloj_rapido):
    _tuu(monkeypatch, FakeTUU([PENDING, COMPLETED]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-ok"}, headers=AUTH).get_json()
    assert body["success"] is True
    assert body["estado"] == "APROBADO"
    assert body["data"]["sequence_number"] == "120001405935"
    tx = tx_store.obtener("venta-ok")
    assert tx["estado"] == "APROBADO"
    assert tx["tipo"] == "tuu"
    assert tx["nombre_caja"] == "tuu:SERIE_TEST"
    assert tx["ticket"] == tuu.clave_idempotencia("venta-ok")


def test_completed_con_monto_distinto_indeterminada(client, monkeypatch, reloj_rapido):
    _tuu(monkeypatch, FakeTUU([respuesta("Completed", 999, "120001405935")]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-monto"}, headers=AUTH).get_json()
    assert body["success"] is False
    assert body["estado"] == "INDETERMINADA"
    assert tx_store.obtener("venta-monto")["estado"] == "INDETERMINADA"


def test_failed_con_sequence_number_rechazado(client, monkeypatch, reloj_rapido):
    _tuu(monkeypatch, FakeTUU([respuesta("Failed", 1500, "120001405999")]))
    body = client.post("/pago", json=PAGO, headers=AUTH).get_json()
    assert body["success"] is False
    assert body["estado"] == "RECHAZADO"


def test_failed_sin_sequence_number_rechazado_y_libera_la_maquina(client, monkeypatch, reloj_rapido):
    # Ej. tarjeta mal leída: la máquina vuelve a "Esperando una misión" sin cobrar
    _tuu(monkeypatch, FakeTUU([respuesta("Failed")]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-amb"}, headers=AUTH).get_json()
    assert body["estado"] == "RECHAZADO"
    assert tx_store.obtener("venta-amb")["estado"] == "RECHAZADO"
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 200  # se puede reintentar


def test_canceled_rechazado(client, monkeypatch, reloj_rapido):
    _tuu(monkeypatch, FakeTUU([respuesta("Canceled")]))
    body = client.post("/pago", json=PAGO, headers=AUTH).get_json()
    assert body["estado"] == "RECHAZADO"
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 200  # no bloquea


def test_estados_no_finales_y_errores_de_consulta_siguen_esperando(client, monkeypatch, reloj_rapido):
    _tuu(monkeypatch, FakeTUU([respuesta("Sent"), ("HTTP", 429), TimeoutError("timed out"), ("HTTP", 401),
                               ("HTTP", 503), respuesta("Processing"), respuesta("Raro"), COMPLETED]))
    body = client.post("/pago", json={**PAGO, "timeout": 300}, headers=AUTH).get_json()
    assert body["estado"] == "APROBADO"


# ── Timeout y bloqueo de la máquina ──────────────────────────

def test_timeout_queda_indeterminada_y_bloquea_la_maquina(client, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([PENDING]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-to"}, headers=AUTH).get_json()
    assert body["success"] is False
    assert body["estado"] == "INDETERMINADA"
    assert body["message"].startswith(tuu.MENSAJE_INDETERMINADA)
    assert tx_store.obtener("venta-to")["estado"] == "INDETERMINADA"

    r = client.post("/pago", json={**PAGO, "tx_id": "venta-2"}, headers=AUTH)
    assert r.status_code == 409
    assert r.get_json()["pending_transaction_id"] == "venta-to"
    assert len(fake.posts()) == 1  # nunca se crea una segunda solicitud en la máquina


def test_monitor_resuelve_indeterminada_cuando_pasa_a_completed(server, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([PENDING]))
    server.app.test_client().post("/pago", json={**PAGO, "tx_id": "venta-mon"}, headers=AUTH)
    assert tx_store.obtener("venta-mon")["estado"] == "INDETERMINADA"

    fake.estados = [COMPLETED]
    server._stop_recon_monitor.set()
    server.start_reconciliation_monitor(interval=0.01)
    try:
        fin = time.time() + 3
        while tx_store.obtener("venta-mon")["estado"] == "INDETERMINADA" and time.time() < fin:
            time.sleep(0.01)
    finally:
        server._stop_recon_monitor.set()
    tx = tx_store.obtener("venta-mon")
    assert tx["estado"] == "APROBADO"
    assert tx["resuelto_por"] == "monitor automático (API TUU)"
    assert fake.gets()[-1]["url"].endswith("/RemotePayment/v2/GetPaymentRequest/" + tuu.clave_idempotencia("venta-mon"))


def test_reconciliar_sigue_pendiente_no_resuelve(server, monkeypatch, reloj_rapido):
    _tuu(monkeypatch, FakeTUU([PENDING]))
    client = server.app.test_client()
    client.post("/pago", json={**PAGO, "tx_id": "venta-rec"}, headers=AUTH)
    assert client.post("/panel/pendientes/venta-rec/reconciliar").get_json()["estado"] == "INDETERMINADA"
    tuu._http_json.estados = [respuesta("Canceled")]
    assert client.post("/panel/pendientes/venta-rec/reconciliar").get_json()["estado"] == "RECHAZADO"
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 200


def test_cancelar_solo_deja_de_esperar(server, monkeypatch):
    monkeypatch.setattr(tuu, "TUU_INTERVALO_CONSULTA", 0.01)
    _tuu(monkeypatch, FakeTUU([PENDING]))
    out = {}

    def pagar():
        out["resp"] = server.app.test_client().post("/pago", json={**PAGO, "tx_id": "venta-can"}, headers=AUTH)
    t = threading.Thread(target=pagar, daemon=True)
    t.start()
    fin = time.time() + 3
    while "venta-can" not in server.tasks and time.time() < fin:
        time.sleep(0.01)

    r = server.app.test_client().post("/pago/cancelar/venta-can", headers=AUTH)
    assert r.status_code == 202
    assert r.get_json()["estado"] == "INDETERMINADA"
    assert "máquina" in r.get_json()["message"]
    t.join(3)
    assert not t.is_alive()
    assert out["resp"].get_json()["estado"] == "INDETERMINADA"
    assert tx_store.obtener("venta-can")["estado"] == "INDETERMINADA"


# ── Creación ─────────────────────────────────────────────────

def test_error_de_red_al_crear_consulta_por_get_con_la_misma_clave(client, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED], post=[TimeoutError("timed out")]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-red"}, headers=AUTH).get_json()
    assert body["estado"] == "APROBADO"
    assert len(fake.posts()) == 1  # existía: no se reenvía
    clave = tuu.clave_idempotencia("venta-red")
    assert fake.gets()[0]["url"].endswith("/GetPaymentRequest/" + clave)


def test_error_de_red_y_tuu_no_la_conoce_reintenta_con_la_misma_clave(client, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([404, 404, COMPLETED], post=[TimeoutError("timed out"), 201]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-404"}, headers=AUTH).get_json()
    assert body["estado"] == "APROBADO"
    claves = {p["payload"]["idempotencyKey"] for p in fake.posts()}
    assert len(fake.posts()) == 2 and claves == {tuu.clave_idempotencia("venta-404")}


def test_creacion_sin_saber_si_llego_queda_indeterminada(client, monkeypatch, reloj_rapido):
    _tuu(monkeypatch, FakeTUU([("HTTP", 503)], post=[TimeoutError("timed out")]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-x"}, headers=AUTH).get_json()
    assert body["estado"] == "INDETERMINADA"
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 409


def test_mr191_al_crear_significa_que_ya_existe(client, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED], post=[(400, {"code": "MR-191", "message": "key in use"})]))
    assert client.post("/pago", json=PAGO, headers=AUTH).get_json()["estado"] == "APROBADO"
    assert len(fake.posts()) == 1


def test_429_al_crear_no_reintenta_ni_bloquea(client, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED], post=[(429, {"message": "Too Many Requests"})]))
    body = client.post("/pago", json=PAGO, headers=AUTH).get_json()
    assert body["estado"] == "RECHAZADO"
    assert "1 minuto" in body["message"]
    assert len(fake.posts()) == 1
    assert fake.gets() == []


def test_error_de_configuracion_al_crear(client, monkeypatch, reloj_rapido):
    _tuu(monkeypatch, FakeTUU([PENDING], post=[(400, {"code": "MR-100", "message": "device not found"})]))
    body = client.post("/pago", json=PAGO, headers=AUTH).get_json()
    assert body["estado"] == "RECHAZADO"
    assert body["error_code"] == "MR-100"
    assert "TUU_DEVICES" in body["message"]


# ── Clave de idempotencia y body ─────────────────────────────

def test_clave_de_idempotencia_determinista_y_max_36():
    for tx_id in ("a", "venta-123", "x" * 64, "6f1c9a8e-2b0d-4c1e-9f3a-7d5e8b2c1a40"):
        clave = tuu.clave_idempotencia(tx_id)
        assert len(clave) <= 36
        assert clave == tuu.clave_idempotencia(tx_id)
    assert tuu.clave_idempotencia("a") != tuu.clave_idempotencia("b")


def test_body_lleva_propina_y_vuelto_en_menos_uno(client, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED]))
    client.post("/pago", json={**PAGO, "tx_id": "venta-body"}, headers=AUTH)
    client.post("/pago", json={**PAGO, "payment_method": "credito"}, headers=AUTH)
    posts = fake.posts()
    for p in posts:
        assert p["payload"]["tipAmount"] == -1
        assert p["payload"]["cashbackAmount"] == -1
        assert p["payload"]["device"] == "SERIE_TEST"
        assert p["payload"]["amount"] == 1500
        assert p["headers"]["X-API-Key"] == API_KEY
        assert p["url"] == "https://tuu.test/RemotePayment/v2/Create"
    assert posts[0]["payload"]["paymentMethod"] == 2
    assert posts[0]["payload"]["idempotencyKey"] == tuu.clave_idempotencia("venta-body")
    assert posts[1]["payload"]["paymentMethod"] == 1


# ── Validaciones ─────────────────────────────────────────────

def test_validaciones(client, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED]))
    assert client.post("/pago", json={**PAGO, "amount": 99}, headers=AUTH).status_code == 400
    assert client.post("/pago", json={**PAGO, "amount": 100_000_000}, headers=AUTH).status_code == 400
    assert client.post("/pago", json={**PAGO, "payment_method": "cheque"}, headers=AUTH).status_code == 400
    assert client.post("/pago", json={**PAGO, "terminal_id": "OTRA"}, headers=AUTH).status_code == 400
    for campo in ("terminal_id", "amount", "timeout", "nombre_caja"):
        r = client.post("/pago", json={k: v for k, v in PAGO.items() if k != campo}, headers=AUTH)
        assert r.status_code == 400, campo
    assert client.post("/pago", json={**PAGO, "timeout": "abc"}, headers=AUTH).status_code == 400
    assert client.post("/pago", json={**PAGO, "tx_id": "con espacios"}, headers=AUTH).status_code == 400
    assert fake.requests == []
    assert client.post("/pago", json={**PAGO, "tx_id": "venta-v"}, headers=AUTH).status_code == 200
    assert client.post("/pago", json={**PAGO, "tx_id": "venta-v"}, headers=AUTH).status_code == 409


def test_terminal_id_elige_la_maquina(client, monkeypatch, reloj_rapido):
    monkeypatch.setattr(api, "TUU_DISPOSITIVOS", ["SERIE_A", "SERIE_B"])
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED]))
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 400  # SERIE_TEST no está en la lista
    body = client.post("/pago", json={**PAGO, "terminal_id": "SERIE_B"}, headers=AUTH).get_json()
    assert body["success"] is True
    assert fake.posts()[0]["payload"]["device"] == "SERIE_B"
    assert tx_store.obtener(body["transaction_id"])["nombre_caja"] == "tuu:SERIE_B"


def test_sin_tuu_devices_acepta_cualquier_terminal_id(client, monkeypatch, reloj_rapido):
    monkeypatch.setattr(api, "TUU_DISPOSITIVOS", [])
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED]))
    assert client.post("/pago", json={**PAGO, "terminal_id": "SERIE_NUEVA"}, headers=AUTH).get_json()["success"] is True
    assert fake.posts()[0]["payload"]["device"] == "SERIE_NUEVA"


# ── API key por request (access_token), como Mercado Pago ────

def test_key_del_request_ignorada_por_defecto(client, monkeypatch, reloj_rapido):
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED]))
    client.post("/pago", json={**PAGO, "access_token": KEY_REQUEST}, headers=AUTH)
    assert fake.posts()[0]["headers"]["X-API-Key"] == API_KEY


def test_key_del_request_se_usa_si_esta_permitido(client, monkeypatch, reloj_rapido):
    monkeypatch.setattr(api, "ALLOW_TUU_KEY_IN_REQUEST", True)
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED]))
    client.post("/pago", json={**PAGO, "access_token": KEY_REQUEST}, headers=AUTH)
    client.post("/pago", json=PAGO, headers=AUTH)  # sin access_token: TUU_API_KEY
    assert [p["headers"]["X-API-Key"] for p in fake.posts()] == [KEY_REQUEST, API_KEY]
    assert all(g["headers"]["X-API-Key"] == KEY_REQUEST for g in fake.gets()[:1])


def test_sin_ninguna_key_400(client, monkeypatch):
    monkeypatch.setattr(api, "TUU_API_KEY", "")
    monkeypatch.setattr(api, "ALLOW_TUU_KEY_IN_REQUEST", True)
    fake = _tuu(monkeypatch, FakeTUU([COMPLETED]))
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 400
    assert fake.requests == []


def test_monitor_usa_la_key_del_request(server, monkeypatch, reloj_rapido):
    monkeypatch.setattr(api, "TUU_API_KEY", "")
    monkeypatch.setattr(api, "ALLOW_TUU_KEY_IN_REQUEST", True)
    fake = _tuu(monkeypatch, FakeTUU([PENDING]))
    client = server.app.test_client()
    client.post("/pago", json={**PAGO, "tx_id": "venta-kr", "access_token": KEY_REQUEST}, headers=AUTH)
    assert tx_store.obtener("venta-kr")["estado"] == "INDETERMINADA"
    assert KEY_REQUEST not in (tx_store.obtener("venta-kr")["raw_response"] or "")

    fake.estados = [COMPLETED]
    tx = tx_store.obtener("venta-kr")
    assert server.aplicar_reconciliacion(tx, server.reconciliar(tx), resuelto_por="monitor") == "APROBADO"
    assert fake.gets()[-1]["headers"]["X-API-Key"] == KEY_REQUEST
    assert "venta-kr" not in server._tuu_keys  # resuelta: la key se descarta


def test_tuu_deshabilitado(client, monkeypatch):
    monkeypatch.setattr(api, "USAR_TUU", False)
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 503


# ── La API key nunca sale ────────────────────────────────────

def test_api_key_nunca_aparece_en_logs_ni_respuestas(client, monkeypatch, reloj_rapido):
    registros = []

    class Captura(logging.Handler):
        def emit(self, record):
            registros.append(self.format(record))

    h = Captura()
    loggers = [logging.getLogger(APP_NAME), logging.getLogger(), tuu.tuu_log]
    for lg in loggers:
        lg.addHandler(h)
    try:
        cuerpos = []
        for fake in (
            FakeTUU([COMPLETED]),
            FakeTUU([PENDING]),
            FakeTUU([("HTTP", 503)], post=[TimeoutError("timed out")]),
            FakeTUU([PENDING], post=[(401, {"code": "KEY-003", "message": "invalid key"})]),
        ):
            _tuu(monkeypatch, fake)
            r = client.post("/pago", json=PAGO, headers=AUTH)
            cuerpos.append(r.get_data(as_text=True))
            monkeypatch.setattr(api, "ALLOW_TUU_KEY_IN_REQUEST", True)
            r = client.post("/pago", json={**PAGO, "access_token": KEY_REQUEST}, headers=AUTH)
            monkeypatch.setattr(api, "ALLOW_TUU_KEY_IN_REQUEST", False)
            cuerpos.append(r.get_data(as_text=True))
            for tx in tx_store.listar_no_resueltas():
                tx_store.actualizar_estado(tx["tx_id"], "RECHAZADO")
        cuerpos.append(client.get("/panel").get_data(as_text=True))
        cuerpos.append(client.get("/panel/log?tipo=tuu").get_data(as_text=True))
    finally:
        for lg in loggers:
            lg.removeHandler(h)

    assert any("Cobro iniciado" in m for m in registros)
    for secreto in (API_KEY, KEY_REQUEST):
        assert all(secreto not in m for m in registros)
        assert all(secreto not in c for c in cuerpos)

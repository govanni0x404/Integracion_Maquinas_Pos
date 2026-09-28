"""
Pruebas del flujo Mercado Pago Point (/online y /pago con type=mercadopago)
contra una API de Orders simulada. Las respuestas imitan las reales vistas en
producción (status "processed" + status_detail "accredited" = cobro exitoso).
"""
import threading
import time
import types
import urllib.error

import pytest

from conftest import AUTH
from core import transaction_store as tx_store
import server.api_server as api

PAGO = {"type": "mercadopago", "id_sucursal": "1", "nombre_caja": "CAJA_TEST", "amount": 350, "timeout": 30}


class FakeMP:
    """
    estados: respuestas sucesivas del GET de la orden (la última se repite). Cada una es
    (status, status_detail), ("HTTP", codigo) para un error HTTP, o una excepción de red.
    post: respuestas sucesivas al crear la orden (código HTTP o excepción; la última se repite).
    cancel_status: código HTTP con el que responde POST /{id}/cancel.
    """

    def __init__(self, estados, post_status=201, post=None, cancel_status=200):
        self.estados = list(estados)
        self.post = list(post) if post is not None else [post_status]
        self.cancel_status = cancel_status
        self.requests = []

    def __call__(self, method, url, headers=None, payload=None, timeout=15):
        self.requests.append({"method": method, "url": url, "headers": headers, "payload": payload})
        if method == "POST" and url.endswith("/cancel"):
            if self.cancel_status != 200:
                return self.cancel_status, {"errors": [{"code": "cannot_cancel_order"}]}
            return 200, {"id": "ORD_TEST", "status": "canceled", "status_detail": "canceled"}
        if method == "POST":
            resp = self.post.pop(0) if len(self.post) > 1 else self.post[0]
            if isinstance(resp, Exception):
                raise resp
            if resp != 201:
                return resp, {"errors": [{"details": ["terminal inválido"]}]}
            return 201, {"id": "ORD_TEST", "status": "created"}
        estado = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
        if isinstance(estado, Exception):
            raise estado
        if estado[0] == "HTTP":
            return estado[1], {"message": "internal error", "status": estado[1]}
        status, detail = estado
        return 200, {"id": "ORD_TEST", "status": status, "status_detail": detail}

    def llamadas(self, sufijo):
        return [r for r in self.requests if r["url"].endswith(sufijo)]


@pytest.fixture
def reloj_rapido(monkeypatch):
    """Evita esperas reales: sleep no bloquea y el reloj avanza 5 s por consulta."""
    t = {"now": time.time()}

    def fake_time():
        t["now"] += 5
        return t["now"]

    monkeypatch.setattr(api, "time", types.SimpleNamespace(time=fake_time, sleep=lambda s: None,
                                                           strftime=time.strftime))


def _mp(monkeypatch, fake):
    monkeypatch.setattr(api, "_http_json", fake)
    return fake


def test_online_mercadopago_con_token(client):
    r = client.get("/online?type=mercadopago", headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()["online"] is True


def test_online_mercadopago_sin_token(client, monkeypatch):
    monkeypatch.setattr(api, "MP_ACCESS_TOKEN", "")
    r = client.get("/online?type=mercadopago", headers=AUTH)
    assert r.status_code == 503
    assert r.get_json()["online"] is False


def test_pago_aprobado_processed_accredited(client, monkeypatch, reloj_rapido):
    fake = _mp(monkeypatch, FakeMP([("at_terminal", None), ("processed", "accredited")]))
    r = client.post("/pago", json={**PAGO, "tx_id": "venta-123"}, headers=AUTH)
    body = r.get_json()
    assert r.status_code == 200
    assert body["success"] is True
    assert body["status"] == "approved"
    assert body["mp_status"] == "processed"
    assert body["data"]["status_detail"] == "accredited"
    assert body["data"]["order_id"] == "ORD_TEST"

    post = fake.requests[0]
    assert post["method"] == "POST"
    assert post["headers"]["Authorization"] == "Bearer TEST-ENV-TOKEN"
    assert post["headers"]["X-Idempotency-Key"] == "mp_venta-123"
    assert post["payload"]["external_reference"] == "venta-123"
    assert post["payload"]["transactions"]["payments"][0]["amount"] == "350"
    assert post["payload"]["config"]["point"]["terminal_id"] == "NEWLAND_N950__TEST"
    assert fake.requests[1]["url"].endswith("/ORD_TEST")


def test_pago_approved_legacy(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([("approved", "accredited")]))
    assert client.post("/pago", json=PAGO, headers=AUTH).get_json()["success"] is True


def test_pago_cancelado(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([("canceled", "canceled_by_user")]))
    body = client.post("/pago", json=PAGO, headers=AUTH).get_json()
    assert body["success"] is False
    assert body["status"] == "canceled"


def test_pago_processed_no_acreditado_no_es_exito(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([("processed", "rejected")]))
    assert client.post("/pago", json=PAGO, headers=AUTH).get_json()["success"] is False


def test_pago_error_creando_orden(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([], post_status=400))
    body = client.post("/pago", json=PAGO, headers=AUTH).get_json()
    assert body["success"] is False
    assert body["status"] == "failed"
    assert body["http_status"] == 400



def test_token_del_request_ignorado_por_defecto(client, monkeypatch, reloj_rapido):
    fake = _mp(monkeypatch, FakeMP([("processed", "accredited")]))
    client.post("/pago", json={**PAGO, "access_token": "TOKEN-DEL-NAVEGADOR"}, headers=AUTH)
    assert fake.requests[0]["headers"]["Authorization"] == "Bearer TEST-ENV-TOKEN"


def test_pago_sin_monto_400(client, monkeypatch):
    fake = _mp(monkeypatch, FakeMP([("processed", "accredited")]))
    r = client.post("/pago", json={k: v for k, v in PAGO.items() if k != "amount"}, headers=AUTH)
    assert r.status_code == 400
    assert fake.requests == []


def test_pago_sin_token_500(client, monkeypatch):
    monkeypatch.setattr(api, "MP_ACCESS_TOKEN", "")
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 500


# ── Consultas con error: nunca se toman como rechazo ─────────

def test_error_http_en_la_consulta_no_es_rechazo(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([("HTTP", 500), ("HTTP", 429), ("processed", "accredited")]))
    body = client.post("/pago", json=PAGO, headers=AUTH).get_json()
    assert body["success"] is True
    assert body["estado"] == "APROBADO"


def test_error_de_red_en_la_consulta_sigue_esperando(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([TimeoutError("timed out"), ("at_terminal", None), ("processed", "accredited")]))
    assert client.post("/pago", json=PAGO, headers=AUTH).get_json()["success"] is True


def test_estado_desconocido_no_es_final(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([("action_required", None), ("processed", "accredited")]))
    assert client.post("/pago", json=PAGO, headers=AUTH).get_json()["success"] is True


# ── Timeout: la orden no puede quedar abierta en el terminal ──

def test_timeout_cancela_la_orden_en_mp(client, monkeypatch, reloj_rapido):
    fake = _mp(monkeypatch, FakeMP([("at_terminal", None)]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-to"}, headers=AUTH).get_json()
    assert body["success"] is False
    assert body["status"] == "canceled"
    assert body["estado"] == "RECHAZADO"
    cancel = fake.llamadas("/ORD_TEST/cancel")
    assert len(cancel) == 1 and cancel[0]["method"] == "POST"
    assert cancel[0]["headers"]["X-Idempotency-Key"]
    assert tx_store.obtener("venta-to")["estado"] == "RECHAZADO"


def test_timeout_con_pago_en_curso_queda_indeterminada_y_se_reconcilia(client, monkeypatch, reloj_rapido):
    fake = _mp(monkeypatch, FakeMP([("at_terminal", None)], cancel_status=409))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-ind"}, headers=AUTH).get_json()
    assert body["success"] is False
    assert body["status"] == "indeterminada"
    assert body["estado"] == "INDETERMINADA"
    assert "/pago/estado/venta-ind" in body["message"]
    tx = tx_store.obtener("venta-ind")
    assert tx["estado"] == "INDETERMINADA"
    assert tx["ticket"] == "ORD_TEST"
    assert tx["nombre_caja"] == "mp:NEWLAND_N950__TEST"
    assert client.get("/pago/estado/venta-ind", headers=AUTH).get_json()["estado"] == "INDETERMINADA"

    # El terminal queda bloqueado: el cliente todavía puede pagar esa orden
    r = client.post("/pago", json=PAGO, headers=AUTH)
    assert r.status_code == 409
    assert r.get_json()["pending_transaction_id"] == "venta-ind"

    # Mientras la orden siga abierta, reconciliar no la resuelve
    assert client.post("/panel/pendientes/venta-ind/reconciliar").get_json()["estado"] == "INDETERMINADA"

    # El cliente terminó pagando: la reconciliación lo confirma y libera el terminal
    fake.estados = [("processed", "accredited")]
    assert client.post("/panel/pendientes/venta-ind/reconciliar").get_json()["estado"] == "APROBADO"
    assert tx_store.obtener("venta-ind")["estado"] == "APROBADO"
    assert client.get("/pago/estado/venta-ind", headers=AUTH).get_json()["estado"] == "APROBADO"
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 200


def test_monitor_reconcilia_orden_indeterminada(server, monkeypatch, reloj_rapido):
    fake = _mp(monkeypatch, FakeMP([("at_terminal", None)], cancel_status=409))
    server.app.test_client().post("/pago", json={**PAGO, "tx_id": "venta-mon"}, headers=AUTH)
    tx = tx_store.obtener("venta-mon")

    # Sigue abierta: el monitor no toca el registro
    assert server.aplicar_reconciliacion(tx, server.reconciliar(tx), resuelto_por="monitor") == "INDETERMINADA"
    assert tx_store.obtener("venta-mon")["resuelto_por"] is None

    fake.estados = [("expired", None)]
    assert server.aplicar_reconciliacion(tx, server.reconciliar(tx), resuelto_por="monitor") == "RECHAZADO"
    assert tx_store.obtener("venta-mon")["resuelto_por"] == "monitor"


# ── Creación de la orden ─────────────────────────────────────

def test_creacion_reintenta_con_la_misma_idempotency_key(client, monkeypatch, reloj_rapido):
    fake = _mp(monkeypatch, FakeMP([("processed", "accredited")], post=[TimeoutError("timed out"), 503, 201]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-ret"}, headers=AUTH).get_json()
    assert body["success"] is True
    posts = [r for r in fake.requests if r["method"] == "POST"]
    assert len(posts) == 3
    assert {p["headers"]["X-Idempotency-Key"] for p in posts} == {"mp_venta-ret"}


def test_creacion_sin_respuesta_queda_indeterminada(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([], post=[TimeoutError("timed out")]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-amb"}, headers=AUTH).get_json()
    assert body["estado"] == "INDETERMINADA"
    tx = tx_store.obtener("venta-amb")
    assert tx["estado"] == "INDETERMINADA" and tx["ticket"] is None
    # Sin id de orden no hay cómo consultarla: queda para el operador
    r = client.post("/panel/pendientes/venta-amb/reconciliar").get_json()
    assert r["estado"] == "INDETERMINADA"
    assert "portal de Mercado Pago" in r["detalle"]["message"]


def test_creacion_sin_conexion_es_error_seguro(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([("processed", "accredited")],
                            post=[urllib.error.URLError(ConnectionRefusedError("refused"))]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-dns"}, headers=AUTH).get_json()
    assert body["status"] == "error"
    assert body["estado"] == "ERROR"
    assert client.post("/pago", json=PAGO, headers=AUTH).status_code == 200  # no bloquea el terminal


# ── Persistencia, validaciones y cancelación ─────────────────

def test_venta_queda_registrada(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([("processed", "accredited")]))
    body = client.post("/pago", json={**PAGO, "tx_id": "venta-reg"}, headers=AUTH).get_json()
    assert body["transaction_id"] == "venta-reg"
    tx = tx_store.obtener("venta-reg")
    assert tx["tipo"] == "mercadopago"
    assert tx["estado"] == "APROBADO"
    assert tx["ticket"] == "ORD_TEST"
    assert tx["monto"] == 350
    assert tx["client_nombre_caja"] == "CAJA_TEST"


def test_tx_id_validado(client, monkeypatch, reloj_rapido):
    fake = _mp(monkeypatch, FakeMP([("processed", "accredited")]))
    assert client.post("/pago", json={**PAGO, "tx_id": "con espacios"}, headers=AUTH).status_code == 400
    assert client.post("/pago", json={**PAGO, "tx_id": "venta-x"}, headers=AUTH).status_code == 200
    assert client.post("/pago", json={**PAGO, "tx_id": "venta-x"}, headers=AUTH).status_code == 409
    assert len([r for r in fake.requests if r["method"] == "POST"]) == 1


def test_cancelar_desde_el_cliente_cancela_la_orden(server, monkeypatch):
    monkeypatch.setattr(api, "MP_INTERVALO_CONSULTA", 0.01)
    fake = _mp(monkeypatch, FakeMP([("at_terminal", None)]))
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
    assert r.get_json()["estado"] == "CANCELANDO"
    t.join(3)
    assert not t.is_alive()
    assert out["resp"].get_json()["estado"] == "RECHAZADO"
    assert len(fake.llamadas("/ORD_TEST/cancel")) == 1
    assert tx_store.obtener("venta-can")["estado"] == "RECHAZADO"


# ── Terminal de Mercado Pago independiente del TERMINAL_ID de Getnet ──

def test_terminal_mp_no_se_mezcla_con_el_de_getnet(client, monkeypatch, reloj_rapido):
    # El sistema web manda el TERMINAL_ID de Getnet: se usa igual el terminal MP configurado
    fake = _mp(monkeypatch, FakeMP([("processed", "accredited")]))
    client.post("/pago", json={**PAGO, "terminal_id": "POS_TEST"}, headers=AUTH)
    assert fake.requests[0]["payload"]["config"]["point"]["terminal_id"] == "NEWLAND_N950__TEST"


def test_varios_terminales_mp_se_elige_con_mp_terminal_id(client, monkeypatch, reloj_rapido):
    monkeypatch.setattr(api, "MP_TERMINALES", ["NEWLAND_A", "NEWLAND_B"])
    fake = _mp(monkeypatch, FakeMP([("processed", "accredited")]))
    r = client.post("/pago", json={**PAGO, "mp_terminal_id": "NEWLAND_B"}, headers=AUTH)
    assert r.get_json()["success"] is True
    assert fake.requests[0]["payload"]["config"]["point"]["terminal_id"] == "NEWLAND_B"

    r = client.post("/pago", json=PAGO, headers=AUTH)  # sin indicar cuál
    assert r.status_code == 400
    assert r.get_json()["terminales_configurados"] == ["NEWLAND_A", "NEWLAND_B"]
    assert client.post("/pago", json={**PAGO, "mp_terminal_id": "OTRO"}, headers=AUTH).status_code == 400


def test_dos_terminales_mp_cobran_a_la_vez(server, monkeypatch):
    """Un cobro abierto en un terminal no bloquea al otro."""
    monkeypatch.setattr(api, "MP_TERMINALES", ["NEWLAND_A", "NEWLAND_B"])
    monkeypatch.setattr(api, "MP_INTERVALO_CONSULTA", 0.01)
    _mp(monkeypatch, FakeMP([("at_terminal", None)]))
    t = threading.Thread(target=lambda: server.app.test_client().post(
        "/pago", json={**PAGO, "mp_terminal_id": "NEWLAND_A", "tx_id": "venta-a"}, headers=AUTH), daemon=True)
    t.start()
    fin = time.time() + 3
    while "venta-a" not in server.tasks and time.time() < fin:
        time.sleep(0.01)

    _mp(monkeypatch, FakeMP([("processed", "accredited")]))
    r = server.app.test_client().post("/pago", json={**PAGO, "mp_terminal_id": "NEWLAND_B"}, headers=AUTH)
    assert r.status_code == 200 and r.get_json()["success"] is True
    server.tasks["venta-a"]["cancel"].set()
    t.join(3)


def test_sin_terminal_mp_configurado_no_usa_el_de_getnet(client, monkeypatch):
    monkeypatch.setattr(api, "MP_TERMINALES", [])
    fake = _mp(monkeypatch, FakeMP([("processed", "accredited")]))
    r = client.post("/pago", json=PAGO, headers=AUTH)
    assert r.status_code == 400
    assert "MP_TERMINAL_ID" in r.get_json()["error"]
    assert fake.requests == []


def test_log_de_negocio_narra_el_cobro(client, monkeypatch, reloj_rapido):
    mensajes = []
    monkeypatch.setattr(api.mercadopago_log, "info", lambda m, *a, **k: mensajes.append(m))
    orden = {"id": "ORD_TEST", "status": "processed", "status_detail": "accredited",
             "transactions": {"payments": [{"id": "PAY01", "status_detail": "accredited",
                                            "payment_method": {"id": "master", "type": "credit_card",
                                                               "installments": 3}}]}}
    fake = FakeMP([("at_terminal", None)], post=[503, 201])
    estados = iter([(200, {"id": "ORD_TEST", "status": "at_terminal"}), (401, {"message": "unauthorized"}), (200, orden)])

    def http(method, url, headers=None, payload=None, timeout=15):
        if method == "GET":
            return next(estados)
        return fake(method, url, headers, payload, timeout)
    monkeypatch.setattr(api, "_http_json", http)

    client.post("/pago", json={**PAGO, "tx_id": "venta-log"}, headers=AUTH)
    texto = "\n".join(mensajes)
    assert "🟢 Cobro iniciado — tx venta-log — terminal NEWLAND_N950__TEST" in texto
    assert "intento 1/3" in texto and "HTTP 503" in texto
    assert "at_terminal" in texto
    assert "MP_ACCESS_TOKEN es inválido o está vencido" in texto
    assert "se recuperó la comunicación" in texto
    assert "✅ Cobro APROBADO — orden ORD_TEST" in texto
    assert "pago PAY01" in texto and "master / credit_card" in texto and "3 cuota(s)" in texto
    assert "📄 Respuesta /pago tx=venta-log (estado=APROBADO)" in texto

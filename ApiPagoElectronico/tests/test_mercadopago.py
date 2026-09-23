"""
Pruebas del flujo Mercado Pago Point (/online y /pago con type=mercadopago)
contra una API de Orders simulada. Las respuestas imitan las reales vistas en
producción (status "processed" + status_detail "accredited" = cobro exitoso).
"""
import time
import types

import pytest

from conftest import AUTH
import server.api_server as api

PAGO = {"type": "mercadopago", "id_sucursal": "1", "nombre_caja": "CAJA_TEST", "amount": 350, "timeout": 30}


class FakeMP:
    def __init__(self, estados, post_status=201):
        self.estados = list(estados)
        self.post_status = post_status
        self.requests = []

    def __call__(self, method, url, headers=None, payload=None, timeout=15):
        self.requests.append({"method": method, "url": url, "headers": headers, "payload": payload})
        if method == "POST":
            if self.post_status != 201:
                return self.post_status, {"errors": [{"details": ["terminal inválido"]}]}
            return 201, {"id": "ORD_TEST", "status": "created"}
        status, detail = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
        return 200, {"id": "ORD_TEST", "status": status, "status_detail": detail}


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


def test_pago_timeout(client, monkeypatch, reloj_rapido):
    _mp(monkeypatch, FakeMP([("at_terminal", None)]))
    body = client.post("/pago", json=PAGO, headers=AUTH).get_json()
    assert body["success"] is False
    assert body["status"] == "timeout"
    assert body["order_id"] == "ORD_TEST"


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

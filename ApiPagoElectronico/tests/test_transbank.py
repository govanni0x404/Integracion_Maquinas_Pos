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

import time

from conftest import AUTH

from core import transaction_store as tx_store

LAN = {"REMOTE_ADDR": "192.168.1.50"}
HOY = time.strftime("%Y-%m-%d")


def _ventas():
    tx_store.registrar_intento("tx-ok", "getnet", "111", "GN1", "1", "caja1", 1500)
    tx_store.actualizar_estado("tx-ok", "APROBADO",
                               raw_response={"status": "success",
                                             "response": {"AuthorizationCode": "A12345", "Last4Digits": "0042",
                                                          "OperationId": 77}})
    tx_store.registrar_intento("tx-no", "transbank", "222", "TB1", "1", "CAJA1", 2000)
    tx_store.actualizar_estado("tx-no", "RECHAZADO", nota="Cancelada por el cliente antes de enviarse al POS",
                               resuelto_por="cancelación del cliente")
    tx_store.registrar_intento("tx-pend", "tuu", "333", "SERIE1", "1", "CAJA2", 3000)


def test_ventas_pide_clave(client_sin_sesion):
    assert client_sin_sesion.get("/ventas").status_code == 401


def test_ventas_detalle_completo(client_sin_sesion):
    _ventas()
    r = client_sin_sesion.get("/ventas", headers=AUTH)
    assert r.status_code == 200
    j = r.get_json()
    assert j["total"] == 3 and j["devueltas"] == 3 and j["filtros"]["desde"] == HOY
    assert j["resumen"]["cantidad_aprobadas"] == 1 and j["resumen"]["monto_aprobado"] == 1500
    ventas = {v["transaction_id"]: v for v in j["ventas"]}
    ok = ventas["tx-ok"]
    assert ok["aprobada"] and ok["maquina"] == "Getnet" and ok["monto"] == 1500
    assert ok["codigo_autorizacion"] == "A12345" and ok["tarjeta_ultimos4"] == "0042"
    assert ok["comprobante_pos"] == 77 and ok["nombre_caja"] == "CAJA1"
    assert ok["fecha"] == HOY and ok["hora_envio"] and ok["hora_proceso"]
    assert ventas["tx-no"]["cancelada"] and not ventas["tx-no"]["aprobada"]
    assert ventas["tx-pend"]["sin_resolver"] and ventas["tx-pend"]["hora_proceso"] is None


def test_ventas_filtros(client_sin_sesion):
    _ventas()
    j = client_sin_sesion.get("/ventas?tipo=haulmer", headers=AUTH).get_json()
    assert [v["transaction_id"] for v in j["ventas"]] == ["tx-pend"]
    j = client_sin_sesion.get("/ventas?estado=aprobado&nombre_caja=caja1", headers=AUTH).get_json()
    assert [v["transaction_id"] for v in j["ventas"]] == ["tx-ok"]
    j = client_sin_sesion.get("/ventas?desde=2000-01-01&hasta=2000-01-02", headers=AUTH).get_json()
    assert j["total"] == 0


def test_ventas_paginado(client_sin_sesion):
    _ventas()
    j = client_sin_sesion.get("/ventas?limite=2", headers=AUTH).get_json()
    assert j["devueltas"] == 2 and j["hay_mas"]
    j2 = client_sin_sesion.get("/ventas?limite=2&offset=2", headers=AUTH).get_json()
    assert j2["devueltas"] == 1 and not j2["hay_mas"]


def test_ventas_parametros_invalidos(client_sin_sesion):
    for q in ("desde=07-11-2026", "tipo=visa", "estado=X", "limite=abc",
              "desde=2026-11-07&hasta=2026-11-01"):
        assert client_sin_sesion.get("/ventas?" + q, headers=AUTH).status_code == 400, q


def test_ventas_no_modifica_nada(client_sin_sesion):
    _ventas()
    antes = tx_store.obtener("tx-pend")
    client_sin_sesion.get("/ventas", headers=AUTH)
    assert tx_store.obtener("tx-pend") == antes


def test_panel_ventas(client):
    _ventas()
    j = client.get("/panel/ventas").get_json()
    assert j["ok"] and j["total"] == 3


def test_panel_ventas_protegido(client, client_sin_sesion):
    assert client_sin_sesion.get("/panel/ventas").status_code == 401
    assert client.get("/panel/ventas", environ_base=LAN).status_code == 403


def test_panel_muestra_seccion(client):
    html = client.get("/panel").get_data(as_text=True)
    assert 'id="sec-historial"' in html and "loadHistorial" in html

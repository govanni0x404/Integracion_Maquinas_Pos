"""
MODO_SERVIDOR: la API corre en un servidor y solo atiende Mercado Pago y TUU.
Getnet y Transbank (máquinas por cable) responden un error claro sin tocar nada.
"""
import pytest

import server.api_server as api
from conftest import AUTH
from core import transaction_store as tx_store

VENTA = {"id_sucursal": "1", "nombre_caja": "CAJA_TEST", "terminal_id": "POS_TEST", "amount": 1500, "timeout": 30}


@pytest.fixture
def modo_servidor(monkeypatch):
    monkeypatch.setattr(api, "MODO_SERVIDOR", True)


@pytest.mark.parametrize("tipo", ["getnet", "transbank"])
def test_maquinas_por_cable_no_disponibles_en_el_servidor(client, modo_servidor, fake_pos, tipo):
    r = client.post("/pago", json={**VENTA, "type": tipo}, headers=AUTH)
    assert r.status_code == 503
    assert r.get_json()["status"] == "no_disponible_en_servidor"
    assert "Mercado Pago y TUU" in r.get_json()["error"]
    assert fake_pos.calls == []
    assert tx_store.listar_no_resueltas() == []


def test_pago_iniciar_transbank_no_disponible_en_el_servidor(client, modo_servidor):
    r = client.post("/pago/iniciar", json={"type": "transbank", "id_sucursal": "1",
                                           "nombre_caja": "CAJA_TEST", "amount": 900}, headers=AUTH)
    assert r.status_code == 503


def test_status_informa_modo_y_version(client, modo_servidor):
    j = client.get("/status").get_json()
    assert j["modo_servidor"] is True
    assert j["version"] == api.APP_VERSION


def test_en_la_caja_transbank_sigue_funcionando(client, fake_pos):
    r = client.post("/pago", json={**VENTA, "type": "transbank"}, headers=AUTH)
    assert r.status_code == 200 and r.get_json()["estado"] == "APROBADO"

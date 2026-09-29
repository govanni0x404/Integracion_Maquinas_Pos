"""Regresiones de la auditoría de seguridad."""
import pytest

from conftest import AUTH
from core import singleton
from server.panel_html import PANEL_HTML

VENTA_TB = {"type": "transbank", "id_sucursal": "1", "nombre_caja": "CAJA_TEST",
            "terminal_id": "POS_TEST", "timeout": 30}


@pytest.mark.parametrize("monto", [-1000, 0, "abc", 15.5, "1e9", True, 10**12])
def test_montos_invalidos(client, fake_pos, monto):
    r = client.post("/pago", json={**VENTA_TB, "amount": monto}, headers=AUTH)
    assert r.status_code == 400
    assert fake_pos.calls == []


def test_monto_texto_se_normaliza(client, fake_pos):
    assert client.post("/pago", json={**VENTA_TB, "amount": "1500"}, headers=AUTH).status_code == 200
    assert fake_pos.calls[0]["amount"] == 1500


def test_body_grande_rechazado(client):
    r = client.post("/pago", data="x" * (70 * 1024), content_type="application/json", headers=AUTH)
    assert r.status_code == 413


def test_mp_api_url_no_puede_apuntar_a_otro_host(client, monkeypatch):
    from conftest import TMP_DIR
    monkeypatch.chdir(TMP_DIR)
    r = client.post("/panel/config", json={"MP_API_URL": "https://evil.test/v1/orders"})
    assert r.status_code == 400
    r = client.post("/panel/config", json={"MP_API_URL": "https://api.mercadopago.com/v1/orders"})
    assert r.status_code == 200


def test_panel_escapa_datos_de_pendientes():
    # Las notas/tickets vienen de clientes de la API: nunca se insertan sin escapar.
    assert "function esc(" in PANEL_HTML
    assert "${tx.nota}" not in PANEL_HTML
    assert "reconciliarTx('${tx.tx_id}')" not in PANEL_HTML
    assert "${esc(tx.nota)}" in PANEL_HTML


def test_singleton_no_mata_un_pid_reutilizado(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / singleton.LOCK_FILE).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / singleton.LOCK_FILE).write_text("4242")
    monkeypatch.setattr(singleton, "_pid_exists", lambda pid: True)
    monkeypatch.setattr(singleton, "_es_nuestra_instancia", lambda pid: False)
    matados = []
    monkeypatch.setattr(singleton, "_terminate_pid", lambda pid, w: matados.append(pid) or True)
    assert singleton.ensure_single_instance_interactive(stop_existing_default=True) is True
    assert matados == []
    assert (tmp_path / singleton.LOCK_FILE).read_text() == str(__import__("os").getpid())

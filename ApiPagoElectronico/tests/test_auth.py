import pytest

from config import settings
from conftest import AUTH, basic


def test_hash_real_configurado_y_sin_clave_en_texto_plano():
    assert settings.API_AUTH_PASS_HASH.startswith("pbkdf2_sha256$")
    assert not hasattr(settings, "API_AUTH_PASS")


def test_pago_sin_credenciales_401(client):
    r = client.post("/pago", json={})
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


def test_pago_clave_incorrecta_401(client):
    r = client.post("/pago", json={}, headers=basic(settings.API_AUTH_USER, "otra"))
    assert r.status_code == 401


def test_pago_usuario_incorrecto_401(client):
    from conftest import TEST_PASS
    r = client.post("/pago", json={}, headers=basic("OTRO", TEST_PASS))
    assert r.status_code == 401


def test_pago_credenciales_correctas_pasa_auth(client):
    r = client.post("/pago", json={"id_sucursal": "1"}, headers=AUTH)
    assert r.status_code != 401


@pytest.mark.parametrize("method,path", [
    ("post", "/register_agent"),
    ("get", "/poll?id_sucursal=1&nombre_caja=CAJA_TEST"),
    ("post", "/result"),
    ("get", "/debug/queues"),
    ("post", "/pago/refund"),
    ("get", "/pago/detalle"),
    ("get", "/online"),
    ("post", "/pago/resolver/x"),
])
def test_endpoints_protegidos(client, method, path):
    r = getattr(client, method)(path, json={})
    assert r.status_code == 401


def test_result_no_permite_inyectar_sin_auth(client, server):
    import threading
    server.tasks["tx-1"] = {"event": threading.Event(), "result": None}
    r = client.post("/result", json={"tx_id": "tx-1", "result": {"status": "success"}})
    assert r.status_code == 401
    assert server.tasks["tx-1"]["result"] is None


def test_auth_test_no_expone_datos_del_servidor(client):
    r = client.get("/auth/test", headers=AUTH)
    body = r.get_json()
    assert body["match"] is True
    assert "server" not in body
    assert settings.API_AUTH_USER not in r.get_data(as_text=True)

    r = client.get("/auth/test", headers=basic("x", "y"))
    assert r.get_json()["match"] is False


def test_status_publico(client):
    assert client.get("/status").status_code == 200


def test_options_no_requiere_auth(client):
    r = client.options("/pago", headers={"Origin": "http://app.test", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Origin")

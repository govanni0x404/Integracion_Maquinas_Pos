import pytest

from conftest import ENV_FILE, TMP_DIR

LAN = {"REMOTE_ADDR": "192.168.1.50"}


@pytest.fixture(autouse=True)
def cwd_en_env(monkeypatch):
    # /panel/config busca el .env en el cwd (en producción, junto al .exe)
    monkeypatch.chdir(TMP_DIR)


def test_panel_local_ok(client):
    assert client.get("/panel/pendientes").status_code == 200


def test_panel_desde_la_red_403(client):
    assert client.get("/panel/pendientes", environ_base=LAN).status_code == 403
    assert client.get("/panel", environ_base=LAN).status_code == 403
    assert client.post("/panel/config", json={"X": "1"}, environ_base=LAN).status_code == 403
    assert client.post("/panel/restart", environ_base=LAN).status_code == 403


def test_panel_resolver_desde_la_red_no_aprueba(client):
    from core import transaction_store as tx_store
    tx_store.registrar_intento("tx-lan", "transbank", "1", "POS_TEST", "1", "CAJA_TEST", 1000)
    r = client.post("/panel/pendientes/tx-lan/resolver", json={"estado": "APROBADO"}, environ_base=LAN)
    assert r.status_code == 403
    assert tx_store.obtener("tx-lan")["estado"] == "PENDIENTE"


def test_panel_host_no_local_403(client):
    # DNS rebinding: la conexión es local pero el Host es un dominio externo
    assert client.get("/panel/pendientes", base_url="http://evil.test:5005/").status_code == 403


def test_panel_origin_externo_403(client):
    r = client.post("/panel/config", json={"X": "1"}, headers={"Origin": "http://evil.test"})
    assert r.status_code == 403
    assert "Access-Control-Allow-Origin" not in r.headers


def test_panel_origin_null_403(client):
    assert client.get("/panel/pendientes", headers={"Origin": "null"}).status_code == 403


def test_panel_mismo_origin_ok(client):
    assert client.get("/panel/pendientes", headers={"Origin": "http://localhost"}).status_code == 200


def test_panel_sin_cors(client):
    r = client.get("/panel/pendientes", headers={"Origin": "http://localhost"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_config_guarda_clave_valida(client):
    r = client.post("/panel/config", json={"TEST_NUEVA_CLAVE": "abc"})
    assert r.status_code == 200
    assert "TEST_NUEVA_CLAVE=abc" in ENV_FILE.read_text(encoding="utf-8")


def test_config_ignora_credenciales(client):
    r = client.post("/panel/config", json={"API_AUTH_USER": "x", "API_AUTH_PASS": "y", "API_AUTH_PASS_HASH": "z"})
    assert r.status_code == 200
    text = ENV_FILE.read_text(encoding="utf-8")
    assert "API_AUTH_USER" not in text and "API_AUTH_PASS" not in text


def test_config_rechaza_saltos_de_linea(client):
    r = client.post("/panel/config", json={"NOMBRE_CAJA": "CAJA\nMP_ACCESS_TOKEN=robado"})
    assert r.status_code == 400
    assert "robado" not in ENV_FILE.read_text(encoding="utf-8")


def test_config_rechaza_claves_invalidas(client):
    assert client.post("/panel/config", json={"clave minuscula": "1"}).status_code == 400
    assert client.post("/panel/config", json=["no", "es", "objeto"]).status_code == 400

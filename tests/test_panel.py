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


# ── Cada máquina con sus propios identificadores y credenciales ──

def test_config_transbank_y_getnet_con_terminal_distinto(client):
    r = client.post("/panel/config", json={"TRANSBANK_TERMINAL_ID": "TBK_01", "GETNET_TERMINAL_ID": "GET_01"})
    assert r.status_code == 200
    text = ENV_FILE.read_text(encoding="utf-8")
    assert "TRANSBANK_TERMINAL_ID=TBK_01" in text and "GETNET_TERMINAL_ID=GET_01" in text


@pytest.mark.parametrize("cambios", [
    {"TRANSBANK_TERMINAL_ID": "POS01", "GETNET_TERMINAL_ID": "POS01"},
    {"GETNET_TERMINAL_ID": "NEWLAND_N950__TEST"},             # igual al terminal MP del .env
    {"MP_TERMINAL_ID": "SERIE_TEST"},                          # igual a la máquina TUU del .env
    {"TUU_DEVICES": "OTRA,TBK_X", "TRANSBANK_TERMINAL_ID": "TBK_X"},
    {"MP_TERMINAL_ID": "APP_USR-123-abc"},                     # token pegado en el campo del terminal
    {"MP_TERMINAL_ID": "TEST-ENV-TOKEN"},                      # igual a MP_ACCESS_TOKEN del .env
    {"TUU_DEVICES": "TEST-TUU-KEY-SECRETA"},                   # la API key en vez de la serie
    {"TUU_API_KEY": "TEST-ENV-TOKEN"},                         # misma credencial para MP y TUU
])
def test_config_rechaza_identificadores_compartidos(client, cambios):
    antes = ENV_FILE.read_text(encoding="utf-8")
    r = client.post("/panel/config", json=cambios)
    assert r.status_code == 400
    error = r.get_json()["error"]
    assert "TEST-ENV-TOKEN" not in error and "TEST-TUU-KEY-SECRETA" not in error  # nunca expone secretos
    assert ENV_FILE.read_text(encoding="utf-8") == antes


def test_panel_credenciales_configuradas_quedan_bloqueadas(client):
    html = client.get("/panel").get_data(as_text=True)
    for key in ("MP_ACCESS_TOKEN", "TUU_API_KEY"):
        i = html.index(f'id="f-{key}"')
        tag = html[html.rfind("<input", 0, i):html.index(">", i)]
        assert "disabled" in tag and "value=" not in tag
        assert f"editSecret('{key}'" in html
    assert "TEST-ENV-TOKEN" not in html and "TEST-TUU-KEY-SECRETA" not in html
    assert 'id="mpTodasCajas"' in html and 'name="USAR_MP"' in html


def test_restart_rechazado_con_venta_en_curso(client, server, monkeypatch):
    import threading
    lanzados = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **kw: lanzados.append(kw) or type("T", (), {"start": lambda s: None})())
    with server.busy_lock:
        server.busy_boxes.add(("1", "CAJA_TEST"))
    try:
        r = client.post("/panel/restart")
        assert r.status_code == 409
        assert "venta en curso" in r.get_json()["error"]
        assert lanzados == []
    finally:
        with server.busy_lock:
            server.busy_boxes.discard(("1", "CAJA_TEST"))


# ── Clave del panel ───────────────────────────────────────────

@pytest.fixture
def clave_panel(monkeypatch):
    from conftest import _hash
    from server import auth
    monkeypatch.setattr(auth, "PANEL_PASS_HASH", _hash("clave-panel-test"))
    return "clave-panel-test"


def test_panel_sin_sesion_muestra_la_pantalla_de_clave(client_sin_sesion):
    html = client_sin_sesion.get("/panel").get_data(as_text=True)
    assert "Clave del panel" in html and "Máquinas de pago" not in html


@pytest.mark.parametrize("metodo,ruta", [
    ("get", "/panel/pendientes"), ("post", "/panel/config"), ("post", "/panel/restart"),
    ("post", "/panel/pendientes/x/resolver"), ("get", "/panel/log"),
])
def test_panel_sin_sesion_401(client_sin_sesion, metodo, ruta):
    r = getattr(client_sin_sesion, metodo)(ruta, json={})
    assert r.status_code == 401 and r.get_json()["login"] is True


def test_panel_login_y_logout(client_sin_sesion, clave_panel):
    c = client_sin_sesion
    assert c.post("/panel/login", json={"password": "mala"}).status_code == 401
    assert c.get("/panel/pendientes").status_code == 401
    assert c.post("/panel/login", json={"password": clave_panel}).get_json()["ok"] is True
    assert c.get("/panel/pendientes").status_code == 200
    assert "Máquinas de pago" in c.get("/panel").get_data(as_text=True)
    c.post("/panel/logout")
    assert c.get("/panel/pendientes").status_code == 401


def test_panel_login_solo_local(client_sin_sesion, clave_panel):
    r = client_sin_sesion.post("/panel/login", json={"password": clave_panel}, environ_base=LAN)
    assert r.status_code == 403


def test_panel_clave_no_se_guarda_desde_el_panel(client):
    assert client.post("/panel/config", json={"PANEL_PASS_HASH": "x"}).status_code in (200, 400)
    assert "PANEL_PASS_HASH" not in ENV_FILE.read_text(encoding="utf-8")


def test_config_guarda_nombre_caja_en_mayusculas(client):
    assert client.post("/panel/config", json={"NOMBRE_CAJA": " caja_10 "}).status_code == 200
    assert "NOMBRE_CAJA=CAJA_10" in ENV_FILE.read_text(encoding="utf-8")

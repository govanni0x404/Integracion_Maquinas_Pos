import pytest

from config import settings
from conftest import AUTH, TEST_PASS, basic
from server import auth
import server.api_server as api

AGENTES = [
    ("post", "/register_agent"),
    ("get", "/poll?id_sucursal=1&nombre_caja=CAJA_TEST"),
    ("post", "/result"),
    ("get", "/debug/queues"),
]


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
    ("post", "/pago/refund"),
    ("get", "/pago/detalle"),
    ("get", "/online"),
    ("post", "/pago/resolver/x"),
])
def test_endpoints_protegidos(client, method, path):
    r = getattr(client, method)(path, json={})
    assert r.status_code == 401


@pytest.mark.parametrize("method,path", AGENTES)
def test_agentes_remotos_deshabilitados_por_defecto(client, method, path):
    r = getattr(client, method)(path, json={}, headers=AUTH)
    assert r.status_code == 410


@pytest.mark.parametrize("method,path", AGENTES)
def test_agentes_remotos_habilitados_requieren_auth(client, monkeypatch, method, path):
    monkeypatch.setattr(api, "AGENTES_REMOTOS", True)
    r = getattr(client, method)(path, json={})
    assert r.status_code == 401


def test_result_no_permite_inyectar_sin_auth(client, server, monkeypatch):
    import threading
    monkeypatch.setattr(api, "AGENTES_REMOTOS", True)
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


# ── Fuerza bruta ──────────────────────────────────────────────

def test_fuerza_bruta_bloquea_pero_no_a_la_caja(client, monkeypatch):
    client.post("/pago", json={"id_sucursal": "1"}, headers=AUTH)  # la caja ya se autenticó antes
    calculos = []
    original = auth._password_matches

    class Contador:
        def __call__(self, p):
            calculos.append(p)
            return original(p)

        def cache_clear(self):
            original.cache_clear()
    monkeypatch.setattr(auth, "_password_matches", Contador())

    for i in range(auth.MAX_FALLOS):
        client.post("/pago", json={}, headers=basic(settings.API_AUTH_USER, f"mala-{i}"))
    assert len(calculos) == auth.MAX_FALLOS

    # Bloqueado: ni la clave correcta nueva se calcula, pero la de la caja sigue funcionando
    calculos.clear()
    r = client.post("/pago", json={}, headers=basic(settings.API_AUTH_USER, "otra-mas"))
    assert r.status_code == 401 and calculos == []
    assert client.post("/pago", json={"id_sucursal": "1"}, headers=AUTH).status_code != 401


def test_usuario_con_saltos_de_linea_no_inyecta_en_el_log(client, caplog):
    client.post("/pago", json={}, headers=basic("x\n[AUTH] Acceso OK", TEST_PASS))
    assert "\n[AUTH] Acceso OK" not in caplog.text


# ── GET en endpoints de cobro (CSRF) ──────────────────────────

@pytest.mark.parametrize("dest", ["image", "iframe", "document", "script"])
def test_get_de_cobro_desde_img_o_link_rechazado(client, dest):
    r = client.get("/pago?type=mercadopago&id_sucursal=1&nombre_caja=X&amount=100",
                   headers={**AUTH, "Sec-Fetch-Dest": dest})
    assert r.status_code == 403


def test_get_de_cobro_desde_fetch_sigue_funcionando(client):
    r = client.get("/pago?id_sucursal=1", headers={**AUTH, "Sec-Fetch-Dest": "empty"})
    assert r.status_code not in (401, 403)
    r = client.get("/pago?id_sucursal=1", headers=AUTH)  # cliente sin navegador (sin Sec-Fetch-*)
    assert r.status_code not in (401, 403)

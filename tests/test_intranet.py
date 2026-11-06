"""Ingreso al panel con la ApiKey del cliente validada por la intranet."""
import hashlib
import hmac
import io
import json
import urllib.error
import urllib.parse

import pytest

from config import settings
from core import intranet

URL = "https://intranet.test/api/Conexiones"
KEY = "6A9F11-4728D0-B33624-95E04A-A13FD9"
CONEXION = [{"id_sucursal": 1, "nombre_bd": "bd", "id_cliente": 9}]


class _Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def getcode(self):
        return self.status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(monkeypatch, status=200, body=None, error=None, enviados=None):
    def _open(req, timeout=None):
        if enviados is not None:
            enviados.append(dict(urllib.parse.parse_qsl(req.data.decode())))
        if error:
            raise error
        raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(raw))
        return _Resp(status, raw)
    monkeypatch.setattr(intranet.urllib.request, "urlopen", _open)


# ── Cliente de api/Conexiones ───────────────────────────────────────────────

def test_firma_y_campos_como_la_version_php(monkeypatch):
    enviados = []
    fake_urlopen(monkeypatch, body={"ok": True, "id_sucursal": 1, "conexiones": CONEXION}, enviados=enviados)
    r = intranet.consultar_conexiones(URL, 17, f"  {KEY} ", 1)
    assert r["estado"] == intranet.VALIDA and r["conexiones"] == CONEXION
    e = enviados[0]
    assert e["apikey"] == KEY and e["id_sucursal"] == "1" and e["id_software"] == "17"
    esperada = hmac.new(KEY.encode(), f"1|17|{e['ts']}".encode(), hashlib.sha256).hexdigest()
    assert e["sig"] == esperada


def test_tolera_avisos_php_antes_del_json(monkeypatch):
    body = b"<br><b>Notice</b>: algo\n" + json.dumps({"ok": True, "id_sucursal": 1, "conexiones": CONEXION}).encode()
    fake_urlopen(monkeypatch, body=body)
    assert intranet.consultar_conexiones(URL, 17, KEY, 1)["estado"] == intranet.VALIDA


def test_apikey_de_otra_sucursal_no_coincide(monkeypatch):
    fake_urlopen(monkeypatch, body={"ok": True, "id_sucursal": 5, "conexiones": CONEXION})
    assert intranet.consultar_conexiones(URL, 17, KEY, 1)["estado"] == intranet.NO_COINCIDE


@pytest.mark.parametrize("status,estado,sin_respuesta", [
    (404, intranet.NO_COINCIDE, False),
    (400, intranet.NO_COINCIDE, False),
    (409, intranet.PIDE_SUCURSAL, False),
    (401, intranet.NO_DISPONIBLE, True),
    (403, intranet.NO_DISPONIBLE, True),
    (500, intranet.NO_DISPONIBLE, True),
    (429, intranet.BLOQUEADA, False),
])
def test_codigos_http(monkeypatch, status, estado, sin_respuesta):
    fake_urlopen(monkeypatch, status=status, body={"ok": False, "mensaje": "m", "retry_after": 30})
    r = intranet.consultar_conexiones(URL, 17, KEY, 1)
    assert r["estado"] == estado and bool(r.get("sin_respuesta")) is sin_respuesta
    if status == 429:
        assert r["retry_after"] == 30


def test_sin_red(monkeypatch):
    fake_urlopen(monkeypatch, error=urllib.error.URLError("dns"))
    r = intranet.consultar_conexiones(URL, 17, KEY, 1)
    assert r["estado"] == intranet.NO_DISPONIBLE and r["sin_respuesta"]


def test_respaldo_guarda_solo_hash(tmp_path):
    ruta = tmp_path / "r.json"
    intranet.guardar_respaldo(ruta, KEY, 1)
    assert KEY not in ruta.read_text()
    assert intranet.respaldo_coincide(ruta, KEY, 1)
    assert not intranet.respaldo_coincide(ruta, "otra", 1)
    assert not intranet.respaldo_coincide(ruta, KEY, 2)  # otra sucursal


# ── /panel/login con ApiKey ─────────────────────────────────────────────────

RESPALDO = settings.DATA_DIR / "panel_apikey.json"


@pytest.fixture(autouse=True)
def sin_respaldo():
    RESPALDO.unlink(missing_ok=True)
    yield
    RESPALDO.unlink(missing_ok=True)


@pytest.fixture
def intranet_responde(monkeypatch):
    """Reemplaza la consulta a la intranet; devuelve la lista de llamadas."""
    class _Llamadas(list):
        pass
    llamadas = _Llamadas()
    estado = {"r": {"estado": intranet.VALIDA, "mensaje": "ok", "id_sucursal": 1, "conexiones": CONEXION}}

    def _consultar(url, id_software, api_key, id_sucursal, timeout=10.0):
        llamadas.append({"url": url, "id_software": id_software, "apikey": api_key, "id_sucursal": id_sucursal})
        return estado["r"]
    monkeypatch.setattr(intranet, "consultar_conexiones", _consultar)
    llamadas.responder = lambda r: estado.__setitem__("r", r)
    return llamadas


def _login(c, key=KEY):
    return c.post("/panel/login", json={"apikey": key})


def test_login_apikey_valida(client_sin_sesion, intranet_responde):
    c = client_sin_sesion
    assert c.get("/panel/pendientes").status_code == 401
    r = _login(c)
    assert r.status_code == 200 and r.get_json()["ok"]
    assert c.get("/panel/pendientes").status_code == 200
    llamada = intranet_responde[0]
    assert llamada["url"] == settings.INTRANET_CONEXIONES_URL
    assert llamada["id_software"] == 22 and llamada["id_sucursal"] == 1
    assert RESPALDO.exists() and KEY not in RESPALDO.read_text()


def test_login_apikey_rechazada(client_sin_sesion, intranet_responde):
    intranet_responde.responder({"estado": intranet.NO_COINCIDE, "mensaje": "La ApiKey no corresponde", "conexiones": []})
    r = _login(client_sin_sesion)
    assert r.status_code == 401 and "no corresponde" in r.get_json()["error"]
    assert client_sin_sesion.get("/panel/pendientes").status_code == 401


def test_sin_intranet_acepta_la_ultima_validada(client_sin_sesion, server, intranet_responde):
    assert _login(client_sin_sesion).status_code == 200
    intranet_responde.responder({"estado": intranet.NO_DISPONIBLE, "mensaje": "Sin conexión", "sin_respuesta": True,
                                 "conexiones": []})
    c = server.app.test_client()
    r = _login(c)
    assert r.status_code == 200 and "aviso" in r.get_json()
    assert c.get("/panel/pendientes").status_code == 200
    # Otra ApiKey no entra sin intranet
    c2 = server.app.test_client()
    assert _login(c2, "OTRA-KEY-123456").status_code == 503


def test_sin_intranet_y_sin_respaldo_no_entra(client_sin_sesion, intranet_responde):
    intranet_responde.responder({"estado": intranet.NO_DISPONIBLE, "mensaje": "Sin conexión", "sin_respuesta": True,
                                 "conexiones": []})
    assert _login(client_sin_sesion).status_code == 503


def test_apikey_revocada_borra_el_respaldo(client_sin_sesion, server, intranet_responde):
    assert _login(client_sin_sesion).status_code == 200
    # Una ApiKey cualquiera rechazada NO borra el respaldo de la buena
    intranet_responde.responder({"estado": intranet.NO_COINCIDE, "mensaje": "no", "conexiones": []})
    assert _login(server.app.test_client(), "OTRA-KEY-123456").status_code == 401
    assert RESPALDO.exists()
    # La buena revocada en la intranet sí lo borra
    assert _login(server.app.test_client()).status_code == 401
    assert not RESPALDO.exists()


def test_freno_tras_varios_rechazos(client_sin_sesion, intranet_responde):
    intranet_responde.responder({"estado": intranet.NO_COINCIDE, "mensaje": "no", "conexiones": []})
    for _ in range(5):
        assert _login(client_sin_sesion).status_code == 401
    assert _login(client_sin_sesion).status_code == 429
    assert len(intranet_responde) == 5  # en pausa ya no se consulta la intranet


def test_intranet_bloqueada_429(client_sin_sesion, intranet_responde):
    intranet_responde.responder({"estado": intranet.BLOQUEADA, "mensaje": "Bloqueada.", "retry_after": 40,
                                 "conexiones": []})
    r = _login(client_sin_sesion)
    assert r.status_code == 429 and "40" in r.get_json()["error"]


def test_sin_id_sucursal_pide_clave_de_soporte(client_sin_sesion, intranet_responde, monkeypatch):
    from server import api_server
    monkeypatch.setattr(api_server, "ID_SUCURSAL", "")
    r = _login(client_sin_sesion)
    assert r.status_code == 400 and "soporte" in r.get_json()["error"]
    assert intranet_responde == []


def test_apikey_vacia(client_sin_sesion, intranet_responde):
    assert _login(client_sin_sesion, "  ").status_code == 400
    assert intranet_responde == []


def test_url_de_la_intranet_no_se_lee_del_env():
    assert "INTRANET_CONEXIONES_URL" in settings._ENV_PROTECTED_KEYS
    assert settings.INTRANET_CONEXIONES_URL.startswith("https://")

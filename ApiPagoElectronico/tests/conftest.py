"""
Configuración común de los tests.

Antes de importar cualquier módulo del proyecto se apunta POS_GATEWAY_ENV_FILE a
un .env temporal: así los tests nunca leen la configuración real de la caja ni
escriben en su pos_gateway.db / logs.
"""
import base64
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP_DIR = Path(tempfile.mkdtemp(prefix="pos_gateway_tests_"))
ENV_FILE = TMP_DIR / ".env"
ENV_FILE.write_text("\n".join([
    "HTTP_PORT=5005",
    "ID_SUCURSAL=1",
    "NOMBRE_CAJA=CAJA_TEST",
    "TERMINAL_ID=POS_TEST",
    "USAR_POS_FISICO=false",
    "USAR_GETNET=false",
    "MAX_TRANSACTION_TIME=30",
    "TIMEOUT_SERVER=30",
    "MP_API_URL=https://mp.test/v1/orders",
    "MP_ACCESS_TOKEN=TEST-ENV-TOKEN",
    "MP_TERMINAL_ID=NEWLAND_N950__TEST",
    "ALLOW_MP_TOKEN_IN_REQUEST=false",
    f"DB_FILE={TMP_DIR / 'test.db'}",
    f"LOG_FILE={TMP_DIR / 'pos_gateway.log'}",
    "",
]), encoding="utf-8")
os.environ["POS_GATEWAY_ENV_FILE"] = str(ENV_FILE)

_cwd = os.getcwd()
from config import settings  # noqa: E402  (hace os.chdir a la carpeta del .env)
from core import transaction_store as tx_store  # noqa: E402
from server import auth  # noqa: E402
os.chdir(_cwd)  # pytest resuelve testpaths contra el cwd

TEST_PASS = "clave-de-prueba"


def _hash(pwd, iterations=1000):
    salt = b"salt-de-prueba!!"
    digest = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def basic(user, pwd):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()}


AUTH = basic(settings.API_AUTH_USER, TEST_PASS)


@pytest.fixture(autouse=True)
def test_password(monkeypatch):
    """Reemplaza el hash real por el de una clave de prueba conocida."""
    monkeypatch.setattr(auth, "API_AUTH_PASS_HASH", _hash(TEST_PASS))
    auth._password_matches.cache_clear()
    yield
    auth._password_matches.cache_clear()


@pytest.fixture(autouse=True)
def clean_db():
    with tx_store._lock:
        conn = tx_store._get_conn()
        conn.execute("DELETE FROM transacciones")
        conn.commit()
    yield


class FakePOS:
    """Sustituye al POSModule de Transbank: sin puerto serie ni SDK."""

    def __init__(self):
        self.calls = []
        self.behavior = lambda **kw: {"status": "success", "response": {"response_code": "0"}}

    def do_sale_with_timeout(self, amount, timeout=30, ticket=None, on_late_result=None):
        self.calls.append({"amount": amount, "timeout": timeout, "ticket": ticket})
        return self.behavior(amount=amount, timeout=timeout, ticket=ticket, on_late_result=on_late_result)

    def is_online(self):
        return True

    def get_current_port(self):
        return "COM_TEST"


@pytest.fixture
def fake_pos():
    return FakePOS()


@pytest.fixture
def server(fake_pos):
    from server.api_server import APIServer
    srv = APIServer(fake_pos, None)
    yield srv
    # Solo se señalizan los hilos (son daemon): esperar su join demora ~2 s por test.
    srv._stop_local_worker.set()
    srv._stop_cleanup.set()


@pytest.fixture
def client(server):
    return server.app.test_client()


def pytest_sessionfinish(session, exitstatus):
    import shutil
    shutil.rmtree(TMP_DIR, ignore_errors=True)

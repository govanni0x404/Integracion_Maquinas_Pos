"""
Validación de la ApiKey del cliente contra la intranet (api/Conexiones).

Cada cliente que usa ApiPago tiene en la intranet una conexión del software
API PAGO ELECTRONICO con su ApiKey. El panel de control acepta esa ApiKey como
clave de ingreso: se consulta a la intranet con el ID_SUCURSAL de esta caja y
solo se entra si la intranet confirma que la ApiKey es vigente para ella.

Respaldo sin conexión: cuando la intranet aprueba una ApiKey se guarda SOLO su
hash (PBKDF2) en data/panel_apikey.json. Si después la intranet no responde, se
acepta la ApiKey si coincide con ese hash. Si la intranet dice que la ApiKey ya
no corresponde (revocada), el hash se borra.
"""
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from core.logging_config import logger as _log

# Estados (mismos nombres que la versión PHP)
VALIDA = "valida"
NO_COINCIDE = "no_coincide"
PIDE_SUCURSAL = "pide_sucursal"
NO_DISPONIBLE = "no_disponible"
BLOQUEADA = "bloqueada"

_ITERACIONES = 200_000
_lock_respaldo = threading.Lock()


def _resultado(estado, mensaje, **extra):
    return {"estado": estado, "mensaje": mensaje, "conexiones": [], **extra}


def _leer_json_tolerante(texto: str):
    """Igual que appDesktop: tolera avisos de PHP antes (y después) del JSON."""
    inicio = texto.find("{")
    if inicio < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(texto[inicio:])
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def consultar_conexiones(url: str, id_software: int, api_key: str, id_sucursal: int,
                         timeout: float = 10.0) -> dict:
    """
    Consulta api/Conexiones. Devuelve {"estado", "mensaje", "conexiones", ...}.
    "sin_respuesta" = True cuando la intranet no contestó o falló de su lado
    (red caída, 5xx, firma por hora desfasada, IP no permitida): solo en esos
    casos se permite usar el respaldo local.
    """
    api_key = (api_key or "").strip()
    if not api_key or id_software <= 0:
        return _resultado(NO_COINCIDE, "Falta la ApiKey o el id_software.")

    ts = int(time.time())
    firma = hmac.new(api_key.encode("utf-8"), f"{id_sucursal}|{id_software}|{ts}".encode("utf-8"),
                     hashlib.sha256).hexdigest()
    cuerpo = urllib.parse.urlencode({
        "id_sucursal": id_sucursal,
        "id_software": id_software,
        "apikey": api_key,
        "ts": ts,
        "sig": firma,
    }).encode("utf-8")
    req = urllib.request.Request(url.strip(), data=cuerpo, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        try:
            raw = e.read()
        except Exception:
            raw = b""
    except Exception as e:
        return _resultado(NO_DISPONIBLE, f"Sin conexión con la intranet: {getattr(e, 'reason', e)}",
                          sin_respuesta=True)

    data = _leer_json_tolerante(raw.decode("utf-8", errors="replace") if raw else "")
    msg = str(data.get("mensaje") or "") if data else ""

    if status == 200 and data and data.get("ok") and data.get("conexiones"):
        try:
            sucursal_intranet = int(data.get("id_sucursal") or 0)
        except (TypeError, ValueError):
            sucursal_intranet = 0
        if sucursal_intranet and sucursal_intranet != id_sucursal:
            return _resultado(NO_COINCIDE, f"La ApiKey es de la sucursal {sucursal_intranet}, "
                                           f"no de esta caja (sucursal {id_sucursal}).")
        return {"estado": VALIDA, "mensaje": "Clave técnica correcta.",
                "id_sucursal": sucursal_intranet or id_sucursal, "conexiones": data["conexiones"]}

    if status == 404:
        return _resultado(NO_COINCIDE, "La ApiKey no corresponde a una conexión vigente de este software.")
    if status == 409:
        return _resultado(PIDE_SUCURSAL, "La ApiKey está en más de una sucursal: envíe id_sucursal.")
    if status == 400:
        return _resultado(NO_COINCIDE, msg or "La intranet rechazó los datos enviados.")
    if status == 401:
        return _resultado(NO_DISPONIBLE, "Firma rechazada: revise la fecha y hora del equipo.", sin_respuesta=True)
    if status == 403:
        return _resultado(NO_DISPONIBLE, "IP no permitida para consumir api/Conexiones.", sin_respuesta=True)
    if status == 429:
        try:
            retry = int((data or {}).get("retry_after") or 0)
        except (TypeError, ValueError):
            retry = 0
        return _resultado(BLOQUEADA, msg or "Demasiados intentos en la intranet.", retry_after=retry)
    return _resultado(NO_DISPONIBLE, f"La intranet respondió {status}" + (f": {msg}" if msg else "."),
                      sin_respuesta=status >= 500 or status == 0)


# ── Respaldo local (solo hash) ──────────────────────────────────────────────

def _hash(api_key: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", api_key.encode("utf-8"), salt, _ITERACIONES).hex()


def guardar_respaldo(ruta, api_key: str, id_sucursal: int):
    salt = os.urandom(16)
    contenido = {"id_sucursal": id_sucursal, "salt": salt.hex(), "hash": _hash(api_key.strip(), salt),
                 "validada": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _lock_respaldo:
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            tmp = ruta.with_suffix(".tmp")
            tmp.write_text(json.dumps(contenido), encoding="utf-8")
            os.replace(tmp, ruta)
        except Exception as e:
            _log.warning("[PANEL] No se pudo guardar el respaldo de la ApiKey: %s", e)


def borrar_respaldo(ruta):
    with _lock_respaldo:
        try:
            ruta.unlink()
        except FileNotFoundError:
            pass
        except Exception as e:
            _log.warning("[PANEL] No se pudo borrar el respaldo de la ApiKey: %s", e)


def respaldo_coincide(ruta, api_key: str, id_sucursal: int):
    """Devuelve la fecha de la última validación si la ApiKey coincide con el
    respaldo de esta sucursal; si no, None."""
    with _lock_respaldo:
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            return None
    try:
        if int(datos.get("id_sucursal")) != id_sucursal:
            return None
        calc = _hash(api_key.strip(), bytes.fromhex(datos["salt"]))
        if hmac.compare_digest(calc, str(datos["hash"])):
            return datos.get("validada") or "?"
    except Exception:
        return None
    return None

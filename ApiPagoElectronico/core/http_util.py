"""
Cliente HTTP mínimo (urllib) compartido por las integraciones en la nube
(Mercado Pago y TUU): no agrega dependencias al .exe.
"""
import json
import http.client
import urllib.request
import urllib.error


def http_json(method: str, url: str, headers: dict | None = None, payload: dict | None = None, timeout: int = 15):
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, method=method.upper(), headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read()
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        try:
            body = e.read()
        except Exception:
            body = b""
    decoded = body.decode("utf-8", errors="replace") if body else ""
    try:
        parsed = json.loads(decoded) if decoded else {}
    except Exception:
        parsed = {"raw": decoded}
    return status, parsed


def error_de_red_ambiguo(e):
    """True si, pese al error, el pedido pudo haber llegado al servidor remoto
    (timeout o conexión cortada después de enviarlo). Un DNS caído o una
    conexión rechazada, en cambio, garantizan que no salió nada."""
    reason = getattr(e, "reason", e)
    return isinstance(reason, (TimeoutError, ConnectionResetError, http.client.HTTPException))

"""
Orden de carpetas junto al .exe:

    <carpeta>/
        ApiPagoElectronico.exe
        .env
        logs/general/       log técnico (pos_gateway-YYYY-MM-DD.log)
        logs/getnet/        log de negocio de cada integración
        logs/transbank/
        logs/mercadopago/
        logs/diagnostico/   auth_diag.log
        data/               pos_gateway.db (+ -wal/-shm) y pos_gateway.lock

Las versiones anteriores dejaban todo suelto junto al .exe: al arrancar se
mueve a su carpeta. No importa logging (se ejecuta antes de configurarlo);
devuelve lo que hizo para registrarlo después.
"""
import re
import shutil
from pathlib import Path

CARPETA_LOG_GENERAL = "general"
LOGS_NEGOCIO = ("getnet", "transbank", "mercadopago")
_LOG_DIARIO = re.compile(r"^(?P<base>[A-Za-z_]+)(-\d{4}-\d{2}-\d{2})?\.log$")


def carpeta_de_log(logs_dir, base_name, base_general):
    """Carpeta de un log: el técnico va a logs/general, cada integración a la suya."""
    nombre = CARPETA_LOG_GENERAL if base_name == base_general else base_name
    return Path(logs_dir) / nombre


def _mover(origen, destino, movidos, errores):
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origen), str(destino))
        movidos.append(f"{origen.name} -> {destino.parent.name}/")
    except Exception as e:  # ej. archivo abierto por otra instancia en Windows
        errores.append(f"{origen.name}: {e}")


def migrar_archivos_sueltos(base_dir, db_file, lock_file, logs_dir, base_general):
    """
    Mueve a data/ y logs/ los archivos que versiones anteriores dejaban sueltos
    en base_dir. Nunca pisa un archivo que ya exista en el destino.
    Returns: {"movidos": [...], "errores": [...], "omitidos": [...]}
    """
    base_dir = Path(base_dir)
    movidos, errores, omitidos = [], [], []
    db_file, lock_file = Path(db_file), Path(lock_file)

    # Base de datos: el .db y sus archivos -wal/-shm van juntos o no se mueven
    # (el -wal puede tener transacciones que todavía no pasaron al .db).
    viejo_db = base_dir / db_file.name
    if viejo_db.exists() and viejo_db.resolve() != db_file.resolve():
        if db_file.exists():
            omitidos.append(f"{viejo_db.name}: ya existe {db_file} (se deja el viejo sin tocar)")
        else:
            for sufijo in ("", "-wal", "-shm", "-journal"):
                origen = base_dir / (db_file.name + sufijo)
                if origen.exists():
                    _mover(origen, db_file.parent / origen.name, movidos, errores)

    # Lock: se mueve para que la detección de "ya hay otra instancia" siga
    # viendo a una versión anterior que esté corriendo.
    viejo_lock = base_dir / lock_file.name
    if viejo_lock.exists() and viejo_lock.resolve() != lock_file.resolve() and not lock_file.exists():
        _mover(viejo_lock, lock_file, movidos, errores)

    # Logs sueltos (diarios o de versiones más antiguas sin fecha)
    for f in base_dir.glob("*.log"):
        m = _LOG_DIARIO.match(f.name)
        if f.name == "auth_diag.log":
            destino = Path(logs_dir) / "diagnostico" / f.name
        elif m and m.group("base") in (base_general, *LOGS_NEGOCIO):
            destino = carpeta_de_log(logs_dir, m.group("base"), base_general) / f.name
        else:
            continue
        if destino.exists():
            omitidos.append(f"{f.name}: ya existe en {destino.parent.name}/")
            continue
        _mover(f, destino, movidos, errores)

    return {"movidos": movidos, "errores": errores, "omitidos": omitidos}

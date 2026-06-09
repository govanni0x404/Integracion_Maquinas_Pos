@echo off
:: =============================================================
::  iniciar_windows.bat — Inicia ApiPagoElectronico en Windows
::  Compatible con Windows 10/11 (x64)
::
::  MODOS DE INICIO:
::   1. EJECUTABLE compilado: dist\ApiPagoElectronico\ApiPagoElectronico.exe
::   2. PYTHON DIRECTO (desarrollo): python app.py
:: =============================================================

setlocal EnableDelayedExpansion

set "SILENT=0"
if /i "%~1"=="--silent" set "SILENT=1"

set "APP_NAME=ApiPagoElectronico"
set "SCRIPT_DIR=%~dp0"
:: Quitar barra final
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

set "DIST_BIN=%SCRIPT_DIR%\dist\%APP_NAME%\%APP_NAME%.exe"
set "DIST_EXE=%SCRIPT_DIR%\dist\%APP_NAME%.exe"
set "LOG_FILE=%SCRIPT_DIR%\dist\pos_gateway.log"
set "PID_FILE=%SCRIPT_DIR%\dist\pos_gateway.pid"

if !SILENT!==0 (
    echo.
    echo ==============================================
    echo   %APP_NAME%
    echo   Iniciando en Windows...
    echo ==============================================
    echo.
)

:: ── 0. Leer HTTP_PORT del .env ─────────────────────────────
set "ENV_FILE=%SCRIPT_DIR%\dist\.env"
if not exist "!ENV_FILE!" set "ENV_FILE=%SCRIPT_DIR%\.env"

if not exist "!ENV_FILE!" (
    if !SILENT!==0 (
        echo [ERROR] No se encontro el archivo .env
        echo   Esperado en: %SCRIPT_DIR%\dist\.env
        pause
    )
    exit /b 1
)

set "HTTP_PORT=5005"
set "ID_SUCURSAL=N/A"
set "NOMBRE_CAJA=N/A"

for /f "usebackq tokens=1,* delims==" %%A in ("!ENV_FILE!") do (
    if "%%A"=="HTTP_PORT"    set "HTTP_PORT=%%B"
    if "%%A"=="ID_SUCURSAL"  set "ID_SUCURSAL=%%B"
    if "%%A"=="NOMBRE_CAJA"  set "NOMBRE_CAJA=%%B"
)
:: Limpiar espacios y CR (por si el .env viene de Linux)
:: Limpiar espacios y CR (por si el .env viene de Linux/Mac)
for /f "tokens=* delims= " %%A in ("!HTTP_PORT!")   do set "HTTP_PORT=%%A"
for /f "tokens=* delims= " %%A in ("!ID_SUCURSAL!") do set "ID_SUCURSAL=%%A"
for /f "tokens=* delims= " %%A in ("!NOMBRE_CAJA!") do set "NOMBRE_CAJA=%%A"

if !SILENT!==0 (
    echo   Sucursal:  !ID_SUCURSAL!
    echo   Caja:      !NOMBRE_CAJA!
    echo   Puerto:    !HTTP_PORT!
    echo.
)

:: ── 1. Verificar si ya corre ─────────────────────────────────
if !SILENT!==0 echo [*] Verificando si ya esta corriendo...
curl -s --connect-timeout 2 "http://localhost:!HTTP_PORT!/status" >\\.\NUL 2>&1
if !errorlevel!==0 (
    if !SILENT!==0 (
        echo [AVISO] El servicio ya esta activo en el puerto !HTTP_PORT!
        echo.
        echo   Para detenerlo:    detener_windows.bat
        pause
    )
    exit /b 0
)
if !SILENT!==0 echo.

:: ── 2. Modo de inicio ─────────────────────────────────────────
if exist "!DIST_BIN!" (
    if !SILENT!==0 (
        echo [OK] Modo: EJECUTABLE COMPILADO [onedir]
        echo   Binario: !DIST_BIN!
        echo.
    )
    start "" "!DIST_BIN!"
    if !SILENT!==0 (goto :wait_server) else exit /b 0
)

if exist "!DIST_EXE!" (
    if !SILENT!==0 (
        echo [OK] Modo: EJECUTABLE COMPILADO [onefile]
        echo   Binario: !DIST_EXE!
        echo.
    )
    start "" "!DIST_EXE!"
    if !SILENT!==0 (goto :wait_server) else exit /b 0
)

if exist "%SCRIPT_DIR%\app.py" (
    if !SILENT!==0 (
        echo [*] Modo: PYTHON DIRECTO desarrollo
        echo.
    )

    :: Buscar Python
    set "PYTHON_BIN="
    if exist "%SCRIPT_DIR%\venv\Scripts\python.exe" (
        set "PYTHON_BIN=%SCRIPT_DIR%\venv\Scripts\python.exe"
        echo   Python: !PYTHON_BIN! [venv]
    ) else (
        where python >\\.\NUL 2>&1
        if !errorlevel!==0 (
            for /f "tokens=*" %%P in ('where python') do (
                if not defined PYTHON_BIN set "PYTHON_BIN=%%P"
            )
            echo   Python: !PYTHON_BIN! [sistema]
        )
    )

    if not defined PYTHON_BIN (
        if !SILENT!==0 (
            echo [ERROR] Python no encontrado.
            echo   Descarga desde: https://www.python.org/downloads/
            pause
        )
        exit /b 1
    )

    set "PYTHONW_BIN="
    if /i "!PYTHON_BIN!"=="%SCRIPT_DIR%\venv\Scripts\python.exe" (
        if exist "%SCRIPT_DIR%\venv\Scripts\pythonw.exe" set "PYTHONW_BIN=%SCRIPT_DIR%\venv\Scripts\pythonw.exe"
    )
    if not defined PYTHONW_BIN (
        for %%X in ("!PYTHON_BIN!") do (
            if exist "%%~dpXpythonw.exe" set "PYTHONW_BIN=%%~dpXpythonw.exe"
        )
    )
    if defined PYTHONW_BIN (
        start "" "!PYTHONW_BIN!" "%SCRIPT_DIR%\app.py"
    ) else (
        start "" "!PYTHON_BIN!" "%SCRIPT_DIR%\app.py"
    )
    if !SILENT!==0 (goto :wait_server) else exit /b 0
)

if !SILENT!==0 (
    echo [ERROR] No se encontro ejecutable ni app.py.
    echo   Compila primero con: compilar_windows.bat
    pause
)
exit /b 1

:wait_server
:: ── 3. Esperar que levante ─────────────────────────────────
echo [*] Esperando que el servidor HTTP levante...
set /a WAITED=0
set /a MAX_WAIT=20

:poll_loop
if !WAITED! geq !MAX_WAIT! goto :timeout_check
curl -s --connect-timeout 1 "http://localhost:!HTTP_PORT!/status" >\\.\NUL 2>&1
if !errorlevel!==0 goto :success
timeout /t 1 /nobreak >\\.\NUL
set /a WAITED+=1
<nul set /p ".=."
goto :poll_loop

:timeout_check
echo.
echo [ERROR] El servicio no respondio en !MAX_WAIT!s
echo.
if exist "!LOG_FILE!" (
    echo Ultimas lineas del log:
    powershell -command "Get-Content '!LOG_FILE!' -Tail 15"
)
if !SILENT!==0 pause
exit /b 1

:success
echo.
for /f "tokens=*" %%S in ('curl -s "http://localhost:!HTTP_PORT!/status" 2^>\\.\NUL') do set "ESTADO=%%S"
echo.
echo [OK] Servicio iniciado correctamente
echo   URL:   http://localhost:!HTTP_PORT!/status
echo   Panel: http://localhost:!HTTP_PORT!/panel
echo   Log:   !LOG_FILE!
echo.
echo Para detener: detener_windows.bat
echo.
timeout /t 3 /nobreak >\\.\NUL

endlocal

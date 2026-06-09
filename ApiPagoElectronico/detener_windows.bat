@echo off
:: =============================================================
::  detener_windows.bat — Detiene ApiPagoElectronico en Windows
:: =============================================================

setlocal EnableDelayedExpansion

set "SILENT=0"
if /i "%~1"=="--silent" set "SILENT=1"

set "APP_NAME=ApiPagoElectronico"
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "PID_FILE=%SCRIPT_DIR%\dist\pos_gateway.pid"

if !SILENT!==0 (
    echo.
    echo ==============================================
    echo   %APP_NAME% — Deteniendo servicio...
    echo ==============================================
    echo.
)

:: ── Por PID file ─────────────────────────────────────────────
if exist "!PID_FILE!" (
    set /p PID=<"!PID_FILE!"
    :: Limpiar espacios
    for /f "tokens=* delims= " %%A in ("!PID!") do set "PID=%%A"

    if defined PID (
        if !SILENT!==0 echo [*] Terminando proceso PID: !PID!
        taskkill /PID !PID! /F >\\.\NUL 2>&1
        if !errorlevel!==0 (
            if !SILENT!==0 echo [OK] Proceso !PID! terminado.
        ) else (
            if !SILENT!==0 echo [AVISO] Proceso !PID! no encontrado o ya terminado.
        )
        del /f /q "!PID_FILE!" >\\.\NUL 2>&1
        goto :done
    )
)

:: ── Fallback: buscar por nombre de proceso ───────────────────
if !SILENT!==0 echo [AVISO] PID file no encontrado. Buscando proceso por nombre...

tasklist /FI "IMAGENAME eq %APP_NAME%.exe" 2>\\.\NUL | find /i "%APP_NAME%.exe" >\\.\NUL
if !errorlevel!==0 (
    if !SILENT!==0 echo [*] Encontrado proceso %APP_NAME%.exe. Terminando...
    taskkill /IM "%APP_NAME%.exe" /F >\\.\NUL 2>&1
    if !SILENT!==0 echo [OK] Proceso terminado.
    goto :done
)

:: Buscar python corriendo app.py
if !SILENT!==0 echo [*] Buscando proceso python con app.py...
for /f "tokens=2" %%P in ('tasklist /FI "IMAGENAME eq python.exe" /NH 2^>\\.\NUL') do (
    wmic process where "ProcessId=%%P" get CommandLine 2>\\.\NUL | find /i "app.py" >\\.\NUL
    if !errorlevel!==0 (
        if !SILENT!==0 echo [*] Terminando python PID %%P...
        taskkill /PID %%P /F >\\.\NUL 2>&1
        if !SILENT!==0 echo [OK] Proceso %%P terminado.
        goto :done
    )
)

if !SILENT!==0 (
    echo [AVISO] No se encontro ningun proceso activo de %APP_NAME%.
    echo   El servicio puede no estar corriendo.
)

:done
if exist "!PID_FILE!" del /f /q "!PID_FILE!" >\\.\NUL 2>&1
if !SILENT!==0 (
    echo.
    echo [OK] Servicio detenido.
    echo.
    timeout /t 2 /nobreak >\\.\NUL
)
endlocal

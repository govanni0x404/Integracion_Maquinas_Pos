@echo off
:: =============================================================
::  reiniciar_windows.bat — Reinicia ApiPagoElectronico en Windows
:: =============================================================
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo Reiniciando ApiPagoElectronico (Windows)...
set "SILENT_ARG="
if /i "%~1"=="--silent" set "SILENT_ARG=--silent"

call "%SCRIPT_DIR%\detener_windows.bat" %SILENT_ARG%
timeout /t 3 /nobreak >\\.\NUL
call "%SCRIPT_DIR%\iniciar_windows.bat" %SILENT_ARG%

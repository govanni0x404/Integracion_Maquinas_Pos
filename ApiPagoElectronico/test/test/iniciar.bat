@echo off
cd /d "%~dp0"
echo ==========================================
echo Iniciando ApiPagoElectronico...
echo ==========================================

REM Verificar si Python está instalado
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python no está instalado o no está en el PATH
    echo Por favor instala Python 3.10 desde python.org
    pause
    exit /b 1
)

REM Verificar dependencias
python -c "import flask" >nul 2>&1
if %errorlevel% neq 0 (
    echo Instalando dependencias...
    python -m pip install -r requirements.txt
)

REM Iniciar aplicación
echo Iniciando servidor...
start /B pythonw app.py

echo.
echo ==========================================
echo Servidor iniciado en segundo plano
echo Presiona cualquier tecla para salir
echo ==========================================
pause >nul
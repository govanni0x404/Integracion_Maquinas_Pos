@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo.
echo ==============================================
echo   ApiPagoElectronico
echo   Instalando dependencias en Windows...
echo ==============================================
echo.

cd /d "%SCRIPT_DIR%"

:: ── 1. Buscar Python ─────────────────────────────────────────
echo [*] Buscando Python...

set "PYTHON_BIN="

py -3.12 --version >\\.\NUL 2>&1
if not errorlevel 1 set "PYTHON_BIN=py -3.12"

if not defined PYTHON_BIN (
    python --version >\\.\NUL 2>&1
    if not errorlevel 1 set "PYTHON_BIN=python"
)

if not defined PYTHON_BIN (
    echo.
    echo [ERROR] Python no encontrado.
    echo   Descarga Python 3.12 x64 desde: https://www.python.org/downloads/windows/
    echo   Al instalar marca: "Add Python to PATH"
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('%PYTHON_BIN% --version 2^>^&1') do echo   Encontrado: %%V

:: ── 1.1 Verificar versión soportada por PyInstaller ──────────
set "PY_VER="
set "PY_BITS="
set "PY_EXE="
for /f "tokens=*" %%V in ('%PYTHON_BIN% -c "import sys; print(str(sys.version_info[0])+\".\"+str(sys.version_info[1]))" 2^>^&1') do set "PY_VER=%%V"
for /f "tokens=*" %%B in ('%PYTHON_BIN% -c "import struct; print(struct.calcsize(\"P\")*8)" 2^>^&1') do set "PY_BITS=%%B"
for /f "delims=" %%E in ('%PYTHON_BIN% -c "import sys; print(sys.executable)" 2^>^&1') do set "PY_EXE=%%E"

%PYTHON_BIN% -c "import sys; sys.exit(0 if (sys.version_info[0]==3 and sys.version_info[1]==12) else 1)" >\\.\NUL 2>&1
set "PY_VER_CHECK=%errorlevel%"
if not "%PY_VER_CHECK%"=="0" (
    echo.
    echo [ERROR] Python %PY_VER% detectado.
    echo   Para evitar errores de compilación ^(ej: faltan módulos stdlib como unicodedata^)
    echo   compila con Python 3.12 x64 oficial de python.org.
    echo.
    echo   1. Instala Python 3.12 x64 desde: https://www.python.org/downloads/windows/
    echo   2. Elimina la carpeta venv\
    echo   3. Ejecuta nuevamente este instalador de dependencias
    pause
    exit /b 1
)

%PYTHON_BIN% -c "import struct,sys; sys.exit(0 if (struct.calcsize('P')*8==64) else 1)" >\\.\NUL 2>&1
set "PY_BITS_CHECK=%errorlevel%"
if not "%PY_BITS_CHECK%"=="0" (
    echo.
    echo [ERROR] Python %PY_VER% %PY_BITS%-bit detectado.
    echo   Instala Python 3.12 x64 oficial desde: https://www.python.org/downloads/windows/
    echo   Luego elimina la carpeta venv\ y ejecuta nuevamente este instalador.
    pause
    exit /b 1
)

echo %PY_EXE% | find /i "\WindowsApps\" >\\.\NUL
if not errorlevel 1 (
    echo.
    echo [ERROR] Se detecto Python del Microsoft Store:
    echo   %PY_EXE%
    echo.
    echo   Instala Python 3.12 x64 oficial desde: https://www.python.org/downloads/windows/
    echo   Luego elimina la carpeta venv\ y ejecuta nuevamente este instalador.
    pause
    exit /b 1
)

:: ── 2. Verificar requirements.txt ────────────────────────────
if not exist "%SCRIPT_DIR%\requirements.txt" (
    echo.
    echo [ERROR] No se encontro requirements.txt
    pause
    exit /b 1
)

:: ── 3. Verificar/recrear venv ────────────────────────────────
echo.
echo [*] Verificando entorno virtual...

set "VENV_PYTHON=%SCRIPT_DIR%\venv\Scripts\python.exe"

:: Si el venv existe pero python.exe no está dentro → está roto, borrarlo
if exist "%SCRIPT_DIR%\venv\" (
    if not exist "%VENV_PYTHON%" (
        echo   venv existente esta roto. Eliminando y recreando...
        rmdir /s /q "%SCRIPT_DIR%\venv"
    ) else (
        set "VENV_BASE_EXE="
        for /f "delims=" %%E in ('"%VENV_PYTHON%" -c "import sys; print(getattr(sys, \"_base_executable\", sys.executable))" 2^>^&1') do set "VENV_BASE_EXE=%%E"

        echo !VENV_BASE_EXE! | find /i "\WindowsApps\" >\\.\NUL
        if not errorlevel 1 (
            echo   venv existente usa Python del Microsoft Store. Eliminando y recreando...
            rmdir /s /q "%SCRIPT_DIR%\venv"
        ) else (
            "%VENV_PYTHON%" -c "import sys; sys.exit(0 if (sys.version_info[0]==3 and sys.version_info[1]==12) else 1)" >\\.\NUL 2>&1
            if errorlevel 1 (
                echo   venv existente no es Python 3.12. Eliminando y recreando con Python 3.12...
                rmdir /s /q "%SCRIPT_DIR%\venv"
            ) else (
                "%VENV_PYTHON%" -c "import struct,sys; sys.exit(0 if (struct.calcsize('P')*8==64) else 1)" >\\.\NUL 2>&1
                if errorlevel 1 (
                    echo   venv existente no es x64. Eliminando y recreando con Python 3.12 x64...
                    rmdir /s /q "%SCRIPT_DIR%\venv"
                ) else (
                    echo   OK - venv existente y valido ^(Python 3.12 x64^).
                    goto :instalar
                )
            )
        )
    )
)

:: Crear venv nuevo
echo   Creando venv con %PYTHON_BIN%...
%PYTHON_BIN% -m venv --copies "%SCRIPT_DIR%\venv"
if errorlevel 1 (
    echo.
    echo [ERROR] No se pudo crear el virtualenv.
    echo.
    echo   Si usas Python del Microsoft Store, instala Python desde:
    echo   https://www.python.org/downloads/windows/
    echo   ^(version oficial, NO la del Store^)
    pause
    exit /b 1
)

if not exist "%VENV_PYTHON%" (
    echo.
    echo [ERROR] El venv se creo pero python.exe no se encontro en:
    echo   %VENV_PYTHON%
    echo.
    echo   Intenta instalar Python desde python.org en lugar del Microsoft Store.
    pause
    exit /b 1
)
echo   OK - venv creado correctamente.

:instalar
:: ── 4. Actualizar pip ────────────────────────────────────────
echo.
echo [*] Actualizando pip...
"%VENV_PYTHON%" -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo   AVISO: no se pudo actualizar pip, continuando...
)

:: ── 5. Instalar dependencias ─────────────────────────────────
echo.
echo [*] Instalando dependencias ^(puede tardar varios minutos^)...
echo.

"%VENV_PYTHON%" -m pip install -r "%SCRIPT_DIR%\requirements.txt"

if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la instalacion.
    echo   Revisa los errores arriba.
    pause
    exit /b 1
)

"%VENV_PYTHON%" -c "import serial" >\\.\NUL 2>&1
if errorlevel 1 (
    echo.
    echo   AVISO: pyserial no quedo instalado. Reintentando instalacion...
    "%VENV_PYTHON%" -m pip install pyserial==3.5
    if errorlevel 1 (
        echo.
        echo [ERROR] No se pudo instalar pyserial.
        pause
        exit /b 1
    )
)

:: ── 6. Confirmar ─────────────────────────────────────────────
echo.
echo ==============================================
echo   OK - Dependencias instaladas correctamente
echo.
echo   Siguiente paso: compilar_windows.bat
echo ==============================================
echo.
pause
endlocal

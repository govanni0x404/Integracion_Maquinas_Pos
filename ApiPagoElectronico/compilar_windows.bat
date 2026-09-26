@echo off
setlocal

set "APP_NAME=ApiPagoElectronico"
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "VENV_PYTHON=%SCRIPT_DIR%\venv\Scripts\python.exe"
set "VENV_PYINSTALLER=%SCRIPT_DIR%\venv\Scripts\pyinstaller.exe"

echo.
echo ==============================================
echo   Compilando %APP_NAME%
echo   Plataforma: Windows x64 (--onefile portable)
echo ==============================================
echo.

cd /d "%SCRIPT_DIR%"

:: ── 1. Verificar venv ────────────────────────────────────────
echo [*] Verificando entorno virtual...

if not exist "%VENV_PYTHON%" (
    echo.
    echo [ERROR] No se encontro el venv. Ejecuta primero:
    echo   instalar_dependencias_windows.bat
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('"%VENV_PYTHON%" --version 2^>^&1') do echo   Python: %%V

:: ── 1.1 Verificar versión soportada por PyInstaller ──────────
set "VENV_PY_VER="
set "VENV_PY_BITS="
set "VENV_BASE_EXE="
for /f "tokens=*" %%V in ('"%VENV_PYTHON%" -c "import sys; print(str(sys.version_info[0])+\".\"+str(sys.version_info[1]))" 2^>^&1') do set "VENV_PY_VER=%%V"
for /f "tokens=*" %%B in ('"%VENV_PYTHON%" -c "import struct; print(struct.calcsize(\"P\")*8)" 2^>^&1') do set "VENV_PY_BITS=%%B"
for /f "delims=" %%E in ('"%VENV_PYTHON%" -c "import sys; print(getattr(sys, \"_base_executable\", sys.executable))" 2^>^&1') do set "VENV_BASE_EXE=%%E"

"%VENV_PYTHON%" -c "import sys; sys.exit(0 if (sys.version_info[0]==3 and sys.version_info[1]==12) else 1)" >\\.\NUL 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python %VENV_PY_VER% detectado en el venv.
    echo   Para evitar errores de compilación ^(ej: faltan módulos stdlib como unicodedata^)
    echo   compila con Python 3.12 x64 oficial de python.org.
    echo.
    echo   Pasos:
    echo     1. Instala Python 3.12 x64 desde: https://www.python.org/downloads/windows/
    echo     2. Borra la carpeta venv\
    echo     3. Ejecuta: instalar_dependencias_windows.bat
    echo     4. Ejecuta: compilar_windows.bat
    pause
    exit /b 1
)

"%VENV_PYTHON%" -c "import struct,sys; sys.exit(0 if (struct.calcsize('P')*8==64) else 1)" >\\.\NUL 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python %VENV_PY_VER% %VENV_PY_BITS%-bit detectado en el venv.
    echo   Compila con Python 3.12 x64 oficial de python.org.
    echo.
    echo   Pasos:
    echo     1. Instala Python 3.12 x64 desde: https://www.python.org/downloads/windows/
    echo     2. Borra la carpeta venv\
    echo     3. Ejecuta: instalar_dependencias_windows.bat
    echo     4. Ejecuta: compilar_windows.bat
    pause
    exit /b 1
)

echo %VENV_BASE_EXE% | find /i "\WindowsApps\" >\\.\NUL
if not errorlevel 1 (
    echo.
    echo [ERROR] El venv se creo con Python del Microsoft Store:
    echo   %VENV_BASE_EXE%
    echo.
    echo   Compila con Python 3.12 x64 oficial de python.org.
    echo.
    echo   Pasos:
    echo     1. Instala Python 3.12 x64 desde: https://www.python.org/downloads/windows/
    echo     2. Borra la carpeta venv\
    echo     3. Ejecuta: instalar_dependencias_windows.bat
    echo     4. Ejecuta: compilar_windows.bat
    pause
    exit /b 1
)

:: ── 2. Instalar PyInstaller si falta ─────────────────────────
echo.
echo [*] Verificando PyInstaller...

if not exist "%VENV_PYINSTALLER%" (
    echo   Instalando PyInstaller...
    "%VENV_PYTHON%" -m pip install pyinstaller --quiet
    if errorlevel 1 (
        echo [ERROR] No se pudo instalar PyInstaller.
        pause
        exit /b 1
    )
)
echo   OK - PyInstaller disponible.

"%VENV_PYTHON%" -c "import serial" >\\.\NUL 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Falta pyserial ^(modulo 'serial'^) en el venv.
    echo   Ejecuta: instalar_dependencias_windows.bat
    pause
    exit /b 1
)

:: ── 3. Limpiar builds anteriores ─────────────────────────────
echo.
echo [*] Limpiando builds anteriores...

:: PowerShell maneja symlinks/junctions que rmdir no puede borrar
powershell -command "if (Test-Path 'build') { Remove-Item -Path 'build' -Recurse -Force -ErrorAction SilentlyContinue }" 2>\\.\NUL
powershell -command "if (Test-Path 'dist')  { Remove-Item -Path 'dist'  -Recurse -Force -ErrorAction SilentlyContinue }" 2>\\.\NUL
 

:: Fallback
if exist "%SCRIPT_DIR%\build\" rmdir /s /q "%SCRIPT_DIR%\build" 2>\\.\NUL
if exist "%SCRIPT_DIR%\dist\"  rmdir /s /q "%SCRIPT_DIR%\dist"  2>\\.\NUL

echo   OK - carpetas limpias.

:: Eliminar .spec de otra plataforma
if exist "%SCRIPT_DIR%\%APP_NAME%.spec" (
    del /f /q "%SCRIPT_DIR%\%APP_NAME%.spec"
    echo   OK - .spec anterior eliminado
)

:: ── 4. Detectar icono ────────────────────────────────────────
set "ICON_FLAG="
if exist "%SCRIPT_DIR%\assets\AppIcon.ico" (
    set "ICON_FLAG=--icon=%SCRIPT_DIR%\assets\AppIcon.ico"
    echo   Icono: assets\AppIcon.ico
)

:: ── 5. Detectar assets ───────────────────────────────────────
set "ASSETS_FLAG="
if exist "%SCRIPT_DIR%\assets\" (
    set "ASSETS_FLAG=--add-data=%SCRIPT_DIR%\assets;assets"
    echo   Assets: assets\ incluido
)

:: ── 6. Compilar ──────────────────────────────────────────────
echo.
echo [*] Compilando con PyInstaller --onefile...
echo   ^(puede tardar 5-10 minutos^)
echo.

"%VENV_PYINSTALLER%" ^
    --onefile ^
    --windowed ^
    --noconfirm ^
    --name "%APP_NAME%" ^
    %ICON_FLAG% ^
    %ASSETS_FLAG% ^
    --exclude-module tkinter ^
    --exclude-module matplotlib ^
    --exclude-module numpy ^
    --exclude-module pandas ^
    --exclude-module scipy ^
    --exclude-module IPython ^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6 ^
    --exclude-module rumps ^
    --collect-submodules server ^
    --collect-submodules core ^
    --collect-submodules pos ^
    --collect-submodules ui ^
    --collect-submodules config ^
    --hidden-import serial ^
    --hidden-import serial.tools ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import flask_cors ^
    --hidden-import jinja2 ^
    --hidden-import jinja2.ext ^
    --hidden-import werkzeug ^
    --hidden-import werkzeug.serving ^
    --collect-all flask_cors ^
    --collect-all requests ^
    --collect-all idna ^
    --collect-all certifi ^
    --collect-all charset_normalizer ^
    --hidden-import psutil ^
    --hidden-import psutil._psutil_common ^
    --hidden-import psutil._psutil_windows ^
    --collect-all transbank ^
    --collect-all plyer ^
app.py

if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la compilacion.
    echo   Revisa los errores de PyInstaller arriba.
    pause
    exit /b 1
)

:: ── 7. Verificar resultado ───────────────────────────────────
set "DIST_EXE=%SCRIPT_DIR%\dist\%APP_NAME%.exe"

if not exist "%DIST_EXE%" (
    echo.
    echo [ERROR] No se genero el ejecutable en:
    echo   %DIST_EXE%
    pause
    exit /b 1
)

:: ── 8. Post-build: solo .exe + .env + assets ─────────────────
echo.
echo [*] Preparando paquete portable...
set "DIST_DIR=%SCRIPT_DIR%\dist"

:: .env junto al .exe (lo edita el usuario de cada caja)
if exist "%SCRIPT_DIR%\.env" (
    copy /y "%SCRIPT_DIR%\.env" "%DIST_DIR%\.env" >\\.\NUL
    echo   OK - .env copiado

    :: Eliminar credenciales del .env distribuido — siempre vienen del codigo compilado
    echo   Limpiando credenciales del .env de distribucion...
    powershell -command "(Get-Content '%DIST_DIR%\.env') | Where-Object { $_ -notmatch '^\s*#?\s*API_AUTH_(USER|PASS|PASS_HASH)\s*=' } | ForEach-Object { if ($_ -match '^\s*MP_ACCESS_TOKEN\s*=') { 'MP_ACCESS_TOKEN=' } else { $_ } } | Set-Content '%DIST_DIR%\.env'"
    echo   OK - credenciales API eliminadas y MP_ACCESS_TOKEN vaciado en el .env distribuido
    echo        ^(el token de Mercado Pago se configura en cada caja desde el panel^)
)

:: assets
if exist "%SCRIPT_DIR%\assets\" (
    xcopy /e /y /q "%SCRIPT_DIR%\assets\" "%DIST_DIR%\assets\" >\\.\NUL
    echo   OK - assets\ copiado
)

echo.
echo ==============================================
echo   OK - Compilacion exitosa
echo.
echo   Contenido de dist\ (listo para distribuir):
echo     %APP_NAME%.exe   ^<-- doble clic para ejecutar
echo     .env              ^<-- editar con los datos de la caja
echo                           ^(credenciales NO incluidas, estan en el .exe^)
echo.
echo   El usuario solo necesita esas dos cosas.
echo   Puede copiar dist\ a cualquier carpeta o PC.
echo ==============================================
echo.
pause
endlocal

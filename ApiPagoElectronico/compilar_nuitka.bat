@echo off
echo ==========================================
echo Compilando con Nuitka...
echo ==========================================

python -m nuitka ^
--standalone ^
--onefile ^
--windows-disable-console ^
--enable-plugin=anti-bloat ^
--include-package=transbank ^
--include-package=serial ^
--include-package=flask ^
--include-package=plyer ^
--output-filename=ApiPagoElectronico.exe ^
app.py

echo.
echo Compilacion finalizada en dist\ApiPagoElectronico.exe
pause
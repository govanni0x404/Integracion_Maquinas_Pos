@echo off
echo ==========================================
echo Compilando POS Gateway...
echo ==========================================

REM Elimina carpetas de compilaciones anteriores
rmdir /s /q build
rmdir /s /q dist
del /q ApiPagoElectronico.spec 2>nul

REM Ejecuta PyInstaller sin analizar todos los módulos automáticamente
python -m PyInstaller --onefile --noconsole ^
--name "ApiPagoElectronico" ^
--add-data "C:/Users/SOPORTE/AppData/Local/Programs/Python/Python310/Lib/site-packages/plyer/platforms;plyer/platforms" ^
--add-data "C:/Users/SOPORTE/AppData/Local/Programs/Python/Python310/Lib/site-packages/transbank;transbank" ^
--exclude-module setuptools ^
--exclude-module pip ^
--exclude-module wheel ^
--noupx ^
app.py

echo.
echo ==========================================
echo Compilacion finalizada. Revisa la carpeta "dist"
echo ==========================================
pause
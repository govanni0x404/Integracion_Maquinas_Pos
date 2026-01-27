@echo off
echo ==========================================
echo Compilando POS Gateway...
echo ==========================================

REM Elimina carpetas de compilaciones anteriores
rmdir /s /q build
rmdir /s /q dist

REM Ejecuta PyInstaller con tus parámetros
pyinstaller --onefile --noconsole ^
--name "ApiPagoElectronico" ^
--add-data "C:/Users/SOPORTE/AppData/Local/Programs/Python/Python310/Lib/site-packages/plyer/platforms;plyer/platforms" ^
--hidden-import=transbank.POS.POSIntegrado ^
--collect-all transbank ^
app.py

echo.
echo ==========================================
echo Compilacion finalizada. Revisa la carpeta "dist"
echo ==========================================
pause
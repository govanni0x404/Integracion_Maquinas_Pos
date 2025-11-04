@echo off
echo ==========================================
echo Compilando POS Gateway...
echo ==========================================

REM Elimina carpetas de compilaciones anteriores
rmdir /s /q build
rmdir /s /q dist
del app.spec 2>nul

REM Ejecuta PyInstaller con tus parámetros
pyinstaller --onefile --noconsole ^
--name "ApiPagoElectronico" ^
--add-data "C:/Users/usuario/AppData/Local/Programs/Python/Python310/Lib/site-packages/plyer/platforms;plyer/platforms" ^
app.py

echo.
echo ==========================================
echo Compilacinn finalizada. Revisa la carpeta "dist"
echo ==========================================
pause

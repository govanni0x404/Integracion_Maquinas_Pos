@echo off
echo ==========================================
echo Compilando POS Gateway...
echo ==========================================

rmdir /s /q build
rmdir /s /q dist
del /q *.spec 2>nul

python -m PyInstaller ^
--onefile ^
--noconsole ^
--name "ApiPagoElectronico" ^
--exclude-module tkinter ^
--exclude-module matplotlib ^
--exclude-module numpy ^
--exclude-module pandas ^
--exclude-module scipy ^
--exclude-module IPython ^
--exclude-module notebook ^
--exclude-module PyQt5 ^
--exclude-module PyQt6 ^
app.py

echo.
echo ==========================================
echo Compilacion finalizada. Revisa la carpeta "dist"
echo ==========================================
pause
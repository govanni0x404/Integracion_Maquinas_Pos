@echo off
echo Deteniendo ApiPagoElectronico...
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq app.py" >nul 2>&1
taskkill /F /IM python.exe /FI "WINDOWTITLE eq app.py" >nul 2>&1
echo Detenido
pause
```

---

## Para distribuir:

1. **Crea un ZIP** con:
```
   ApiPagoElectronico/
   ├── app.py
   ├── pos/
   ├── server/
   ├── core/
   ├── config/
   ├── ui/
   ├── .env
   ├── requirements.txt
   ├── iniciar.bat
   └── detener.bat
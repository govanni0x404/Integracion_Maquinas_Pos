
Dependencias Python:

pip install python-dotenv requests flask flask-cors pystray pillow
pip install pyserial transbank-sdk
pip install pyinstaller

#pyinstaller --onefile --noconsole ApiPagoElectronico.py

pyinstaller --onefile --noconsole --add-data ".env.encrypted;." --hidden-import=cryptography.fernet ApiPagoElectronico.py
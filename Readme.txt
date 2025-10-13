
**************** Dependencias Server ****************

pip install flask requests
pip install gunicorn gevent
pip install redis

**************** Dependencias Agente ****************

pip install requests pyserial
pip install transbank-sdk
pip install transbank-pos-sdk

**************** Hacer Agente .exe ****************

pip install python-dotenv
pip install pyinstaller
pyinstaller --onefile --noconsole agent.py

** Es importante que el .exe este junto al archivo .env

**************** Instalar server.py ubuntu server **************** 

sudo apt-get install python3-venv
python3.5 -m venv venv
source venv/bin/activate
pip install flask==1.1.4 requests==2.28.2 python-dotenv==0.21.0
python3.5 -m pip install --upgrade "pip<21" "setuptools<45"
pip install "Flask<2" "requests<3" "python-dotenv<1"
python3.5 -c "import flask; print(flask.__version__)"

python3.5 server.py

**Es importante poner el server.py junto a su .env

# Si se corta el servicio solo se usa

source venv/bin/activate
python3.5 server.py

**************** Otra forma de correr server.py ubuntu server **************** 

nohup python3.5 server.py > server.log 2>&1 &

ver estado --> lsof -i :5000
ver ejecucion --> ps -ef | grep server.py

terminar proceso --> kill 4617

**************** Hacer servicio server.py ubuntu server **************** 

sudo nano /etc/systemd/system/api_server.service


[Unit]
Description=API Server Python 3.5
After=network.target

[Service]
# Ruta a tu entorno virtual y script
WorkingDirectory=/home/carlos/Escritorio/api_prueba
ExecStart=/home/carlos/Escritorio/api_prueba/venv/bin/python3.5 server.py
Restart=always
RestartSec=5

# Usuario que ejecutará el servicio
User=carlos

# Log directo a journalctl (también puedes seguir usando server.log si prefieres)
StandardOutput=append:/home/carlos/Escritorio/api_prueba/server.log
StandardError=append:/home/carlos/Escritorio/api_prueba/server.log

[Install]
WantedBy=multi-user.target


sudo systemctl daemon-reload
sudo systemctl start api_server
sudo systemctl status api_server  -> el que se usa mas
sudo systemctl enable api_server










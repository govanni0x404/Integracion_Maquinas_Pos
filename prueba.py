from transbank import POSIntegrado
from transbank.error.transbank_exception import TransbankException

PORT = "COM5"
BAUDRATE = 115200

try:
    POS = POSIntegrado()    
    POS.open_port(PORT, BAUDRATE)
    print("Puerto abierto correctamente")
    
    ultima = POS.last_sale()
    print("Última venta:", ultima)

except Exception as e:
    print("Error:", e)

finally:
    try:
        POS.close_port()
    except:
        pass

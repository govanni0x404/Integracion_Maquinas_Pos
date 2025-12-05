from transbank import POSIntegrado
from transbank.error.transbank_exception import TransbankException

PORT = "COM5"
BAUDRATE = 115200

try:
    POS = POSIntegrado()
    POS.open_port(PORT, BAUDRATE)

    respuesta = POS.refund(109)
    print("Respuesta anulación:", respuesta)

except Exception as e:
    print("Error:", e)

finally:
    try:
        POS.close_port()
    except:
        pass

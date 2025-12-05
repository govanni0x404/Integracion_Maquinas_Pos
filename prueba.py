from transbank import POSIntegrado
from transbank.error.transbank_exception import TransbankException

PORT = "COM5"

try:
    POS = POSIntegrado()
    POS.open_port(PORT)
    
    print(POS.details(False))

except Exception as e:
    print("Error:", e)

finally:
    try:
        POS.close_port()
    except:
        pass

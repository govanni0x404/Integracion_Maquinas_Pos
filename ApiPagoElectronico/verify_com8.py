import logging
import sys
import os

sys.path.append(os.getcwd())

from pos.pos_module import POSModule

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('ApiPagoElectronico')

def verify_com8():
    print("Testing detection SPECIFICALLY for COM8")
    pos = POSModule("COM8")
    results = pos.detect_ports()
    print(f"\nDetection results: {results}")
    
    if results.get('getnet') == 'COM8':
        print(f"SUCCESS: Getnet POS detected on COM8")
    else:
        print("FAILURE: Getnet POS NOT detected on COM8")

if __name__ == "__main__":
    verify_com8()

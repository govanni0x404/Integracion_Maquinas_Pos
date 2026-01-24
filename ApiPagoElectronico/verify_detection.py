import logging
import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

from pos.pos_module import POSModule
from config.settings import PUERTOS_COM

# Configure logging to see the debug messages
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('ApiPagoElectronico')

def verify_detection():
    print(f"Testing detection with preferred ports: {PUERTOS_COM}")
    pos = POSModule(PUERTOS_COM)
    results = pos.detect_ports()
    print(f"\nDetection results: {results}")
    
    if results.get('getnet'):
        print(f"SUCCESS: Getnet POS detected on {results['getnet']}")
    else:
        print("FAILURE: Getnet POS NOT detected")

if __name__ == "__main__":
    verify_detection()

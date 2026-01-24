import serial
import serial.tools.list_ports
import json
import hashlib
import time
from datetime import datetime

def probe_getnet(port_name):
    print(f"Probing {port_name}...")
    try:
        ser = serial.Serial(
            port=port_name,
            baudrate=115200,
            timeout=3,
            write_timeout=3
        )
        time.sleep(1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        command = {
            "Command": 106,
            "DateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        json_v = json.dumps(command, separators=(',', ':'))
        sign = hashlib.sha256(json_v.encode()).hexdigest().upper()
        
        message = {
            "JsonSerialized": json_v,
            "Sign": sign
        }
        
        # Try both with and without space in separators
        for sep in [ (',', ':'), (',', ': ') ]:
            msg_str = json.dumps(message, separators=sep) + '\r\n'
            print(f"  Sending with sep {sep}: {msg_str.strip()}")
            ser.write(msg_str.encode('utf-8'))
            ser.flush()
            
            start = time.time()
            buffer = ""
            while time.time() - start < 3:
                if ser.in_waiting:
                    data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data
                    print(f"  Received chunk: {data!r}")
                time.sleep(0.1)
            
            if buffer:
                print(f"  Final buffer for {sep}: {buffer!r}")
            else:
                print(f"  No response for {sep}")
        
        ser.close()
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    ports = [p.device for p in serial.tools.list_ports.comports()]
    print(f"Found ports: {ports}")
    for p in ports:
        probe_getnet(p)

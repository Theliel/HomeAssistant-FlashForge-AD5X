import argparse
import json
import os
import requests
import sys
import time
from flask import Flask, request, Response
from smartcard.System import readers

# --- CONFIGURACIÓN NTAG215 ---
PAGE_START_JSON = 50
TOTAL_BYTES_JSON = 256
PAGES_JSON = 64 
TOTAL_PAGES_NTAG215 = 135
FILAMENTS_DIR = "./filamentos"

app = Flask(__name__)

# --- BLOQUES ESTÁTICOS HOME ASSISTANT ---
HA_START_BLOCKS = [
    # 03: [0xE1, 0x11, 0x3E, 0x00], lo quitamos para no quemar el bit de version
    [0x03, 0x93, 0x91, 0x01], # 04
    [0x1F, 0x55, 0x02, 0x68], # 05
    [0x6F, 0x6D, 0x65, 0x2D], # 06
    [0x61, 0x73, 0x73, 0x69], # 07
    [0x73, 0x74, 0x61, 0x6E], # 08
    [0x74, 0x2E, 0x69, 0x6F], # 09
    [0x2F, 0x74, 0x61, 0x67]  # 0A: /tag
]

HA_END_BLOCKS = [
    [0x61, 0x6E, 0x64, 0x72], [0x6F, 0x69, 0x64, 0x2E], [0x63, 0x6F, 0x6D, 0x3A], # 0E, 0F, 10
    [0x70, 0x6B, 0x67, 0x69], [0x6F, 0x2E, 0x68, 0x6F], [0x6D, 0x65, 0x61, 0x73], # 11, 12, 13
    [0x73, 0x69, 0x73, 0x74], [0x61, 0x6E, 0x74, 0x2E], [0x63, 0x6F, 0x6D, 0x70], # 14, 15, 16
    [0x61, 0x6E, 0x69, 0x6F], [0x6E, 0x2E, 0x61, 0x6E], [0x64, 0x72, 0x6F, 0x69], # 17, 18, 19
    [0x64, 0x54, 0x0F, 0x2A], [0x61, 0x6E, 0x64, 0x72], [0x6F, 0x69, 0x64, 0x2E], # 1A, 1B, 1C
    [0x63, 0x6F, 0x6D, 0x3A], [0x70, 0x6B, 0x67, 0x69], [0x6F, 0x2E, 0x68, 0x6F], # 1D, 1E, 1F
    [0x6D, 0x65, 0x73, 0x73], [0x69, 0x73, 0x74, 0x61], [0x6E, 0x74, 0x2E, 0x63], # 20, 21, 22
    [0x63, 0x6F, 0x6D, 0x70], [0x61, 0x6E, 0x69, 0x6F], [0x6E, 0x2E, 0x61, 0x6E], # 23, 24, 25
    [0x64, 0x72, 0x6F, 0x69], [0x64, 0x2E, 0x6D, 0x69], [0x6E, 0x69, 0x6D, 0x61], # 26, 27, 28
    [0x6C, 0xFE, 0x00, 0x00] # 29
]

# --- FUNCIONES ---

def get_connection():
    try:
        r = readers()
        if not r: return None
        conn = r[0].createConnection()
        conn.connect()
        return conn
    except: return None

def save_local_filament(js_data):
    if not os.path.exists(FILAMENTS_DIR): os.makedirs(FILAMENTS_DIR)
    file_id = js_data.get("id", "unk").replace("/", "-")
    file_path = os.path.join(FILAMENTS_DIR, f"{file_id}.json")
    with open(file_path, "w") as f:
        json.dump(js_data, f, indent=4)
    return file_path

def read_raw_zone(conn):
    buffer = []
    try:
        for i in range(PAGE_START_JSON, PAGE_START_JSON + PAGES_JSON):
            res, sw1, _ = conn.transmit([0xFF, 0xB0, 0x00, i, 0x04])
            if sw1 == 0x90:
                buffer.extend(res)
            else:
                return "READ_ERROR" # Fallo de lectura física
        
        raw_data = bytes(buffer).rstrip(b'\x00')
        
        # SI EL TAG ESTÁ VACÍO (Solo ceros)
        if not raw_data:
            return "EMPTY"
            
        return json.loads(raw_data.decode('utf-8'))
    except Exception as e:
        print(f"[-] Excepción en lectura: {e}")
        return "RESET_ERROR"

def write_page(conn, page, data):
    try:
        cmd = [0xFF, 0xD6, 0x00, page, 0x04] + list(data)
        _, sw1, _ = conn.transmit(cmd)
        return sw1 == 0x90
    except Exception as e:
        print(f"[-] Error escribiendo página {page}: {e}")
        return False

def format_ha_blocks(id_str):
    full_id = f"/{id_str}".encode('utf-8')
    padded_id = full_id.ljust(9, b'\x00')
    return [list(padded_id[0:4]), list(padded_id[4:8]), [padded_id[8], 0x14, 0x0F, 0x22]]

def full_write_process(conn, data_dict, write_ha=False):
    if write_ha:
        print("[*] Escribiendo NDEF HA...")
        for i, b in enumerate(HA_START_BLOCKS): write_page(conn, 4+i, b)
        id_b = format_ha_blocks(data_dict.get("id", "unknown"))
        for i, b in enumerate(id_b): write_page(conn, 11+i, b)
        for i, b in enumerate(HA_END_BLOCKS): write_page(conn, 14+i, b)
    
    print("[*] Escribiendo JSON...")
    j_str = json.dumps(data_dict, separators=(',', ':'))
    j_bytes = j_str.encode('utf-8').ljust(TOTAL_BYTES_JSON, b'\x00')
    if len(j_bytes) > TOTAL_BYTES_JSON: return False, "Exceso de tamaño"
    
    for i in range(PAGES_JSON):
        if not write_page(conn, PAGE_START_JSON + i, list(j_bytes[i*4:(i*4)+4])):
            return False, "Fallo escritura"
    save_local_filament(data_dict)
    return True, "OK"

# --- SERVIDOR ---

@app.route('/read', methods=['POST'])
def handle_read_request():
    start = time.time()
    while time.time() - start < 10:
        conn = get_connection()
        if conn:
            js = read_raw_zone(conn)
            
            # CASO 1: Tag vacío (formateado o virgen)
            if js == "EMPTY":
                print("[!] Tag detectado pero está vacío.")
                return Response(json.dumps({"status": "empty"}), mimetype='application/json'), 200

            # CASO 2: Error de comunicación (reset) - Reintentamos
            if js in ["RESET_ERROR", "READ_ERROR"]:
                try: conn.disconnect()
                except: pass
                time.sleep(0.5)
                continue
                
            # CASO 3: JSON válido encontrado
            if isinstance(js, dict):
                save_local_filament(js)
                print(f"[+] Datos válidos: {js.get('id')}")
                return Response(json.dumps(js, indent=4), mimetype='application/json'), 200
            
            # Si llegamos aquí con algo raro, desconectamos y seguimos bucle
            try: conn.disconnect()
            except: pass

        time.sleep(0.5)
    
    # CASO 4: No se detectó ningún tag en 10 segundos
    return Response(json.dumps({"status": "timeout"}), status=408, mimetype='application/json')

@app.route('/write', methods=['POST'])
def handle_write_request():
    data = request.json
    if not data: return Response(json.dumps({"status": "error"}), status=400, mimetype='application/json')
    start = time.time()
    while time.time() - start < 10:
        conn = get_connection()
        if conn:
            success, msg = full_write_process(conn, data, write_ha=True)
            if success: return Response(json.dumps({"status": "success"}), status=200, mimetype='application/json')
            return Response(json.dumps({"status": "error", "message": msg}), status=500, mimetype='application/json')
        time.sleep(0.5)
    return Response(json.dumps({"status": "timeout"}), status=408, mimetype='application/json')


@app.route('/clear', methods=['POST'])
def handle_clear_request():
    print("[*] Orden de borrado (formateo) recibida...")
    start = time.time()
    while time.time() - start < 10:
        conn = get_connection()
        if conn:
            print("[*] Borrando páginas 0x03 a 0x81...")
            # De 3 a 129 decimal (0x81)
            for page in range(4, 130):
                if not write_page(conn, page, [0x00, 0x00, 0x00, 0x00]):
                    return Response(json.dumps({"status": "error", "message": f"Fallo en página {page}"}), status=500, mimetype='application/json')
            print("[+] Tag formateado con éxito.")
            return Response(json.dumps({"status": "success"}), status=200, mimetype='application/json')
        time.sleep(0.5)
    return Response(json.dumps({"status": "timeout"}), status=408, mimetype='application/json')


# --- MAIN ---

def main():
    parser = argparse.ArgumentParser(description="SaraTagBridge, by Theliel | Version:v1.0.7")
    parser.add_argument("-l", "--read", help='Lee y almacena el contenido del Tag', action="store_true")
    parser.add_argument("-e", "--write", help='Escribe al Tag el archivo especificado', type=str)
    parser.add_argument("-ha", "--homeassistant", help='Se escriben las páginas propias de HA', action="store_true")
    parser.add_argument("-d", "--debug", help='Hace un dump completo del tag', action="store_true")
    parser.add_argument("-listen", "--listen", help='Modo Servidor, espera /write /read /clear', type=int)
    parser.add_argument("-end", "--endpoint", help='Si se usa -l, envía los datos a un endpoint especificado, con -e los recibe', type=str)
    parser.add_argument("-slot", "--slot", help='Especifica el slot a tener en cuenta cuando se usa -end', type=int)
    parser.add_argument("-v", "--version", action="version", version="v1.0.7")
    args = parser.parse_args()

    if args.listen:
        app.run(host='0.0.0.0', port=args.listen)
        return

    conn = get_connection()
    if not conn: print("[-] No lector"); sys.exit(1)

    if len(sys.argv) == 1:
        js = read_raw_zone(conn)
        if js: print(json.dumps(js, indent=4))
        return

    if args.debug:
        dump = bytearray()
        for i in range(TOTAL_PAGES_NTAG215):
            res, sw1, _ = conn.transmit([0xFF, 0xB0, 0x00, i, 0x04])
            dump.extend(res if sw1 == 0x90 else [0x00]*4)
        with open("dump.bin", "wb") as f: f.write(dump)
        print("[+] Dump OK")
        return

    if args.write:
        if os.path.exists(args.write):
            with open(args.write, 'r') as f: content = json.load(f)
            success, msg = full_write_process(conn, content, write_ha=args.homeassistant)
            print(f"[+] {msg}")

    elif args.read:
        js = read_raw_zone(conn)
        if js:
            print(json.dumps(js, indent=4))
            save_local_filament(js)
            if args.endpoint:
                h = {'X-NFC-Slot': str(args.slot)} if args.slot else {}
                requests.post(args.endpoint, json=js, headers=h)

if __name__ == "__main__":
    main()

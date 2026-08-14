"""
rsu3_fog.py — طبقة الضباب (Fog Layer)
الأدوات: Python 3.8 + paho-mqtt
الوظيفة: UDP:7001 ← CAM_06 → Edge Aggregation → MQTT:traffic/RSU_03_MINI
البروتوكول: MQTT over TCP/LTE (رابط RSU → Server)
"""
import json, socket, threading, time, os, logging
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [FOG-RSU_03] %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

LISTEN_PORT  = 7001
MQTT_BROKER  = "localhost"
MQTT_PORT    = 1883
MQTT_TOPIC   = "traffic/RSU_03_MINI"   # منفصل عن النظام الكامل
MQTT_QOS     = 1
INTERVAL     = 5.0
STALE        = 30.0
RSU_COORD    = {"lat": 52.5195, "lon": 13.4040}
RSU_JSON_DIR = r"C:\TrafficProject\data\rsu_json"

_buffer    = {}
_buf_lock  = threading.Lock()
_last_recv = {}
_stats     = {"rx":0, "tx":0, "bytes_rx":0}


def udp_listener(stop: threading.Event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.1)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    log.info(f"UDP ← :{LISTEN_PORT} جاهز")

    while not stop.is_set():
        try:
            data, _ = sock.recvfrom(4096)
            p       = json.loads(data.decode())
            cam_id  = p.get("camera_id","")
            if cam_id:
                with _buf_lock:
                    _buffer[cam_id] = p
                _last_recv[cam_id] = time.time()
                _stats["rx"] += 1
                _stats["bytes_rx"] += len(data)
                log.info(f"← {cam_id} "
                         f"count={p.get('vehicle_count',0):3d} | "
                         f"speed={p.get('avg_speed_kmh',0.0):5.1f} km/h")
        except socket.timeout:
            continue
        except json.JSONDecodeError:
            pass
    sock.close()


def aggregate():
    now = time.time()
    with _buf_lock:
        snap = dict(_buffer)
    fresh = [v for k,v in snap.items()
             if now - _last_recv.get(k, 0) <= STALE]
    if not fresh:
        return None
    return {
        "rsu_id"         : "RSU_03_MINI",
        "timestamp"      : datetime.now(timezone.utc).isoformat(),
        "vehicle_count"  : sum(r.get("vehicle_count",0) for r in fresh),
        "avg_speed_kmh"  : round(
            sum(r.get("avg_speed_kmh",0.0) for r in fresh)/len(fresh), 2),
        "traffic_density": round(
            sum(r.get("density_veh_km",0.0) for r in fresh)/len(fresh), 3),
        "latitude"       : RSU_COORD["lat"],
        "longitude"      : RSU_COORD["lon"],
        "cameras_active" : [r.get("camera_id") for r in fresh],
        "network_stats"  : {
            "packets_rx": _stats["rx"],
            "bytes_rx"  : _stats["bytes_rx"],
        },
        "layer"          : "fog",
    }


def save_local(p):
    os.makedirs(RSU_JSON_DIR, exist_ok=True)
    path = os.path.join(RSU_JSON_DIR, "RSU_03_MINI.json")
    tmp  = path + ".tmp"
    with open(tmp,'w') as f: json.dump(p,f,indent=2)
    try: os.replace(tmp,path)
    except: pass


def run():
    log.info("═"*55)
    log.info("طبقة الضباب — RSU_03 (Fog Layer)")
    log.info(f"Input : UDP:{LISTEN_PORT} ← CAM_06 (WiFi)")
    log.info(f"Output: MQTT:{MQTT_TOPIC} (LTE/4G)")
    log.info("═"*55)

    client = mqtt.Client(client_id="RSU_03_MINI_FOG")
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    log.info(f"MQTT متصل: {MQTT_BROKER}:{MQTT_PORT}")

    stop   = threading.Event()
    t      = threading.Thread(target=udp_listener, args=(stop,), daemon=True)
    t.start()

    try:
        while True:
            time.sleep(INTERVAL)
            p = aggregate()
            if not p:
                continue
            msg    = json.dumps(p, ensure_ascii=False)
            result = client.publish(MQTT_TOPIC, msg, qos=MQTT_QOS)
            _stats["tx"] += 1
            status = "✓" if result.rc==0 else f"✗ rc={result.rc}"
            log.info(
                f"→ MQTT {MQTT_TOPIC} "
                f"count={p['vehicle_count']:3d} | "
                f"speed={p['avg_speed_kmh']:5.1f} km/h | "
                f"density={p['traffic_density']:6.2f} {status}"
            )
            save_local(p)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        t.join(timeout=2)
        client.loop_stop()
        client.disconnect()
        log.info("RSU_03 متوقف.")


if __name__ == "__main__":
    run()
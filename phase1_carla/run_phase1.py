"""
run_phase1.py — Memory-Based Network Streaming Edition
======================================================
Architecture:
    Camera → UDP Socket → RSU Listener (simulates WiFi 802.11n)

لماذا UDP وليس TCP؟
    UDP يُحاكي طبيعة WiFi اللاسلكية: connectionless، broadcast،
    لا overhead للاتصال. فقدان الحزمة مقبول — الـ RSU يستخدم
    أحدث قراءة متاحة. هذا مطابق لسلوك IEEE 802.11n في البيئة الحضرية.

لماذا لا Disk I/O في المسار الرئيسي؟
    world.tick() يجب أن يُكمَل في 50ms (عند 20 FPS).
    أي عملية قرص (فتح ملف، كتابة، إغلاق) تستغرق 1-10ms
    تُراكِم تأخيراً يُسبب desync بين CARLA والكود.
    UDP sendto() تستغرق < 0.1ms — لا تأثير على الـ simulation loop.
"""

import carla
import json
import os
import queue
import socket
import threading
import time
import numpy as np
import cv2
from datetime import datetime, timezone
from traffic_analyzer import TrafficAnalyzer
from camera_config import CAMERAS, RSU_CLUSTERS, RSU_UDP_PORTS
# عداد تسلسلي لكل كاميرا — يُمكِّن كشف فقدان الحزم في RSU
_seq_counters: dict = {}   # cam_id → int

# ── Trace Logger ──────────────────────────────────────────────────
# يُسجِّل كل حزمة UDP حقيقية لتغذية NS-3 لاحقاً
TRACE_FILE     = r"C:\TrafficProject\data\ns3_results\real_traffic_trace.csv"
_trace_lock    = threading.Lock()
_trace_started = False

def init_trace_file():
    """ينشئ ملف الـ trace عند أول إرسال"""
    global _trace_started
    os.makedirs(os.path.dirname(TRACE_FILE), exist_ok=True)
    with open(TRACE_FILE, 'w') as f:
        f.write("wall_timestamp,cam_id,rsu_id,payload_bytes,seq\n")
    _trace_started = True

def log_trace(cam_id: str, rsu_id: str, payload_bytes: int, seq: int):
    """
    يُسجِّل كل حزمة في daemon thread — لا يُعيق world.tick()
    wall_timestamp: Unix timestamp بدقة ميكروثانية
    """
    def _write():
        with _trace_lock:
            with open(TRACE_FILE, 'a') as f:
                f.write(f"{time.time():.6f},"
                        f"{cam_id},{rsu_id},"
                        f"{payload_bytes},{seq}\n")
    threading.Thread(target=_write, daemon=True).start()

# ─────────────────────────────────────────────────────────────────────────────
# إعداد
# ─────────────────────────────────────────────────────────────────────────────
CARLA_HOST  = 'localhost'
CARLA_PORT  = 2000
CARLA_FPS   = 20.0
SPAWN_COUNT = 50
JSON_DIR    = r"C:\TrafficProject\data\camera_json"

# عندما True: يحفظ نسخة على القرص في thread خلفي (للتشخيص فقط)
# في الإنتاج يجب أن يكون False لضمان عدم تأثر world.tick()
SAVE_LOCAL_BACKUP = False

# بناء جدول عكسي: cam_id → rsu_id
CAM_TO_RSU: dict = {}
for _rsu_id, _cams in RSU_CLUSTERS.items():
    for _cam in _cams:
        CAM_TO_RSU[_cam] = _rsu_id

# ─────────────────────────────────────────────────────────────────────────────
# UDP Socket مشترك لجميع الكاميرات
# socket واحد يكفي: UDP لا يحتاج connection منفصل لكل RSU
# ─────────────────────────────────────────────────────────────────────────────
_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)


def send_camera_data(cam_id: str, metrics: dict, rsu_id: str) -> None:
    """
    إرسال حزمة UDP مع:
      - sequence_number: لكشف الحزم الضائعة في الـ RSU
      - send_timestamp : لحساب الـ RTT/Latency الحقيقي

    الـ RSU تُقارن الـ sequence_numbers الواردة بالمتوقعة
    وتحسب PDR = received / expected × 100
    وLatency = recv_timestamp - send_timestamp
    """
    port = RSU_UDP_PORTS.get(rsu_id)
    if port is None:
        return

    # زيادة العداد التسلسلي لهذه الكاميرا
    _seq_counters[cam_id] = _seq_counters.get(cam_id, 0) + 1

    payload = {
        "camera_id"      : cam_id,
        "rsu_id"         : rsu_id,
        # الطابع الزمني بدقة ميكروثانية — ضروري لقياس الـ Latency
        "send_timestamp" : datetime.now(timezone.utc).timestamp(),
        "seq"            : _seq_counters[cam_id],
        "vehicle_count"  : metrics.get("vehicle_count",  0),
        "avg_speed_kmh"  : metrics.get("avg_speed_kmh",  0.0),
        "density_veh_km" : metrics.get("density_veh_km", 0.0),
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        _udp_sock.sendto(data, ("127.0.0.1", port))

      # تسجيل الـ trace في الخلفية
        global _trace_started
        if not _trace_started:
            init_trace_file()
        log_trace(cam_id, rsu_id, len(data), _seq_counters[cam_id])
    except OSError as e:
        print(f"[UDP][WARN] {cam_id}→{rsu_id} port={port}: {e}")


def save_json_async(cam_id: str, metrics: dict) -> None:
    """
    حفظ اختياري غير متزامن على القرص — للتشخيص فقط.
    يعمل في daemon thread منفصل حتى لا يُعيق world.tick().
    لا يُشغَّل إلا إذا كان SAVE_LOCAL_BACKUP = True.
    """
    if not SAVE_LOCAL_BACKUP:
        return

    def _write():
        os.makedirs(JSON_DIR, exist_ok=True)
        payload = {
            "camera_id"      : cam_id,
            "timestamp"      : datetime.now(timezone.utc).isoformat(),
            "vehicle_count"  : metrics.get("vehicle_count",  0),
            "avg_speed_kmh"  : metrics.get("avg_speed_kmh",  0.0),
            "density_veh_km" : metrics.get("density_veh_km", 0.0),
        }
        path = os.path.join(JSON_DIR, f"{cam_id}.json")
        tmp  = path + ".tmp"
        try:
            with open(tmp, 'w') as f:
                json.dump(payload, f, indent=2)
            for _ in range(5):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    time.sleep(0.01)
        except Exception as e:
            print(f"[BACKUP][WARN] {cam_id}: {e}")

    threading.Thread(target=_write, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# CARLA
# ─────────────────────────────────────────────────────────────────────────────
def find_junctions(world):
    wps = world.get_map().generate_waypoints(distance=2.0)
    junctions = set()
    for wp in wps:
        if wp.is_junction:
            x = round(wp.transform.location.x / 10) * 10
            y = round(wp.transform.location.y / 10) * 10
            junctions.add((x, y))
    print("\n[Junctions] Available in Town10:")
    for j in sorted(junctions)[:20]:
        print(f"  x={j[0]:6.0f}, y={j[1]:6.0f}")
    print()


def spawn_vehicles(world, count):
    bp_lib  = world.get_blueprint_library()
    v_bps   = bp_lib.filter('vehicle.*')
    sp      = world.get_map().get_spawn_points()
    actors  = []
    for i in range(min(count, len(sp))):
        bp = np.random.choice(v_bps)
        a  = world.try_spawn_actor(bp, sp[i])
        if a:
            a.set_autopilot(True)
            actors.append(a)
    print(f"[Phase1] Spawned {len(actors)} vehicles")
    return actors


def spawn_cameras(world):
    bp_lib = world.get_blueprint_library()
    cam_bp = bp_lib.find('sensor.camera.rgb')
    cam_bp.set_attribute('image_size_x', '1280')
    cam_bp.set_attribute('image_size_y', '720')
    cam_bp.set_attribute('fov', '60')

    actors = []
    queues = {}

    for cfg in CAMERAS:
        t = carla.Transform(
            carla.Location(x=cfg['x'], y=cfg['y'], z=cfg['z']),
            carla.Rotation(pitch=cfg['pitch'], yaw=cfg['yaw'], roll=cfg['roll'])
        )
        cid = cfg['id']
        q   = queue.Queue(maxsize=2)

        def make_cb(cam_id, cam_q):
            def cb(img):
                arr = np.frombuffer(img.raw_data, dtype=np.uint8)
                arr = arr.reshape((img.height, img.width, 4))
                if not cam_q.full():
                    cam_q.put(arr[:, :, :3].copy())
            return cb

        actor = world.spawn_actor(cam_bp, t)
        actor.listen(make_cb(cid, q))
        actors.append(actor)
        queues[cid] = q
        rsu = CAM_TO_RSU.get(cid, '?')
        port = RSU_UDP_PORTS.get(rsu, '?')
        print(f"[Phase1] Camera spawned: {cid} → {rsu} (UDP:{port})")

    return actors, queues


def run():
    print("[Phase1] Connecting to CARLA...")
    client = carla.Client(CARLA_HOST, CARLA_PORT)
    client.set_timeout(15.0)
    world  = client.get_world()
    print(f"[Phase1] Connected. Map: {world.get_map().name}")

    find_junctions(world)

    settings = world.get_settings()
    settings.synchronous_mode    = True
    settings.fixed_delta_seconds = 1.0 / CARLA_FPS
    world.apply_settings(settings)

    print(f"[Phase1] Sync mode @ {CARLA_FPS} FPS")
    print(f"[Phase1] IPC: UDP Sockets — NO disk I/O in main loop")
    print(f"[Phase1] Backup: {'async disk' if SAVE_LOCAL_BACKUP else 'disabled'}\n")

    vehicles           = spawn_vehicles(world, SPAWN_COUNT)
    cam_actors, queues = spawn_cameras(world)
    analyzers          = {cfg['id']: TrafficAnalyzer(cfg['id'])
                          for cfg in CAMERAS}

    print("[Phase1] Pipeline running. Q to stop.\n")

    try:
        while True:
            world.tick()   # ← لا disk I/O هنا أبداً

            for cfg in CAMERAS:
                cid    = cfg['id']
                rsu_id = CAM_TO_RSU.get(cid, '')
                try:
                    frame = queues[cid].get(timeout=0.05)
                except queue.Empty:
                    continue

                metrics = analyzers[cid].process_frame(frame)

                # المسار الأساسي: UDP → RSU (في الذاكرة، < 0.1ms)
                send_camera_data(cid, metrics, rsu_id)

                # المسار الاختياري: حفظ قرص في الخلفية (لا يُعيق)
                save_json_async(cid, metrics)

                print(f"[{cid}→{rsu_id}] "
                      f"count={metrics['vehicle_count']:3d} | "
                      f"speed={metrics['avg_speed_kmh']:5.1f} km/h | "
                      f"density={metrics['density_veh_km']:6.1f} veh/km")

                small = cv2.resize(metrics['annotated'], (640, 360))
                cv2.imshow(cid, small)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("[Phase1] Q pressed — stopping.")
                break

    except KeyboardInterrupt:
        print("\n[Phase1] Interrupted.")

    finally:
        _udp_sock.close()
        for a in cam_actors:
            if a.is_alive: a.destroy()
        for a in vehicles:
            if a.is_alive: a.destroy()
        settings.synchronous_mode    = False
        settings.fixed_delta_seconds = None
        world.apply_settings(settings)
        cv2.destroyAllWindows()
        print("[Phase1] Done.")


if __name__ == '__main__':
    run()
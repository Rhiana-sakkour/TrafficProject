"""
cam6_edge.py — طبقة الحافة (Edge Layer)
الأدوات: CARLA 0.9.13 + OpenCV 4.6.0 + YOLOv8n + SORT
الوظيفة: CAM_06 تلتقط الإطارات → YOLOv8+SORT → UDP:7001
البروتوكول: WiFi 802.11n (مُحاكى في NS-3 عبر منفذ 7001)
"""
import sys
sys.path.insert(0, r"C:\TrafficProject\phase1_carla")

import carla, json, queue, socket, time, threading
import numpy as np, cv2
from datetime import datetime, timezone
from traffic_analyzer import TrafficAnalyzer
from camera_config   import CAMERAS

#  إعدادات 
CARLA_HOST  = 'localhost'
CARLA_PORT  = 2000
CARLA_FPS   = 20.0
CAM_ID      = "CAM_06"
RSU_ID      = "RSU_03"
EDGE_PORT   = 7001        # منفذ WiFi→RSU
NS3_PORT    = 7010        # منفذ إشعار NS-3 (WSL2)
NS3_HOST    = "127.0.0.1"

# إحداثيات CAM_06
_cfg = next((c for c in CAMERAS if c['id'] == CAM_ID), None)

# socket UDP مشترك
_sock    = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_seq_num = 0

def send_to_rsu(metrics: dict):
    """إرسال بيانات CAM_06 عبر UDP (يُمثِّل WiFi 802.11n)"""
    global _seq_num
    _seq_num += 1
    payload = {
        "camera_id"      : CAM_ID,
        "rsu_id"         : RSU_ID,
        "send_timestamp" : datetime.now(timezone.utc).timestamp(),
        "seq"            : _seq_num,
        "vehicle_count"  : metrics.get("vehicle_count",  0),
        "avg_speed_kmh"  : metrics.get("avg_speed_kmh",  0.0),
        "density_veh_km" : metrics.get("density_veh_km", 0.0),
    }
    data = json.dumps(payload).encode("utf-8")
    # إرسال للـ RSU
    _sock.sendto(data, ("127.0.0.1", EDGE_PORT))
    # إشعار NS-3 (WSL2) بحجم الحزمة الفعلي للمحاكاة
    ns3_notif = json.dumps({
        "event"  : "wifi_tx",
        "size"   : len(data),
        "seq"    : _seq_num,
        "ts"     : payload["send_timestamp"],
    }).encode()
    try:
        _sock.sendto(ns3_notif, (NS3_HOST, NS3_PORT))
    except OSError:
        pass  # NS-3 قد لا يعمل دائماً

    print(f"[CAM_06→WiFi] seq={_seq_num:4d} | "
          f"count={payload['vehicle_count']:3d} | "
          f"speed={payload['avg_speed_kmh']:5.1f} km/h | "
          f"{len(data)}B → :7001")


def run():
    if _cfg is None:
        print("ERROR: CAM_06 not in camera_config.py"); return

    print("═"*55)
    print("  edge layer— CAM_06 (CARLA + YOLOv8 + SORT)")
    print(f"  location: ({_cfg['x']}, {_cfg['y']}, {_cfg['z']})")
    print(f"  protocol: WiFi 802.11n → UDP:{EDGE_PORT}")
    print("═"*55)

    client = carla.Client(CARLA_HOST, CARLA_PORT)
    client.set_timeout(10.0)
    world  = client.get_world()
    print(f"[Edge] CARLA connect: {world.get_map().name}")

    bp = world.get_blueprint_library().find('sensor.camera.rgb')
    bp.set_attribute('image_size_x', '1280')
    bp.set_attribute('image_size_y', '720')
    bp.set_attribute('fov', '110')

    t = carla.Transform(
        carla.Location(x=_cfg['x'], y=_cfg['y'], z=_cfg['z']),
        carla.Rotation(pitch=_cfg['pitch'], yaw=_cfg['yaw'], roll=0.0)
    )

    q = queue.Queue(maxsize=2)
    actor = world.spawn_actor(bp, t)
    actor.listen(lambda img: q.put(
        np.frombuffer(img.raw_data, dtype=np.uint8)
        .reshape((img.height, img.width, 4))[:,:,:3].copy()
    ) if not q.full() else None)

    # تشغيل المحليات على 20 مركبة إضافية إذا كانت الخارطة فارغة
    sp      = world.get_map().get_spawn_points()
    bp_lib  = world.get_blueprint_library()
    v_actors = []
    for i in range(min(20, len(sp))):
        a = world.try_spawn_actor(np.random.choice(bp_lib.filter('vehicle.*')), sp[i])
        if a:
            a.set_autopilot(True)
            v_actors.append(a)
    print(f"[Edge] {len(v_actors)} مركبة إضافية")

    analyzer = TrafficAnalyzer(CAM_ID)
    print("[Edge] تشغيل... اضغط Q للإيقاف\n")

    try:
        while True:
            try:
                frame = q.get(timeout=0.1)
            except queue.Empty:
                continue

            metrics = analyzer.process_frame(frame)
            send_to_rsu(metrics)

            # عرض النافذة مع تسمية الطبقة
            small = cv2.resize(metrics['annotated'], (800, 450))
            cv2.putText(small, "EDGE LAYER — CAM_06 | WiFi 802.11n",
                        (10, 430), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 255), 2)
            cv2.imshow("Edge: CAM_06 → RSU_03", small)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        pass
    finally:
        actor.destroy()
        for a in v_actors: a.destroy()
        _sock.close()
        cv2.destroyAllWindows()
        print("[Edge] متوقف.")


if __name__ == "__main__":
    run()
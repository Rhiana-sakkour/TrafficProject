"""
rsu_aggregation.py — Memory-Based Network Streaming Edition
==========================================================
Architecture:
    Camera ──UDP──→ [UDP Listener Thread per RSU] ──→ In-Memory Buffer
    [Aggregation Loop] reads buffer every 5s ──MQTT──→ Central Server

لا قراءة ملفات في المسار الرئيسي.
كل بيانات الكاميرات تتدفق عبر الذاكرة (RAM buffers).

Threading Model:
    Thread-1 (main)     : حلقة التجميع + MQTT publish
    Thread-2 (RSU_01)   : UDP listener على منفذ 5001
    Thread-3 (RSU_02)   : UDP listener على منفذ 5002
    Thread-4 (RSU_03)   : UDP listener على منفذ 5003
    Thread-5+ (daemon)  : نسخ القرص الاختيارية
"""
from __future__ import annotations
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from camera_config import RSU_CLUSTERS, RSU_UDP_PORTS, RSU_COORDINATES

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ثوابت
# ─────────────────────────────────────────────────────────────────────────────
MQTT_BROKER        = "localhost"
MQTT_PORT          = 1883
MQTT_QOS           = 1
MQTT_TOPIC_PREFIX  = "traffic"
BROADCAST_INTERVAL = 5.0
UDP_BUFFER_SIZE    = 4096
UDP_TIMEOUT        = 0.1
STALE_THRESHOLD    = 30.0
SAVE_RSU_BACKUP    = True
RSU_JSON_DIR       = r"C:\TrafficProject\data\rsu_json"

# ── RSU Trace Logger (لـ NS-3 LTE Trace-Driven) ──────────────────
RSU_TRACE_FILE    = r"C:\TrafficProject\data\ns3_results\real_rsu_trace.csv"
_rsu_trace_lock   = threading.Lock()
_rsu_trace_init   = False
_rsu_seq_counters = {}   # rsu_id → int

def _init_rsu_trace():
    global _rsu_trace_init
    os.makedirs(os.path.dirname(RSU_TRACE_FILE), exist_ok=True)
    with open(RSU_TRACE_FILE, 'w') as f:
        f.write("wall_timestamp,rsu_id,payload_bytes,seq\n")
    _rsu_trace_init = True

def log_rsu_trace(rsu_id: str, payload_bytes: int):
    """
    يُسجِّل كل إرسال RSU → Server في daemon thread.
    wall_timestamp: Unix timestamp بدقة ميكروثانية
    seq: رقم تسلسلي لكشف الفقد لاحقاً في NS-3
    """
    global _rsu_trace_init
    if not _rsu_trace_init:
        _init_rsu_trace()

    _rsu_seq_counters[rsu_id] = _rsu_seq_counters.get(rsu_id, 0) + 1
    seq = _rsu_seq_counters[rsu_id]
    ts  = time.time()

    def _write():
        with _rsu_trace_lock:
            with open(RSU_TRACE_FILE, 'a') as f:
                f.write(f"{ts:.6f},{rsu_id},{payload_bytes},{seq}\n")

    threading.Thread(target=_write, daemon=True).start()

# ─────────────────────────────────────────────────────────────────────────────
# إحصاءات الشبكة الحقيقية — تُحدَّث عند كل حزمة واردة
# ─────────────────────────────────────────────────────────────────────────────
# _net_stats[cam_id] = {
#   "expected_seq" : الرقم التسلسلي المتوقع التالي
#   "rx_count"     : عدد الحزم المُستقبَلة فعلاً
#   "lost_count"   : عدد الحزم الضائعة المكتشفة
#   "latency_sum"  : مجموع التأخيرات بالثانية
#   "latency_count": عدد قياسات التأخير
#   "bytes_rx"     : مجموع البايتات المُستقبَلة
#   "first_rx_ts"  : وقت أول حزمة (لحساب الـ throughput)
#   "last_rx_ts"   : وقت آخر حزمة
# }
_net_stats:  dict = {}
_stats_lock: threading.Lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# Buffers في الذاكرة — آمنة للـ Multi-threading
#
# _camera_buffers: {rsu_id: {cam_id: latest_payload_dict}}
# _buffer_locks  : {rsu_id: threading.Lock}
# _last_received : {cam_id: unix_timestamp}
#
# كل Listener thread يكتب فقط في buffer الـ RSU الخاص به.
# حلقة التجميع تقرأ جميع الـ buffers. Lock يمنع تعارض القراءة/الكتابة.
# ─────────────────────────────────────────────────────────────────────────────
_camera_buffers: dict = {rsu_id: {} for rsu_id in RSU_CLUSTERS}
_buffer_locks:   dict = {rsu_id: threading.Lock() for rsu_id in RSU_CLUSTERS}
_last_received:  dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# UDP Listener Thread
# ─────────────────────────────────────────────────────────────────────────────
def rsu_udp_listener(rsu_id: str, port: int, stop_event: threading.Event):
    """
    يستمع للحزم الواردة ويقيس المقاييس الشبكية الحقيقية:
    - يكتشف الحزم الضائعة عبر الـ sequence numbers
    - يقيس الـ Latency الحقيقي من send_timestamp الوارد
    - يحسب الـ throughput الفعلي من البايتات والوقت
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(UDP_TIMEOUT)

    try:
        sock.bind(("127.0.0.1", port))
        log.info(f"[{rsu_id}] UDP listener ← port {port}")
    except OSError as e:
        log.error(f"[{rsu_id}] Cannot bind port {port}: {e}")
        return

    expected_cameras = set(RSU_CLUSTERS.get(rsu_id, []))

    while not stop_event.is_set():
        try:
            data, _ = sock.recvfrom(UDP_BUFFER_SIZE)
            recv_ts  = time.time()   # وقت الاستقبال الفعلي
            payload  = json.loads(data.decode("utf-8"))
            cam_id   = payload.get("camera_id", "")

            if cam_id not in expected_cameras:
                continue

            seq       = payload.get("seq", 1)
            send_ts   = payload.get("send_timestamp", recv_ts)
            latency_s = recv_ts - send_ts   # التأخير الحقيقي بالثانية

            with _stats_lock:
                if cam_id not in _net_stats:
                    _net_stats[cam_id] = {
                        "expected_seq" : 1,
                        "rx_count"     : 0,
                        "lost_count"   : 0,
                        "latency_sum"  : 0.0,
                        "latency_count": 0,
                        "bytes_rx"     : 0,
                        "first_rx_ts"  : recv_ts,
                        "last_rx_ts"   : recv_ts,
                    }

                st = _net_stats[cam_id]

                # كشف الحزم الضائعة عبر الفجوة في الـ sequence numbers
                if seq > st["expected_seq"]:
                    gap = seq - st["expected_seq"]
                    st["lost_count"] += gap
                    log.warning(f"  [{rsu_id}←{cam_id}] "
                                f"PACKET LOSS: {gap} packets lost "
                                f"(seq gap {st['expected_seq']}→{seq})")

                st["expected_seq"] = seq + 1
                st["rx_count"]    += 1
                st["latency_sum"] += latency_s
                st["latency_count"] += 1
                st["bytes_rx"]    += len(data)
                st["last_rx_ts"]   = recv_ts

            # تحديث الـ buffer الرئيسي
            with _buffer_locks[rsu_id]:
                _camera_buffers[rsu_id][cam_id] = payload
            _last_received[cam_id] = recv_ts

            log.debug(f"  [{rsu_id}←{cam_id}] "
                      f"seq={seq} latency={latency_s*1000:.2f}ms")

        except socket.timeout:
            continue
        except json.JSONDecodeError as e:
            log.warning(f"[{rsu_id}] Malformed packet: {e}")
        except Exception as e:
            if not stop_event.is_set():
                log.error(f"[{rsu_id}] Error: {e}")

    sock.close()

def get_live_network_stats() -> dict:
    """
    يُعيد إحصاءات الشبكة الحقيقية في أي لحظة يُطلب فيها.

    كل قيمة محسوبة من بيانات فعلية مقاسة:
      PDR     = (حزم وصلت) / (حزم وصلت + حزم ضائعة) × 100
      Latency = مجموع التأخيرات / عدد القياسات
      Tput    = بايتات وصلت × 8 / الفترة الزمنية
    """
    with _stats_lock:
        snapshot = {k: dict(v) for k, v in _net_stats.items()}

    result = {}
    for cam_id, st in snapshot.items():
        rx   = st["rx_count"]
        lost = st["lost_count"]
        total_expected = rx + lost

        pdr_pct = (rx / total_expected * 100.0) if total_expected > 0 else 0.0

        avg_latency_ms = (
            st["latency_sum"] / st["latency_count"] * 1000.0
            if st["latency_count"] > 0 else 0.0
        )

        duration = st["last_rx_ts"] - st["first_rx_ts"]
        tput_kbps = (
            st["bytes_rx"] * 8.0 / duration / 1000.0
            if duration > 0 else 0.0
        )

        result[cam_id] = {
            "packets_tx_expected" : total_expected,
            "packets_rx"          : rx,
            "packets_lost"        : lost,
            "pdr_pct"             : round(pdr_pct, 2),
            "avg_latency_ms"      : round(avg_latency_ms, 3),
            "throughput_kbps"     : round(tput_kbps, 3),
        }
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Edge-Based Aggregation من الذاكرة
# ─────────────────────────────────────────────────────────────────────────────
def aggregate_rsu(rsu_id: str) -> dict | None:
    """
    يقرأ أحدث بيانات الكاميرات من الـ RAM ويُجمِّعها.

    خوارزمية Edge-Based Aggregation:
      vehicle_count  = مجموع (كل كاميرا تُغطي تقاطعاً مختلفاً)
      avg_speed_kmh  = متوسط (نمط يصف المنطقة ككل)
      traffic_density = متوسط (نمط يصف المنطقة ككل)

    Staleness Check:
      إذا لم تُرسل كاميرا بيانات منذ STALE_THRESHOLD ثانية →
      يُتجاهَل آخر قراءة لها لأنها تعكس حالة قديمة غير موثوقة.
    """
    now = time.time()

    # نأخذ snapshot آمناً ثم نُحرِّر الـ lock فوراً
    with _buffer_locks[rsu_id]:
        snapshot = dict(_camera_buffers[rsu_id])

    readings = []
    for cam_id, data in snapshot.items():
        age = now - _last_received.get(cam_id, 0)
        if age <= STALE_THRESHOLD:
            readings.append(data)
        else:
            log.warning(f"  [{rsu_id}] Stale: {cam_id} ({age:.1f}s ago)")

    if not readings:
        log.warning(f"[{rsu_id}] No fresh camera data — skipping")
        return None

    total_count = sum(r.get("vehicle_count",  0)   for r in readings)
    avg_speed   = sum(r.get("avg_speed_kmh",  0.0) for r in readings) / len(readings)
    avg_density = sum(r.get("density_veh_km", 0.0) for r in readings) / len(readings)

    return {
        "rsu_id"          : rsu_id,
        "timestamp"       : datetime.now(timezone.utc).isoformat(),
        "vehicle_count"   : total_count,
        "avg_speed_kmh"   : round(avg_speed,   2),
        "traffic_density" : round(avg_density, 3),
        "latitude"        : RSU_COORDINATES[rsu_id]["lat"],
        "longitude"       : RSU_COORDINATES[rsu_id]["lon"],
        "camera_count"    : len(readings),
        "cameras_active"  : [r["camera_id"] for r in readings],
    }


# ─────────────────────────────────────────────────────────────────────────────
# نسخ احتياطية اختيارية — daemon threads
# ─────────────────────────────────────────────────────────────────────────────
def save_rsu_json(rsu_id: str, payload: dict) -> None:
    """حفظ اختياري غير متزامن — لا يُعيق حلقة التجميع."""
    if not SAVE_RSU_BACKUP:
        return

    def _write():
        os.makedirs(RSU_JSON_DIR, exist_ok=True)
        path = os.path.join(RSU_JSON_DIR, f"{rsu_id}.json")
        tmp  = path + ".tmp"
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:
            log.warning(f"[BACKUP] {rsu_id}: {e}")

    threading.Thread(target=_write, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# MQTT Client
# ─────────────────────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info(f"[MQTT] Connected to {MQTT_BROKER}:{MQTT_PORT} ✓")
    else:
        log.error(f"[MQTT] Connection failed rc={rc}")


def create_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(client_id="RSU_Aggregator_v2")
    client.on_connect = on_connect
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_start()
        time.sleep(1.0)
    except ConnectionRefusedError:
        log.error("Cannot connect to MQTT — start Mosquitto first:")
        log.error("  mosquitto -c C:\\mosquitto\\mosquitto.conf -v")
        raise
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def run():
    log.info("=" * 65)
    log.info("RSU Aggregation v2 — Memory-Based Network Streaming")
    log.info("Camera→RSU: UDP Sockets (WiFi simulation)")
    log.info("RSU→Server: MQTT over TCP (QoS=1)")
    log.info(f"Ports: " + " | ".join(
        f"{k}:{v}" for k, v in RSU_UDP_PORTS.items()))
    log.info(f"Interval: {BROADCAST_INTERVAL}s | "
             f"Stale threshold: {STALE_THRESHOLD}s")
    log.info("=" * 65)

    stop_event = threading.Event()

    # تشغيل thread واحد لكل RSU
    threads = []
    for rsu_id in RSU_CLUSTERS:
        t = threading.Thread(
            target=rsu_udp_listener,
            args=(rsu_id, RSU_UDP_PORTS[rsu_id], stop_event),
            name=f"UDP-{rsu_id}",
            daemon=True
        )
        t.start()
        threads.append(t)

    mqtt_client = create_mqtt_client()
    cycle = 0

    try:
        while True:
            time.sleep(BROADCAST_INTERVAL)
            cycle += 1
            log.info(f"\n── Cycle #{cycle} " + "─" * 40)

            # ── طباعة إحصاءات الشبكة الحقيقية ──────────────────
            net = get_live_network_stats()
            if net:
                log.info("  [Network Stats — REAL MEASUREMENTS]")
                for cam_id, s in net.items():
                    log.info(
                        f"    {cam_id}: "
                        f"TX≈{s['packets_tx_expected']} | "
                        f"RX={s['packets_rx']} | "
                        f"Lost={s['packets_lost']} | "
                        f"PDR={s['pdr_pct']}% | "
                        f"Latency={s['avg_latency_ms']}ms | "
                        f"Tput={s['throughput_kbps']}kbps"
                    )

            # ── تجميع وإرسال بيانات المرور ───────────────────────
            for rsu_id in RSU_CLUSTERS:
                payload = aggregate_rsu(rsu_id)
                if payload is None:
                    continue

                # إضافة ملخص إحصاءات الشبكة لكل RSU في الـ payload
                rsu_net = {}
                for cam_id in RSU_CLUSTERS[rsu_id]:
                    if cam_id in net:
                        rsu_net[cam_id] = net[cam_id]
                payload["network_stats"] = rsu_net

                topic  = f"{MQTT_TOPIC_PREFIX}/{rsu_id}"
                result = mqtt_client.publish(
                    topic,
                    json.dumps(payload, ensure_ascii=False),
                    qos=MQTT_QOS
                )
                # تسجيل الـ trace — حجم الحزمة الفعلي بعد التسلسل
                log_rsu_trace(rsu_id, len(json.dumps(payload, ensure_ascii=False).encode()))

                status = "✓" if result.rc == mqtt.MQTT_ERR_SUCCESS else f"✗ rc={result.rc}"
                log.info(
                    f"  [{rsu_id}] "
                    f"count={payload['vehicle_count']:3d} | "
                    f"speed={payload['avg_speed_kmh']:5.1f} km/h | "
                    f"density={payload['traffic_density']:6.2f} veh/km | "
                    f"→ {topic} {status}"
                )

                save_rsu_json(rsu_id, payload)

    except KeyboardInterrupt:
        log.info("\n[RSU] Stopping...")

        # حفظ إحصاءات الشبكة النهائية عند الإيقاف
        final_stats = get_live_network_stats()
        if final_stats:
            import csv
            csv_path = r"C:\TrafficProject\data\ns3_results\real_network_stats.csv"
            os.makedirs(os.path.dirname(csv_path), exist_ok=True)
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'cam_id','packets_tx_expected','packets_rx',
                    'packets_lost','pdr_pct','avg_latency_ms','throughput_kbps'
                ])
                writer.writeheader()
                for cam_id, s in final_stats.items():
                    writer.writerow({'cam_id': cam_id, **s})
            log.info(f"[Stats] Saved to {csv_path}")

    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=2.0)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        log.info("[RSU] Clean shutdown.")


if __name__ == "__main__":
    run()
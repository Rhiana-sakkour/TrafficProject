# traffic_analyzer.py — تصور مشابه للصور المرجعية
import numpy as np
import cv2
from collections import defaultdict, deque
from ultralytics import YOLO
from sort import Sort

# ── ثوابت الكاميرا (ارتفاع 8م، زاوية 45°، FOV 60°) ────────────────
CONF_THRESHOLD      = 0.25
RESIZE_WIDTH        = 1280
VEHICLE_CLASSES     = [2, 3, 5, 7]
COUNT_LINE_Y        = 400      # خط العد في الثلث السفلي من الصورة
PIXELS_PER_METER    = 25.98   # مُعايَر: 720/(29.86-2.14) = 720/27.72
SPEED_WINDOW_SIZE   = 5
MIN_MOVEMENT_PIXELS = 2
MIN_TIME_SEC        = 0.05
MIN_TRACK_LEN       = 3
SORT_MAX_AGE        = 45
SORT_MIN_HITS       = 2        # خُفِّض من 3 إلى 2 لكشف أسرع
SORT_IOU_THRESH     = 0.2      # خُفِّض لاستيعاب تغيير الحجم عند 45°
LOST_BUFFER         = 30
IOU_REUSE_THRESH    = 0.3
CARLA_FPS           = 20.0

# ألوان المركبات حسب فئة COCO (BGR)
CLASS_COLORS = {
    2: (0,   220,  0),    # سيارة  → أخضر
    3: (255, 140,  0),    # دراجة  → برتقالي
    5: (0,    80, 255),   # حافلة  → أزرق
    7: (0,   200, 255),   # شاحنة  → سماوي
}
CLASS_NAMES = {2: "Car", 3: "Moto", 5: "Bus", 7: "Truck"}

_model = None

def get_model():
    global _model
    if _model is None:
        print("[TrafficAnalyzer] Loading YOLOv8n...")
        _model = YOLO("yolov8n.pt")
        print("[TrafficAnalyzer] Model ready.")
    return _model


def iou(A, B):
    xA, yA = max(A[0], B[0]), max(A[1], B[1])
    xB, yB = min(A[2], B[2]), min(A[3], B[3])
    inter  = max(0, xB-xA+1) * max(0, yB-yA+1)
    aA = (A[2]-A[0]+1)*(A[3]-A[1]+1)
    aB = (B[2]-B[0]+1)*(B[3]-B[1]+1)
    return inter / float(aA + aB - inter)


def calc_density(n, ppm, h_px):
    km = (h_px / ppm) / 1000.0
    return round(n / km, 2) if km > 0 else 0.0


class TrafficAnalyzer:

    def __init__(self, camera_id: str):
        self.cam_id  = camera_id
        self.model   = get_model()
        self.tracker = Sort(max_age=SORT_MAX_AGE,
                            min_hits=SORT_MIN_HITS,
                            iou_threshold=SORT_IOU_THRESH)
        self.frame_id    = 0
        self.track_hist  = defaultdict(lambda: deque(maxlen=SPEED_WINDOW_SIZE))
        self.speed_hist  = {}
        self.class_hist  = {}       # track_id → class_id
        self.last_bbox   = {}
        self.counted_dn  = set()
        self.counted_up  = set()
        self.lost_tracks = {}
        self.total_ids   = set()
        self.dn_count    = 0
        self.up_count    = 0
        # عدد كل فئة مرت من الخط
        self.class_count = {2: 0, 3: 0, 5: 0, 7: 0}

    def process_frame(self, frame: np.ndarray) -> dict:
        self.frame_id += 1
        fid = self.frame_id
        h, w = frame.shape[:2]
        if w != RESIZE_WIDTH:
            frame = cv2.resize(frame, (RESIZE_WIDTH, int(h*RESIZE_WIDTH/w)))

        # ── كشف الأجسام ──────────────────────────────────────────
        results = self.model(frame, verbose=False)[0]
        dets, det_classes = [], {}
        for box in results.boxes:
            cls  = int(box.cls[0])
            conf = float(box.conf[0])
            if cls in VEHICLE_CLASSES and conf >= CONF_THRESHOLD:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                dets.append([x1, y1, x2, y2, conf])
                # نحفظ فئة أول كشف لكل صندوق
                cx_det = (x1+x2)//2
                cy_det = (y1+y2)//2
                det_classes[(cx_det, cy_det)] = cls

        dets_np    = np.array(dets) if dets else np.empty((0,5))
        tracks     = self.tracker.update(dets_np)
        active_ids = {int(t[4]) for t in tracks}

        # تنظيف المسارات المنتهية
        for tid in [k for k,(_, lf) in self.lost_tracks.items()
                    if fid - lf > LOST_BUFFER]:
            del self.lost_tracks[tid]

        speeds_list = []

        # ── معالجة كل مسار ───────────────────────────────────────
        for track in tracks:
            x1, y1, x2, y2, raw_id = map(int, track[:5])
            tid = raw_id

            # Re-ID
            if raw_id not in self.track_hist:
                cur = [x1, y1, x2, y2]
                best_iou, best_old = IOU_REUSE_THRESH, None
                for old_id, (ob, _) in self.lost_tracks.items():
                    v = iou(cur, ob)
                    if v > best_iou:
                        best_iou, best_old = v, old_id
                if best_old is not None:
                    self.track_hist[raw_id] = self.track_hist.pop(
                        best_old, deque(maxlen=SPEED_WINDOW_SIZE))
                    self.speed_hist[raw_id] = self.speed_hist.pop(best_old, 0.0)
                    self.class_hist[raw_id] = self.class_hist.pop(best_old, 2)
                    for s in (self.counted_dn, self.counted_up):
                        if best_old in s:
                            s.add(raw_id); s.discard(best_old)
                    del self.lost_tracks[best_old]
                else:
                    self.track_hist[raw_id] = deque(maxlen=SPEED_WINDOW_SIZE)
                    # تعيين الفئة — ابحث عن أقرب كشف
                    cx, cy = (x1+x2)//2, (y1+y2)//2
                    best_dist, best_cls = 99999, 2
                    for (dx,dy), cls in det_classes.items():
                        d = abs(dx-cx)+abs(dy-cy)
                        if d < best_dist:
                            best_dist, best_cls = d, cls
                    self.class_hist[raw_id] = best_cls

            vehicle_cls = self.class_hist.get(tid, 2)
            color       = CLASS_COLORS.get(vehicle_cls, (0,255,0))

            cx  = (x1+x2)//2
            by  = y2
            ts  = fid * (1000.0 / CARLA_FPS)

            self.track_hist[tid].append((cx, by, ts))
            self.last_bbox[tid] = [x1, y1, x2, y2]
            self.total_ids.add(tid)

            # ── حساب السرعة ──────────────────────────────────────
            spd  = self.speed_hist.get(tid, 0.0)
            hist = self.track_hist[tid]
            if len(hist) == SPEED_WINDOW_SIZE:
                _, y0, t0 = hist[0]
                _, yn, tn = hist[-1]
                dp = abs(yn - y0)
                if dp > MIN_MOVEMENT_PIXELS:
                    dt = (tn - t0) / 1000.0
                    spd = (dp / PIXELS_PER_METER / dt) * 3.6 \
                          if dt > MIN_TIME_SEC else 0.0
                else:
                    spd = 0.0
                self.speed_hist[tid] = spd
            speeds_list.append(spd)

            # ── عدّ المركبات ──────────────────────────────────────
            if len(hist) >= MIN_TRACK_LEN:
                _, py, _ = hist[-2]
                _, cy2, _ = hist[-1]
                if py < COUNT_LINE_Y <= cy2 and tid not in self.counted_dn:
                    self.dn_count += 1
                    self.class_count[vehicle_cls] = \
                        self.class_count.get(vehicle_cls, 0) + 1
                    self.counted_dn.add(tid)
                if py > COUNT_LINE_Y >= cy2 and tid not in self.counted_up:
                    self.up_count += 1
                    self.counted_up.add(tid)

            # ── رسم الصندوق والمعلومات ────────────────────────────
            cv2.rectangle(frame, (x1,y1), (x2,y2), color, 2)
            label = f"#{tid} {CLASS_NAMES.get(vehicle_cls,'Veh')} {round(spd)}km/h"
            (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1-lh-6), (x1+lw+4, y1), color, -1)
            cv2.putText(frame, label, (x1+2, y1-4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1)

        # تحديث المسارات المفقودة
        for tid in set(self.track_hist.keys()) - active_ids:
            if tid not in self.lost_tracks and tid in self.last_bbox:
                self.lost_tracks[tid] = (self.last_bbox[tid], fid)

        # ── رسم خط العد ──────────────────────────────────────────
        cv2.line(frame, (0, COUNT_LINE_Y), (RESIZE_WIDTH, COUNT_LINE_Y),
                 (0, 0, 255), 2)
        cv2.putText(frame, "COUNTING LINE",
                    (RESIZE_WIDTH//2 - 60, COUNT_LINE_Y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1)

        # ── لوحة الإحصاء (أعلى يسار) ────────────────────────────
        panel_lines = [
            f" Camera: {self.cam_id}",
            f" Total crossed: {self.dn_count+self.up_count}",
            f"  Cars  : {self.class_count.get(2,0)}",
            f"  Motos : {self.class_count.get(3,0)}",
            f"  Buses : {self.class_count.get(5,0)}",
            f"  Trucks: {self.class_count.get(7,0)}",
        ]
        pw = 200
        cv2.rectangle(frame, (0,0), (pw, 20+18*len(panel_lines)),
                      (0,0,0), -1)
        cv2.rectangle(frame, (0,0), (pw, 20+18*len(panel_lines)),
                      (255,255,255), 1)
        for i, line in enumerate(panel_lines):
            cv2.putText(frame, line, (4, 16+18*i),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                        (0,255,255), 1)

        # ── حساب المؤشرات النهائية ───────────────────────────────
        density  = calc_density(len(dets), PIXELS_PER_METER, frame.shape[0])
        avg_spd  = float(np.mean(speeds_list)) if speeds_list else 0.0

        # معلومات إضافية أسفل الصورة
        info = (f"Active: {len(tracks)}  "
                f"Density: {density:.1f} veh/km  "
                f"Avg Speed: {avg_spd:.1f} km/h")
        cv2.rectangle(frame,
                      (0, frame.shape[0]-22),
                      (frame.shape[1], frame.shape[0]),
                      (0,0,0), -1)
        cv2.putText(frame, info,
                    (6, frame.shape[0]-6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0,255,255), 1)

        return {
            "camera_id"      : self.cam_id,
            "frame_id"       : fid,
            "vehicle_count"  : len(self.total_ids),
            "avg_speed_kmh"  : round(avg_spd, 1),
            "density_veh_km" : density,
            "down_count"     : self.dn_count,
            "up_count"       : self.up_count,
            "annotated"      : frame,
        }
import cv2
import time
import numpy as np
from collections import defaultdict, deque
from ultralytics import YOLO
from sort import Sort

# ── Video / model ──────────────────────────────────────────────────────────────
VIDEO_PATH   = "stmarc_video.avi"
MODEL_NAME   = "yolov8n.pt"
RESULTS_FILE = "my_tracking_results.txt"
OUTPUT_VIDEO = "output_tracked.avi"       # Added: save annotated output for offline review

# ── Detection ──────────────────────────────────────────────────────────────────
CONF_THRESHOLD  = 0.4
RESIZE_WIDTH    = 640
VEHICLE_CLASSES = [2, 3, 5, 7]           # COCO: car, bus, truck, motorcycle

# ── Counting line ──────────────────────────────────────────────────────────────
COUNT_LINE_Y = 360

# ── Speed estimation ───────────────────────────────────────────────────────────
PIXELS_PER_METER    = 34.64
# Exact fixed window: deque maxlen matches this so [0]→[-1] always spans
# exactly SPEED_WINDOW_SIZE frames — no variable-length averaging
SPEED_WINDOW_SIZE   = 5
MIN_MOVEMENT_PIXELS = 2
MIN_TIME_SEC        = 0.1

# ── Counting ───────────────────────────────────────────────────────────────────
# Require this many history points before allowing a count,
# to avoid false triggers from noisy detections at track birth
MIN_TRACK_LEN_FOR_COUNT = 5

# ── SORT tracker ───────────────────────────────────────────────────────────────
# max_age: how many frames SORT keeps a track alive without a match.
# High value reduces ID switches at the cost of ghost tracks.
SORT_MAX_AGE    = 45
SORT_MIN_HITS   = 3
SORT_IOU_THRESH = 0.25

# ── Re-ID ──────────────────────────────────────────────────────────────────────
LOST_TRACK_BUFFER_SIZE = 30   # frames to keep a lost track available for re-ID
IOU_REUSE_THRESHOLD    = 0.3  # minimum IoU to accept a lost-track match


# ──────────────────────────────────────────────────────────────────────────────
def iou(boxA, boxB):
    """Intersection-over-Union for two [x1, y1, x2, y2] boxes."""
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    inter  = max(0, xB - xA + 1) * max(0, yB - yA + 1)
    areaA  = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
    areaB  = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)
    return inter / float(areaA + areaB - inter)


def get_video_fps(cap):
    fps = cap.get(cv2.CAP_PROP_FPS)
    return fps if fps > 0 else 30.0


def safe_int_speed(speed_kmh):
    # FIX: round() not int() — int(49.9) = 49, round(49.9) = 50
    return round(speed_kmh) if np.isfinite(speed_kmh) else 0


def calculate_density(n_vehicles, pixels_per_meter, frame_height_px):
    """
    Density in vehicles/km over the visible road segment.
    FIX: divide by 1000, not 100 — 1 km = 1000 m, not 100 m.
    """
    if pixels_per_meter <= 0:
        return 0.0
    visible_km = (frame_height_px / pixels_per_meter) / 1000.0
    return n_vehicles / visible_km if visible_km > 0 else 0.0


# ──────────────────────────────────────────────────────────────────────────────
def main():
    print("[INFO] Loading YOLO model...")
    model   = YOLO(MODEL_NAME)
    tracker = Sort(max_age=SORT_MAX_AGE,
                   min_hits=SORT_MIN_HITS,
                   iou_threshold=SORT_IOU_THRESH)

    cap = cv2.VideoCapture(VIDEO_PATH)
    if not cap.isOpened():
        print("[ERROR] Cannot open video.")
        return

    video_fps = get_video_fps(cap)
    print(f"[INFO] Video FPS: {video_fps:.2f}")
    print(f"[INFO] Count line Y = {COUNT_LINE_Y}")

    # Peek at first frame to get resized output dimensions, then rewind
    ret, peek = cap.read()
    if not ret:
        print("[ERROR] Cannot read first frame.")
        return
    out_h = int(peek.shape[0] * RESIZE_WIDTH / peek.shape[1])
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    fourcc = cv2.VideoWriter_fourcc(*"XVID")
    writer = cv2.VideoWriter(OUTPUT_VIDEO, fourcc, video_fps, (RESIZE_WIDTH, out_h))

    f_out = open(RESULTS_FILE, "w")

    # ── Per-track state ────────────────────────────────────────────────────────
    # maxlen = SPEED_WINDOW_SIZE: guarantees [0] and [-1] always span exactly
    # that many frames once full, removing variable-window speed bias
    track_history = defaultdict(lambda: deque(maxlen=SPEED_WINDOW_SIZE))
    speed_history = {}

    # last known bbox per track — required for lost_tracks IoU matching
    last_bbox = {}

    # FIX: separate sets per direction instead of one shared counted_ids set,
    # so a vehicle can be counted once in each direction independently
    counted_down = set()   # IDs that crossed line with increasing y (top→bottom)
    counted_up   = set()   # IDs that crossed line with decreasing y (bottom→top)

    # Maps track_id → ([x1,y1,x2,y2], frame_id_when_lost)
    lost_tracks = {}

    down_count   = 0
    up_count     = 0
    frame_id     = 0
    prev_time    = time.time()
    total_unique = set()   # FIX: unique vehicle IDs, not raw box count per frame

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_id += 1
        h, w  = frame.shape[:2]
        frame = cv2.resize(frame, (RESIZE_WIDTH, int(h * RESIZE_WIDTH / w)))

        # ── Detection ─────────────────────────────────────────────────────────
        results    = model(frame, verbose=False)[0]
        detections = []
        for box in results.boxes:
            cls_id = int(box.cls[0])
            conf   = float(box.conf[0])
            if cls_id in VEHICLE_CLASSES and conf >= CONF_THRESHOLD:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                detections.append([x1, y1, x2, y2, conf])

        dets_np    = np.array(detections) if detections else np.empty((0, 5))
        tracks     = tracker.update(dets_np)
        active_ids = {int(t[4]) for t in tracks}

        # ── Expire stale lost tracks ───────────────────────────────────────────
        expired = [tid for tid, (_, last_f) in lost_tracks.items()
                   if frame_id - last_f > LOST_TRACK_BUFFER_SIZE]
        for tid in expired:
            del lost_tracks[tid]

        # ── Per-track processing ───────────────────────────────────────────────
        for track in tracks:
            x1, y1, x2, y2, raw_id = map(int, track[:5])
            track_id = raw_id   # single authoritative ID; never reassigned below

            # ── Re-ID: inherit history from a recently lost track ──────────────
            if raw_id not in track_history:
                current_box = [x1, y1, x2, y2]
                best_iou    = IOU_REUSE_THRESHOLD
                best_old_id = None

                for lost_id, (lost_box, _) in lost_tracks.items():
                    v = iou(current_box, lost_box)
                    if v > best_iou:
                        best_iou    = v
                        best_old_id = lost_id

                if best_old_id is not None:
                    # FIX: move history into raw_id slot and KEEP track_id = raw_id.
                    # Original code moved history to raw_id then set track_id = old_id,
                    # so subsequent track_history[track_id] hit defaultdict and the
                    # transferred history was silently orphaned and lost.
                    track_history[raw_id] = track_history.pop(
                        best_old_id, deque(maxlen=SPEED_WINDOW_SIZE))
                    speed_history[raw_id] = speed_history.pop(best_old_id, 0.0)

                    # Carry directional count state to the new ID
                    if best_old_id in counted_down:
                        counted_down.add(raw_id)
                        counted_down.discard(best_old_id)
                    if best_old_id in counted_up:
                        counted_up.add(raw_id)
                        counted_up.discard(best_old_id)

                    del lost_tracks[best_old_id]
                else:
                    track_history[raw_id] = deque(maxlen=SPEED_WINDOW_SIZE)

            # ── Record position ───────────────────────────────────────────────
            center_x     = (x1 + x2) // 2
            bottom_y     = y2
            timestamp_ms = frame_id * (1000.0 / video_fps)

            track_history[track_id].append((center_x, bottom_y, timestamp_ms))
            last_bbox[track_id] = [x1, y1, x2, y2]
            total_unique.add(track_id)

            # MOT-challenge format output
            f_out.write(
                f"{frame_id},{track_id},{x1},{y1},{x2-x1},{y2-y1},-1,-1,-1,-1\n")

            # ── Speed estimation ──────────────────────────────────────────────
            speed_kmh = speed_history.get(track_id, 0.0)
            hist      = track_history[track_id]

            # Only compute when deque is exactly full: guarantees a consistent
            # time window and avoids inflated speeds on newly born tracks
            if len(hist) == SPEED_WINDOW_SIZE:
                _, y_old, t_old = hist[0]
                _, y_new, t_new = hist[-1]

                # FIX: vertical displacement only (abs(dy)) instead of Euclidean.
                # Horizontal bbox jitter from the detector adds phantom lateral
                # motion that inflates hypot-based speed estimates significantly.
                pixel_dist = abs(y_new - y_old)

                if pixel_dist > MIN_MOVEMENT_PIXELS:
                    time_sec = (t_new - t_old) / 1000.0
                    if time_sec > MIN_TIME_SEC:
                        speed_kmh = (pixel_dist / PIXELS_PER_METER / time_sec) * 3.6
                    else:
                        speed_kmh = 0.0
                else:
                    speed_kmh = 0.0

                speed_history[track_id] = speed_kmh

            # ── Bidirectional counting ────────────────────────────────────────
            if len(hist) >= MIN_TRACK_LEN_FOR_COUNT:
                _, prev_y, _ = hist[-2]
                _, curr_y, _ = hist[-1]

                # Downward (increasing y): vehicle moves from top toward bottom
                if prev_y < COUNT_LINE_Y <= curr_y and track_id not in counted_down:
                    down_count += 1
                    counted_down.add(track_id)

                # Upward (decreasing y): vehicle moves from bottom toward top
                if prev_y > COUNT_LINE_Y >= curr_y and track_id not in counted_up:
                    up_count += 1
                    counted_up.add(track_id)

            # ── Draw bounding box ─────────────────────────────────────────────
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.circle(frame, (center_x, bottom_y), 4, (0, 0, 255), -1)
            cv2.putText(frame,
                        f"ID {track_id}  {safe_int_speed(speed_kmh)} km/h",
                        (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)

        # ── FIX: populate lost_tracks (was bare "pass" — made re-ID dead code) ──
        for tid in set(track_history.keys()) - active_ids:
            if tid not in lost_tracks and tid in last_bbox:
                lost_tracks[tid] = (last_bbox[tid], frame_id)

        # ── Overlays ──────────────────────────────────────────────────────────
        cv2.line(frame, (0, COUNT_LINE_Y), (RESIZE_WIDTH, COUNT_LINE_Y),
                 (255, 0, 0), 2)

        # FIX: display both directions separately
        cv2.putText(frame, f"Down: {down_count}  Up: {up_count}",
                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

        density = calculate_density(len(detections), PIXELS_PER_METER,
                                    frame.shape[0])
        cv2.putText(frame, f"Density: {density:.1f} veh/km",
                    (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)

        curr_time = time.time()
        proc_fps  = 1.0 / max(curr_time - prev_time, 1e-6)
        prev_time = curr_time
        cv2.putText(frame, f"FPS: {proc_fps:.1f}",
                    (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)

        writer.write(frame)
        cv2.imshow("Traffic Analytics", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    f_out.close()
    writer.release()
    cap.release()
    cv2.destroyAllWindows()

    print(f"[INFO] Done.")
    print(f"  Frames processed : {frame_id}")
    print(f"  Unique vehicles  : {len(total_unique)}")   # FIX: meaningful metric
    print(f"  Downward count   : {down_count}")
    print(f"  Upward count     : {up_count}")
    print(f"  Output video     : {OUTPUT_VIDEO}")


if __name__ == "__main__":
    main()
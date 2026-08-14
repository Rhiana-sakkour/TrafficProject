"""
sumo_ground_truth.py
يجمع بيانات Ground Truth من SUMO للمقارنة مع YOLOv8
"""
import traci
import csv
import os
import time

SUMO_BIN  = r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe"
NET_FILE  = r"C:\TrafficProject\phase2_sumo\city.net.xml"
ROUTE     = r"C:\TrafficProject\phase2_sumo\routes_medium.rou.xml"
GT_PORT   = 8830
GT_FILE   = r"C:\TrafficProject\data\ns3_results\sumo_ground_truth.csv"


def run_sumo_ground_truth():
    # تحقق من الملفات
    for f in [NET_FILE, ROUTE]:
        if not os.path.exists(f):
            print(f"ERROR: {f} not found"); return

    os.makedirs(os.path.dirname(GT_FILE), exist_ok=True)

    sumo_cmd = [
        SUMO_BIN,
        "--net-file",    NET_FILE,
        "--route-files", ROUTE,
        "--step-length", "0.05",
        "--end",         "3600",
        "--no-warnings",
        "--no-step-log",
    ]

    print("[SUMO GT] Starting SUMO via traci.start()...")
    traci.start(sumo_cmd, port=GT_PORT, label="gt")
    traci.switch("gt")
    print("[SUMO GT] Connected ✓")

    with open(GT_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'step', 'sim_time_s', 'vehicle_id',
            'x', 'y', 'speed_ms', 'speed_kmh',
            'road_id', 'vehicle_type'
        ])

        # كل ثانية (20 خطوة × 0.05s)
        for step in range(72000):
            traci.simulationStep()
            sim_time = step * 0.05

            if step % 20 == 0:
                vehicles = traci.vehicle.getIDList()
                for vid in vehicles:
                    x, y     = traci.vehicle.getPosition(vid)
                    speed_ms = traci.vehicle.getSpeed(vid)
                    road_id  = traci.vehicle.getRoadID(vid)
                    vtype    = traci.vehicle.getTypeID(vid)
                    writer.writerow([
                        step, round(sim_time, 2), vid,
                        round(x, 2), round(y, 2),
                        round(speed_ms, 3),
                        round(speed_ms * 3.6, 2),
                        road_id, vtype
                    ])

            if step % 4000 == 0:
                n = len(traci.vehicle.getIDList())
                print(f"[SUMO GT] t={sim_time:.0f}s | Vehicles: {n}")

    traci.switch("gt")
    traci.close()
    print(f"[SUMO GT] Done. Saved to: {GT_FILE}")

    # إحصاء السطور
    with open(GT_FILE) as f:
        lines = sum(1 for _ in f) - 1
    print(f"[SUMO GT] {lines} records written")


if __name__ == "__main__":
    run_sumo_ground_truth()
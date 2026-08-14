"""
run_scenarios.py
يُشغِّل السيناريوهات الثلاثة ويقيس مؤشرات المرور
"""
import traci
import csv
import os
import time

SUMO_BIN  = r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe"
NET_FILE  = r"C:\TrafficProject\phase2_sumo\city.net.xml"
OUT_DIR   = r"C:\TrafficProject\data"

SCENARIOS = {
    "light" : r"C:\TrafficProject\phase2_sumo\routes_light.rou.xml",
    "medium": r"C:\TrafficProject\phase2_sumo\routes_medium.rou.xml",
    "heavy" : r"C:\TrafficProject\phase2_sumo\routes_heavy.rou.xml",
}

# منافذ مختلفة لكل سيناريو لتجنب التعارض
PORTS = {"light": 8820, "medium": 8821, "heavy": 8822}


def run_scenario(name, route_file):
    port = PORTS[name]
    print(f"\n[Scenario] Running: {name} (port {port})")

    # traci.start() يُشغِّل SUMO ويتصل به تلقائياً
    # أكثر موثوقية من subprocess + traci.connect()
    sumo_cmd = [
        SUMO_BIN,
        "--net-file",    NET_FILE,
        "--route-files", route_file,
        "--step-length", "0.05",
        "--end",         "3600",
        "--no-warnings",
        "--no-step-log",
    ]

    traci.start(sumo_cmd, port=port, label=name)
    traci.switch(name)

    metrics = {
        "scenario"        : name,
        "total_vehicles"  : 0,
        "avg_speed_kmh"   : 0.0,
        "avg_wait_time_s" : 0.0,
    }

    speed_sum   = 0.0
    wait_sum    = 0.0
    sample_count = 0

    # 3600s / 0.05s = 72000 خطوة
    for step in range(72000):
        traci.simulationStep()
        if step % 20 == 0:   # كل ثانية واحدة
            vehicles = traci.vehicle.getIDList()
            if not vehicles:
                continue
            for vid in vehicles:
                speed_sum  += traci.vehicle.getSpeed(vid) * 3.6
                wait_sum   += traci.vehicle.getWaitingTime(vid)
            metrics["total_vehicles"] = max(
                metrics["total_vehicles"], len(vehicles))
            sample_count += len(vehicles)

        if step % 4000 == 0:
            vehicles = traci.vehicle.getIDList()
            print(f"  Step {step}/72000 | Vehicles: {len(vehicles)}")

    if sample_count > 0:
        metrics["avg_speed_kmh"]   = round(speed_sum  / sample_count, 2)
        metrics["avg_wait_time_s"] = round(wait_sum   / sample_count, 2)

    traci.switch(name)
    traci.close()
    print(f"  Done: {name}")
    print(f"  Max Vehicles : {metrics['total_vehicles']}")
    print(f"  Avg Speed    : {metrics['avg_speed_kmh']} km/h")
    print(f"  Avg Wait     : {metrics['avg_wait_time_s']} s")
    return metrics


def main():
    results = []

    for name, route in SCENARIOS.items():
        # تحقق من وجود الملفات
        if not os.path.exists(NET_FILE):
            print(f"ERROR: {NET_FILE} not found")
            return
        if not os.path.exists(route):
            print(f"ERROR: {route} not found")
            return

        m = run_scenario(name, route)
        results.append(m)
        time.sleep(2)  # انتظر قبل السيناريو التالي

    # حفظ النتائج
    out = os.path.join(OUT_DIR, "scenario_comparison.csv")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"\n[Scenarios] Saved to: {out}")

    # طباعة مقارنة
    print("\n═══ Scenario Comparison ═══════════════════")
    print(f"{'Scenario':<10} {'MaxVeh':<10} {'Speed(km/h)':<14} {'Wait(s)'}")
    print("─" * 45)
    for r in results:
        print(f"{r['scenario']:<10} {r['total_vehicles']:<10} "
              f"{r['avg_speed_kmh']:<14} {r['avg_wait_time_s']}")


if __name__ == "__main__":
    main()
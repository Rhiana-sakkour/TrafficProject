"""
adaptive_signal.py
التحكم التكيفي بإشارات المرور عبر TraCI
يقرأ الكثافة من RSU_03_MINI.json ويضبط مدة الضوء الأخضر
"""
import traci
import json
import os
import time
import paho.mqtt.client as mqtt

SUMO_BIN   = r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe"
NET_FILE   = r"C:\TrafficProject\phase2_sumo\city.net.xml"
ROUTE      = r"C:\TrafficProject\phase2_sumo\routes_medium.rou.xml"
TRACI_PORT = 8840
RSU_JSON   = r"C:\TrafficProject\data\rsu_json\RSU_03_MINI.json"

_live_density = {"RSU_01": 0.0, "RSU_02": 0.0, "RSU_03": 0.0}


def on_message(client, userdata, msg):
    try:
        data    = json.loads(msg.payload.decode())
        rsu_id  = data.get("rsu_id", "")
        density = data.get("traffic_density", 0.0)
        if rsu_id in _live_density:
            _live_density[rsu_id] = density
    except Exception:
        pass


def calc_green(density: float) -> float:
    return min(60.0, max(15.0, 15.0 + 0.45 * density))


def run():
    # MQTT
    client = mqtt.Client(client_id="AdaptiveSignal")
    client.on_message = on_message
    client.connect("localhost", 1883, 60)
    client.subscribe("traffic/#", qos=1)
    client.loop_start()
    print("[TraCI] MQTT connected")

    # SUMO
    sumo_cmd = [
        SUMO_BIN,
        "--net-file",    NET_FILE,
        "--route-files", ROUTE,
        "--step-length", "0.05",
        "--end",         "3600",
        "--no-warnings",
        "--no-step-log",
    ]
    traci.start(sumo_cmd, port=TRACI_PORT, label="adaptive")
    traci.switch("adaptive")
    print("[TraCI] SUMO connected")

    tls_ids = traci.trafficlight.getIDList()
    print(f"[TraCI] Traffic lights: {tls_ids}")

    step = 0
    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        step += 1

        if step % 20 == 0 and tls_ids:
            density  = max(_live_density.values())
            duration = calc_green(density)
            for tls_id in tls_ids:
                try:
                    traci.trafficlight.setPhaseDuration(tls_id, duration)
                except Exception:
                    pass

        if step % 400 == 0:
            n = len(traci.vehicle.getIDList())
            d = max(_live_density.values())
            g = calc_green(d)
            print(f"[TraCI] Step={step:6d} | Vehicles={n:3d} | "
                  f"Density={d:.1f} | Green={g:.0f}s")

    traci.switch("adaptive")
    traci.close()
    client.loop_stop()
    print("[TraCI] Done.")


if __name__ == "__main__":
    run()
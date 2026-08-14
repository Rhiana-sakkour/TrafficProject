"""
sumo_mosaic_traci.py — محاكاة المرور مع التحكم التكيفي
الأدوات: Eclipse MOSAIC 22.1 + SUMO 1.14.1 + Python TraCI
الوظيفة:
  1. يُشغِّل Eclipse MOSAIC الذي ينسِّق SUMO
  2. يتصل بـ SUMO عبر TraCI
  3. يقرأ كثافة المرور من RSU_03_MINI.json
  4. يضبط إشارات المرور تكيفياً
دور MOSAIC: Co-simulation Orchestrator — ينسِّق SUMO مع الإطار الأشمل
"""
import subprocess, time, json, os, traci, logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [SUMO+MOSAIC] %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

MOSAIC_DIR  = r"C:\MOSAIC"
MOSAIC_BAT  = r"C:\MOSAIC\mosaic.bat"
SCENARIO    = r"C:\TrafficProject\mini_integrated\mosaic_scenario"
SUMO_BIN    = r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe"
NET_FILE    = r"C:\TrafficProject\mini_integrated\mosaic_scenario\sumo\city.net.xml"
ROUTE_FILE  = r"C:\TrafficProject\mini_integrated\mosaic_scenario\sumo\routes_medium.rou.xml"
TRACI_PORT  = 8813
RSU_JSON    = r"C:\TrafficProject\data\rsu_json\RSU_03_MINI.json"

def read_rsu_density() -> float:
    """يقرأ الكثافة الحية من ملف RSU JSON"""
    try:
        with open(RSU_JSON) as f:
            data = json.load(f)
        return data.get("traffic_density", 0.0)
    except Exception:
        return 0.0

def adaptive_green(density: float) -> float:
    """
    معادلة التحكم التكيفي:
    density=0   → 15s (حد أدنى)
    density=100 → 60s (حد أقصى)
    """
    return min(60.0, max(15.0, 15.0 + 0.45 * density))

def launch_mosaic():
    """
    يُشغِّل Eclipse MOSAIC كـ orchestrator.
    MOSAIC يُحمِّل السيناريو ويُشغِّل SUMO تلقائياً.
    في حال عدم توفر MOSAIC، يُشغِّل SUMO مباشرةً.
    """
    mosaic_jar = os.path.join(MOSAIC_DIR, "mosaic.jar")

    if os.path.exists(mosaic_jar):
        log.info("Start Eclipse MOSAIC كـ Co-Simulation Orchestrator...")
        cmd = ["java", "-jar", mosaic_jar,
               "-s", SCENARIO, "-w", "0"]
        proc = subprocess.Popen(cmd, cwd=MOSAIC_DIR)
        log.info(f"MOSAIC PID: {proc.pid}")
        time.sleep(5)  # انتظر حتى يُشغِّل MOSAIC SUMO
        return proc
    else:
        log.warning("Start MOSAIC is unvalid now")
        cmd = [SUMO_BIN,
               "--net-file",   NET_FILE,
               "--route-files", ROUTE_FILE,
               "--remote-port", str(TRACI_PORT),
               "--step-length", "0.05",
               "--end", "3600",
               "--no-warnings"]
        proc = subprocess.Popen(cmd)
        time.sleep(3)
        return proc

def run():
    log.info("═"*55)
    log.info("SUMO + Eclipse MOSAIC + TraCI")
    log.info(f"scenario: {SCENARIO}")
    log.info(f"Adaptive Control: read from {RSU_JSON}")
    log.info("═"*55)

    proc = launch_mosaic()
    time.sleep(2)

    try:
        traci.init(port=TRACI_PORT)
        log.info("TraCI connect to SUMO ")
    except Exception as e:
        log.error(f"connection faild with TraCI: {e}")
        proc.terminate()
        return

    tls_ids = traci.trafficlight.getIDList()
    log.info(f"valid traffic light: {tls_ids}")

    step     = 0
    log_freq = 400  # كل 20 ثانية

    try:
        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            step += 1

            # التحكم التكيفي كل ثانية (20 خطوة × 0.05s)
            if step % 20 == 0 and tls_ids:
                density  = read_rsu_density()
                duration = adaptive_green(density)
                for tls_id in tls_ids:
                    try:
                        traci.trafficlight.setPhaseDuration(
                            tls_id, duration)
                    except Exception:
                        pass

            if step % log_freq == 0:
                vehicles = traci.vehicle.getIDList()
                density  = read_rsu_density()
                green_t  = adaptive_green(density)
                log.info(
                    f"Step={step:6d} | "
                    f"Vehicles={len(vehicles):3d} | "
                    f"RSU_Density={density:.1f} veh/km | "
                    f"Green={green_t:.0f}s"
                )

    except KeyboardInterrupt:
        log.info("Stop TraCI...")
    finally:
        traci.close()
        proc.terminate()
        log.info("SUMO + MOSAIC is stopped.")


if __name__ == "__main__":
    run()
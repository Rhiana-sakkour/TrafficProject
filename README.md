# TrafficProject
4th_year_project
# تصميم ومحاكاة شبكة لتقدير كثافة حركة المرور في المدن الذكية باستخدام كاميرات متعددة والتجميع الموزع

Design and implementation of a network for estimating traffic density in smart cities using multiple cameras and distributed aggregation.

## نظرة عامة
يُنفّذ هذا المشروع شبكة موزعة على مستوى المدينة لتقدير كثافة المرور في الوقت الفعلي، باستخدام
كاميرات متعددة وتجميع بيانات عبر ثلاث طبقات: الحافة (Edge)، الضباب (Fog)، والسحابة (Cloud).
كل كاميرا تعمل كعقدة حافة تُجري كشف وتتبّع المركبات محلياً، ووحدات RSU (Roadside Units) تُشكّل
طبقة تجميع وسيطة، بينما ينتج السيرفر المركزي (طبقة السحابة) التحليلات النهائية — بما فيها خريطة
حرارية (Heatmap) لازدحام المرور.

يُحاكى النظام باستخدام CARLA (محاكاة الكاميرات والمركبات)، SUMO (محاكاة حركة المرور)، وNS-3
(محاكاة الشبكة عبر WiFi وLTE)، ويدمج معالجة الصور في الوقت الفعلي مع مراقبة أداء طبقة الشبكة.

## هيكلية النظام

| الطبقة   | المكوّن           | الدور                                                                 |
|----------|-------------------|------------------------------------------------------------------------|
| Edge     | 7 كاميرات         | تشغّل YOLOv8n + SORT محلياً؛ تُصدّر JSON بحجم ~350 بايت كل 5 ثوانٍ بدل بث الفيديو الخام (نسبة ضغط تتجاوز 7700:1) |
| Fog      | 3 وحدات RSU       | كل وحدة تُجمّع بيانات 2-3 كاميرات، ما يقلّص تدفقات البيانات الصاعدة من 7 إلى 3 |
| Cloud    | سيرفر مركزي واحد  | يُجمّع بيانات وحدات RSU، وينتج التحليلات النهائية والخريطة الحرارية    |

يُحاكى الاتصال بين الكاميرا ووحدة RSU عبر UDP sockets لمحاكاة قناة لاسلكية V2I وقياس زمن
الاستجابة من طرف إلى طرف.

## الميزات الرئيسية
- كشف وتتبّع المركبات في الوقت الفعلي، مع تقدير الكثافة والسرعة
- ضغط هائل للبيانات على مستوى الحافة (ملخصات JSON بدل الفيديو الخام)
- تجميع على مستويين (RSU ← السيرفر المركزي) لتقليل حِمل الشبكة
- محاكاة شبكة WiFi وLTE عبر NS-3 لتقييم قابلية التوسّع (Scalability)
- توليد خريطة حرارية لازدحام المرور على مستوى المدينة
## التقنيات والأدوات المستخدمة

| الأداة / المكتبة      | الإصدار   | الغرض                                      |
|------------------------|-----------|----------------------------------------------|
| Python                 | 3.8.10    | لغة التطوير الأساسية                        |
| CARLA                  | 0.9.13    | بيئة محاكاة الكاميرات والمركبات             |
| YOLOv8n + SORT         | —         | كشف المركبات وتتبّعها متعدد الأجسام          |
| OpenCV                 | 4.6.0     | معالجة الصور                                |
| SUMO                   | 1.14.1    | محاكاة حركة المرور                          |
| Eclipse MOSAIC         | 22.1      | إطار عمل المحاكاة المشتركة (Co-simulation)  |
| NS-3                   | 3.35      | محاكاة الشبكة (WiFi/LTE)                    |
| Mosquitto              | 2.0.18    | وسيط MQTT (MQTT Broker)                     |
| paho-mqtt              | 1.6.1     | مكتبة عميل MQTT للبايثون                    |
| Java                   | 17        | مطلوبة لمكوّنات MOSAIC/NS-3                 |

## المتطلبات الأساسية (Prerequisites)
- Windows — لتشغيل محاكاة CARLA ومعالجة الصور من جهة الكاميرا (المرحلة 1)
- Linux (Ubuntu 20.04 عبر WSL2) — لتشغيل محاكاة الشبكة NS-3 (المرحلتان 4 و5)
- Python 3.8.10 
- Java 17 (JDK)
-  تثبيت وإعداد وسيط MQTT (Mosquitto)

##مراحل التنفيذ 
Terminal 1
mosquitto -c C:\mosquitto\mosquitto.conf -v

T2:
start "" "C:\CARLA_0.9.13\WindowsNoEditor\CarlaUE4.exe" -quality-level=Low -windowed -ResX=800 -ResY=600 -benchmark -fps=20
OR
start "" "C:\CARLA_0.9.13\WindowsNoEditor\CarlaUE4.exe" -RenderOffScreen -quality-level=Low -windowed -ResX=800 -ResY=600 -benchmark -fps=20

T3:
cd C:\TrafficProject\phase1_carla
python run_phase1.py


T4:
cd C:\TrafficProject\phase3_mosaic
python rsu_aggregation.py


T_new:     to show json Files
type C:\TrafficProject\data\rsu_json\RSU_01.json
type C:\TrafficProject\data\rsu_json\RSU_02.json
type C:\TrafficProject\data\rsu_json\RSU_03.json

T_exceptional:
cd C:\TrafficProject\phase3_mosaic
python adaptive_signal.py

T5:
cd C:\TrafficProject\phase6_server
python central_server.py


T_new:     to show dynamic heatmap
start C:\TrafficProject\data\heatmaps\heatmap_latest.html


T6:        to open ns-3 and monitoring
wsl -d Ubuntu-20.04

cd ~/ns-allinone-3.35/ns-3.35
./waf --run "traffic_wifi_sim" 2>&1 | tee ~/ns3_traffic/wifi_output.log

./waf --run "traffic_lte_sim --nRSU=3" 2>&1 | tee ~/ns3_traffic/lte_3rsu.log

T7_Scalability:
for n in 3 6 12 15 22 25 30 35; do
    echo "=== nRSU=$n ==="
    ./waf --run "traffic_lte_trace \
        --trace=/mnt/c/TrafficProject/data/ns3_results/real_rsu_trace.csv \
        --nRSU=$n" \
        2>&1 | tee ~/ns3_traffic/lte_trace_${n}rsu.log
    echo "Done: nRSU=$n"
done

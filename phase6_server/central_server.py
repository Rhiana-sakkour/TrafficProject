"""
central_server.py
=================
المرحلة الرابعة: السيرفر المركزي

المهام:
  1. الاشتراك في جميع مواضيع MQTT (traffic/#)
  2. استقبال JSON من 3 وحدات RSU
  3. توليد خريطة حرارية جغرافية تفاعلية (HTML) بـ Folium
  4. توليد خريطة حرارية إحصائية (PNG) بـ Seaborn
  5. حفظ الخرائط في C:\TrafficProject\data\heatmaps\

تدفق البيانات:
  RSU_01 → traffic/RSU_01 ─┐
  RSU_02 → traffic/RSU_02 ─┤─ MQTT Broker ─→ هذا السيرفر ─→ Heatmap
  RSU_03 → traffic/RSU_03 ─┘
"""

import json
import os
import logging
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import folium
from folium.plugins import HeatMap
import seaborn as sns
import matplotlib
matplotlib.use('Agg')   # بدون GUI — يمنع فتح نوافذ غير ضرورية
import matplotlib.pyplot as plt
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# إعداد نظام التسجيل
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
MQTT_BROKER       = "localhost"
MQTT_PORT         = 1883
MQTT_TOPIC        = "traffic/#"      # # يعني جميع المواضيع الفرعية

OUTPUT_DIR        = r"C:\TrafficProject\data\heatmaps"

# مركز الخريطة الجغرافية (متوسط مواقع RSUs)
MAP_CENTER        = [52.5203, 13.4043]
MAP_ZOOM          = 15

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# حالة الشبكة — يتجدد عند كل رسالة واردة
# المفتاح: rsu_id | القيمة: آخر payload مستلم
# ─────────────────────────────────────────────────────────────────────────────
network_state: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# توليد الخريطة الحرارية الجغرافية (Folium — HTML تفاعلي)
# ─────────────────────────────────────────────────────────────────────────────
def generate_folium_heatmap(state: dict, timestamp: str) -> str:
    """
    ينشئ خريطة HTML تفاعلية تُظهر:
    - نقاط حرارية (HeatMap) بحسب كثافة المرور
    - Markers لكل RSU مع popup يحتوي التفاصيل

    العائد: مسار ملف HTML المحفوظ
    """
    m = folium.Map(
        location=MAP_CENTER,
        zoom_start=MAP_ZOOM,
        tiles='CartoDB dark_matter'   # خلفية داكنة تُبرز الألوان الحرارية
    )

    heat_data = []   # [lat, lon, intensity]

    for rsu_id, data in state.items():
        lat     = data.get('latitude',        MAP_CENTER[0])
        lon     = data.get('longitude',       MAP_CENTER[1])
        density = data.get('traffic_density', 0.0)
        count   = data.get('vehicle_count',   0)
        speed   = data.get('avg_speed_kmh',   0.0)
        cams    = data.get('cameras_active',  [])

        # إضافة نقطة للـ HeatMap
        heat_data.append([lat, lon, density])

        # تحديد لون الـ Marker حسب مستوى الكثافة
        if density > 60:
            color = 'red'       # ازدحام شديد
        elif density > 25:
            color = 'orange'    # ازدحام متوسط
        else:
            color = 'green'     # تدفق طبيعي

        # إضافة Marker مع Popup تفصيلي
        popup_html = f"""
        <div style="font-family:monospace; font-size:13px; min-width:200px">
            <b>{rsu_id}</b><br>
            ─────────────────<br>
            🚗 عدد المركبات : <b>{count}</b><br>
            ⚡ متوسط السرعة : <b>{speed:.1f} km/h</b><br>
            📊 الكثافة      : <b>{density:.2f} veh/km</b><br>
            📷 كاميرات      : {', '.join(cams)}<br>
            🕒 {data.get('timestamp','N/A')[:19].replace('T',' ')}
        </div>
        """
        folium.CircleMarker(
            location=[lat, lon],
            radius=14,
            color='white',
            weight=1,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"{rsu_id}: {density:.1f} veh/km"
        ).add_to(m)

    # رسم طبقة الحرارة
    if heat_data:
        HeatMap(
            heat_data,
            radius=80,
            blur=50,
            max_zoom=17,
            min_opacity=0.3,
            gradient={0.2: 'blue', 0.5: 'lime', 0.8: 'orange', 1.0: 'red'}
        ).add_to(m)

    # حفظ نسخة بطابع زمني + نسخة ثابتة "latest"
    ts_clean    = timestamp.replace(':', '-').replace('.', '-')[:19]
    named_path  = os.path.join(OUTPUT_DIR, f"heatmap_{ts_clean}.html")
    latest_path = os.path.join(OUTPUT_DIR, "heatmap_latest.html")

    m.save(named_path)
    m.save(latest_path)

    return latest_path


# ─────────────────────────────────────────────────────────────────────────────
# توليد الخريطة الحرارية الإحصائية (Seaborn — PNG)
# ─────────────────────────────────────────────────────────────────────────────
def generate_seaborn_heatmap(state: dict, timestamp: str) -> str:
    """
    ينشئ صورة PNG تُقارن 3 مقاييس (عدد، سرعة، كثافة) عبر 3 RSUs.

    يُطبَّق التطبيع (0-1) لكل مقياس على حدة لجعل المقارنة
    بصرية عادلة بين مقاييس ذات وحدات مختلفة.

    العائد: مسار ملف PNG المحفوظ
    """
    rsu_ids = sorted(state.keys())
    metrics = ['vehicle_count', 'avg_speed_kmh', 'traffic_density']
    labels  = ['Vehicle Count', 'Avg Speed (km/h)', 'Density (veh/km)']

    # بناء مصفوفة البيانات
    matrix = np.zeros((len(rsu_ids), len(metrics)))
    for i, rsu_id in enumerate(rsu_ids):
        d = state[rsu_id]
        matrix[i, 0] = d.get('vehicle_count',   0.0)
        matrix[i, 1] = d.get('avg_speed_kmh',   0.0)
        matrix[i, 2] = d.get('traffic_density',  0.0)

    # تطبيع كل عمود على مداه (0 → 1)
    # يمنع هيمنة مقياس ذي قيم كبيرة (مثل density) على التصور
    matrix_norm = matrix.copy()
    for j in range(len(metrics)):
        col_max = matrix[:, j].max()
        if col_max > 0:
            matrix_norm[:, j] = matrix[:, j] / col_max

    fig, axes = plt.subplots(
        1, 2,
        figsize=(14, 5),
        gridspec_kw={'width_ratios': [2, 1]}
    )
    fig.patch.set_facecolor('#1a1a2e')

    # ── الخريطة الحرارية المُطبَّعة (يسار) ──────────────────────────
    ax1 = axes[0]
    ax1.set_facecolor('#1a1a2e')

    sns.heatmap(
        matrix_norm,
        ax=ax1,
        annot=False,
        cmap='YlOrRd',
        xticklabels=labels,
        yticklabels=rsu_ids,
        linewidths=0.5,
        linecolor='#333355',
        cbar_kws={'label': 'Normalized Value (0-1)', 'shrink': 0.8}
    )
    ax1.set_title(
        f'Traffic Density Heatmap\n{timestamp[:19].replace("T", " ")} UTC',
        color='white', fontsize=13, pad=12
    )
    ax1.tick_params(colors='white')
    ax1.xaxis.label.set_color('white')
    ax1.yaxis.label.set_color('white')

    # ── جدول القيم الفعلية (يمين) ────────────────────────────────────
    ax2 = axes[1]
    ax2.set_facecolor('#1a1a2e')
    ax2.axis('off')

    table_data = []
    col_labels = ['RSU', 'Count', 'Speed\n(km/h)', 'Density\n(veh/km)']
    for rsu_id in rsu_ids:
        d = state[rsu_id]
        table_data.append([
            rsu_id,
            str(d.get('vehicle_count', 0)),
            f"{d.get('avg_speed_kmh', 0.0):.1f}",
            f"{d.get('traffic_density', 0.0):.2f}",
        ])

    table = ax2.table(
        cellText=table_data,
        colLabels=col_labels,
        loc='center',
        cellLoc='center'
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.2, 2.0)

    # تنسيق الجدول بألوان داكنة
    for (r, c), cell in table.get_celld().items():
        cell.set_facecolor('#16213e' if r == 0 else '#0f3460')
        cell.set_text_props(color='white')
        cell.set_edgecolor('#333355')

    ax2.set_title('Live Values', color='white', fontsize=12, pad=12)

    plt.tight_layout()

    # حفظ
    ts_clean    = timestamp.replace(':', '-').replace('.', '-')[:19]
    named_path  = os.path.join(OUTPUT_DIR, f"heatmap_{ts_clean}.png")
    latest_path = os.path.join(OUTPUT_DIR, "heatmap_latest.png")

    plt.savefig(named_path,  dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    plt.savefig(latest_path, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close()

    return latest_path


# ─────────────────────────────────────────────────────────────────────────────
# توليد كلا الخريطتين معاً
# ─────────────────────────────────────────────────────────────────────────────
def generate_all_heatmaps(state: dict) -> None:
    """يُولِّد الخريطتين ويُسجِّل مساريهما."""
    if not state:
        return

    timestamp = datetime.now(timezone.utc).isoformat()

    html_path = generate_folium_heatmap(state, timestamp)
    png_path  = generate_seaborn_heatmap(state, timestamp)

    log.info(f"  📍 HTML → {html_path}")
    log.info(f"  📊 PNG  → {png_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Callbacks للـ MQTT
# ─────────────────────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info(f"[MQTT] متصل بـ {MQTT_BROKER}:{MQTT_PORT} ✓")
        client.subscribe(MQTT_TOPIC, qos=1)
        log.info(f"[MQTT] مشترك في: {MQTT_TOPIC}")
    else:
        log.error(f"[MQTT] فشل الاتصال — rc={rc}")


def on_message(client, userdata, msg):
    """
    يُستدعى عند وصول رسالة من أي RSU.

    يُحدِّث network_state ثم يُعيد رسم الخرائط.
    """
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        rsu_id  = payload.get('rsu_id', 'UNKNOWN')

        network_state[rsu_id] = payload

        log.info(
            f"[{rsu_id}] "
            f"count={payload.get('vehicle_count',0):3d} | "
            f"speed={payload.get('avg_speed_kmh',0.0):5.1f} km/h | "
            f"density={payload.get('traffic_density',0.0):6.2f} veh/km"
        )

        # أعِد رسم الخرائط عند اكتمال بيانات الـ 3 RSUs
        if len(network_state) >= 1:
            generate_all_heatmaps(network_state)

    except json.JSONDecodeError as e:
        log.error(f"[MQTT] JSON تالف من {msg.topic}: {e}")
    except Exception as e:
        log.error(f"[MQTT] خطأ في معالجة الرسالة: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# نقطة الدخول
# ─────────────────────────────────────────────────────────────────────────────
def run():
    log.info("=" * 60)
    log.info("Central Server — بدء التشغيل")
    log.info(f"مجلد الخرائط: {OUTPUT_DIR}")
    log.info("=" * 60)

    client = mqtt.Client(client_id="CentralServer")
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except ConnectionRefusedError:
        log.error("لا يمكن الاتصال — تأكد أن Mosquitto يعمل أولاً")
        return

    log.info("السيرفر يعمل — في انتظار بيانات RSU... (Ctrl+C للإيقاف)")
    client.loop_forever()


if __name__ == "__main__":
    run()
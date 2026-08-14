/*
 * traffic_wifi_sim.cc
 * ===================
 * محاكاة رابط WiFi 802.11n بين الكاميرات ووحدات RSU
 *
 * البنية:
 *   Cluster A: CAM_01 + CAM_02 → RSU_01 (Channel 1 / 2412 MHz)
 *   Cluster B: CAM_03+CAM_04+CAM_05 → RSU_02 (Channel 6 / 2437 MHz)
 *   Cluster C: CAM_06 + CAM_07 → RSU_03 (Channel 11 / 2462 MHz)
 *
 * كل cluster يعمل كمحاكاة مستقلة (Simulator::Destroy بينها)
 * لضمان العزل التام وعدم التداخل الراديوي بين الكتل.
 *
 * المقاييس المُجمَّعة:
 *   - PDR  (Packet Delivery Ratio) بالنسبة المئوية
 *   - Average Latency بالمللي ثانية
 *   - Throughput بالكيلوبت/ثانية
 *
 * الخرج: wifi_results.csv
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/wifi-module.h"
#include "ns3/mobility-module.h"
#include "ns3/internet-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("TrafficWifiSim");

/* ── نتائج كل cluster ──────────────────────────────────────────── */
struct ClusterResult {
    uint32_t tx;
    uint32_t rx;
    double   delaySum_ms;
    double   rxBytes;
    double   duration_s;
};

/* ── تشغيل محاكاة cluster واحد ─────────────────────────────────── */
ClusterResult RunCluster(
    uint32_t              nCameras,
    uint32_t              freqMHz,
    Vector                apPos,
    std::vector<Vector>   camPos,
    uint32_t              simTime_s,
    uint32_t              payloadBytes)
{
    NodeContainer apNode, staNodes;
    apNode.Create(1);
    staNodes.Create(nCameras);

    /* ── WiFi 802.11n ──────────────────────────────────────────── */
    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211n_2_4GHZ);

    /*
     * IdealWifiManager يختار أفضل معدل إرسال حسب SNR المُقدَّر.
     * اختير لاستقراره في بيئات المسافات الثابتة (كاميرات ثابتة).
     */
    wifi.SetRemoteStationManager("ns3::IdealWifiManager");

    YansWifiChannelHelper channel = YansWifiChannelHelper::Default();
    /*
     * LogDistancePropagationLossModel مع exponent=3.0:
     * يُمثّل انتشار الإشارة في البيئة الحضرية الخارجية.
     * القيمة 3.0 مُعتمَدة في أدبيات شبكات V2I الحضرية.
     */
    channel.AddPropagationLoss(
        "ns3::LogDistancePropagationLossModel",
        "Exponent", DoubleValue(3.0));

    YansWifiPhyHelper phy;
    phy.SetChannel(channel.Create());
    /*
     * تحديد التردد يضمن وجود كل cluster على قناة مستقلة
     * حتى لو شاركت نفس كائن القناة في المستقبل.
     */
    phy.Set("Frequency", UintegerValue(freqMHz));

    WifiMacHelper mac;
    Ssid ssid = Ssid("traffic-cluster");

    mac.SetType("ns3::ApWifiMac", "Ssid", SsidValue(ssid));
    NetDeviceContainer apDev = wifi.Install(phy, mac, apNode);

    mac.SetType("ns3::StaWifiMac",
                "Ssid",           SsidValue(ssid),
                "ActiveProbing",  BooleanValue(false));
    NetDeviceContainer staDev = wifi.Install(phy, mac, staNodes);

    /* ── مواضع ثابتة (بنية تحتية) ──────────────────────────────── */
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");

    Ptr<ListPositionAllocator> posAlloc =
        CreateObject<ListPositionAllocator>();
    posAlloc->Add(apPos);                          // RSU (AP)
    for (const auto& p : camPos) posAlloc->Add(p); // Cameras (STAs)

    mobility.SetPositionAllocator(posAlloc);
    NodeContainer allNodes;
    allNodes.Add(apNode);
    allNodes.Add(staNodes);
    mobility.Install(allNodes);

    /* ── طبقة الإنترنت ─────────────────────────────────────────── */
    InternetStackHelper internet;
    internet.Install(allNodes);

    Ipv4AddressHelper addr;
    addr.SetBase("10.1.0.0", "255.255.255.0");
    Ipv4InterfaceContainer apIf  = addr.Assign(apDev);
    addr.Assign(staDev);

    /* ── التطبيقات ──────────────────────────────────────────────── */
    uint16_t port = 9;

    // PacketSink على الـ RSU (AP) يستقبل من جميع الكاميرات
    PacketSinkHelper sink("ns3::UdpSocketFactory",
        InetSocketAddress(Ipv4Address::GetAny(), port));
    ApplicationContainer sinkApp = sink.Install(apNode);
    sinkApp.Start(Seconds(0.0));
    sinkApp.Stop(Seconds(simTime_s));

    /*
     * كل كاميرا ترسل payloadBytes كل 5 ثوانٍ (دورة التجميع).
     * OnTime=0.01s: انفجار إرسال قصير يُمثّل رسالة JSON.
     * OffTime=4.99s: فترة الصمت بين الدورات.
     * Stagger 0.5s بين الكاميرات لتجنب تصادم الحزم عند البداية.
     */
    for (uint32_t i = 0; i < nCameras; i++) {
        OnOffHelper onoff("ns3::UdpSocketFactory",
            InetSocketAddress(apIf.GetAddress(0), port));

        uint64_t dataRateBps =
            static_cast<uint64_t>(payloadBytes * 8.0 / 0.01);
        onoff.SetConstantRate(DataRate(dataRateBps), payloadBytes);
        onoff.SetAttribute("OnTime",
            StringValue("ns3::ConstantRandomVariable[Constant=0.01]"));
        onoff.SetAttribute("OffTime",
            StringValue("ns3::ConstantRandomVariable[Constant=4.99]"));

        ApplicationContainer app = onoff.Install(staNodes.Get(i));
        app.Start(Seconds(1.0 + i * 0.5));
        app.Stop(Seconds(simTime_s));
    }

    /* ── Flow Monitor ────────────────────────────────────────────── */
    FlowMonitorHelper fmHelper;
    Ptr<FlowMonitor> fm = fmHelper.InstallAll();

    Simulator::Stop(Seconds(simTime_s));
    Simulator::Run();

    /* ── جمع النتائج ─────────────────────────────────────────────── */
    fm->CheckForLostPackets();

    ClusterResult result = {0, 0, 0.0, 0.0, 0.0};

    for (auto& kv : fm->GetFlowStats()) {
        const FlowMonitor::FlowStats& fs = kv.second;
        result.tx        += fs.txPackets;
        result.rx        += fs.rxPackets;
        result.delaySum_ms += fs.delaySum.GetMilliSeconds();
        result.rxBytes   += fs.rxBytes;
        if (fs.timeLastRxPacket > fs.timeFirstTxPacket) {
            result.duration_s = std::max(
                result.duration_s,
                (fs.timeLastRxPacket -
                 fs.timeFirstTxPacket).GetSeconds());
        }
    }

    Simulator::Destroy();
    return result;
}

/* ── main ────────────────────────────────────────────────────────── */
int main(int argc, char* argv[])
{
    uint32_t simTime    = 300;  // ثانية
    uint32_t payloadSz  = 350;  // بايت — حجم JSON الكاميرا

    CommandLine cmd;
    cmd.AddValue("simTime",   "Simulation duration (s)", simTime);
    cmd.AddValue("payload",   "Payload size (bytes)",    payloadSz);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);

    /* ── تعريف الـ Clusters ─────────────────────────────────────── */
    struct ClusterCfg {
        std::string          rsuName;
        uint32_t             nCameras;
        uint32_t             freqMHz;    // 2412=CH1, 2437=CH6, 2462=CH11
        Vector               apPos;
        std::vector<Vector>  camPos;
    };

    std::vector<ClusterCfg> clusters = {
        {"RSU_01", 2, 2412,
         Vector(-110, 35, 8),
         {Vector(-110, 15, 8), Vector(-110, 55, 8)}},

        {"RSU_02", 3, 2437,
         Vector(-97, 32, 8),
         {Vector(-100, 15, 8), Vector(-100, 45, 8), Vector(-90, 35, 8)}},

        {"RSU_03", 2, 2462,
         Vector(-70, -10, 8),
         {Vector(-70, -55, 8), Vector(-70, 35, 8)}}
    };

    /* ── ملف CSV للنتائج ─────────────────────────────────────────── */
    std::ofstream csv("wifi_results.csv");
    csv << "rsu_id,n_cameras,channel_mhz,"
           "packets_tx,packets_rx,pdr_pct,"
           "avg_delay_ms,throughput_kbps\n";

    uint32_t totalTx = 0, totalRx = 0;

    for (const auto& cfg : clusters) {
        NS_LOG_UNCOND("\n[WiFi] Running cluster: " << cfg.rsuName
            << " (" << cfg.nCameras << " cameras, "
            << cfg.freqMHz << " MHz)");

        ClusterResult r = RunCluster(
            cfg.nCameras, cfg.freqMHz,
            cfg.apPos, cfg.camPos,
            simTime, payloadSz);

        double pdr   = (r.tx > 0) ? 100.0 * r.rx / r.tx : 0.0;
        double delay = (r.rx > 0) ? r.delaySum_ms / r.rx : 0.0;
        double tput  = (r.duration_s > 0) ?
            r.rxBytes * 8.0 / r.duration_s / 1000.0 : 0.0;

        NS_LOG_UNCOND("  TX=" << r.tx << " RX=" << r.rx
            << " PDR=" << pdr << "%"
            << " AvgDelay=" << delay << "ms"
            << " Throughput=" << tput << "kbps");

        csv << cfg.rsuName << ","
            << cfg.nCameras << ","
            << cfg.freqMHz  << ","
            << r.tx         << ","
            << r.rx         << ","
            << pdr          << ","
            << delay        << ","
            << tput         << "\n";

        totalTx += r.tx;
        totalRx += r.rx;
    }

    csv.close();

    double overallPDR = (totalTx > 0) ? 100.0 * totalRx / totalTx : 0.0;
    NS_LOG_UNCOND("\n=== WiFi Simulation Complete ===");
    NS_LOG_UNCOND("Total TX: " << totalTx << " | Total RX: " << totalRx);
    NS_LOG_UNCOND("Overall PDR: " << overallPDR << "%");
    NS_LOG_UNCOND("Results → wifi_results.csv");

    return 0;
}

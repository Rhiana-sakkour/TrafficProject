/*
 * traffic_lte_sim.cc
 * ==================
 * محاكاة رابط LTE/4G بين وحدات RSU والسيرفر المركزي
 *
 * البنية:
 *   RSU_01 (UE) ─┐
 *   RSU_02 (UE) ─┤─ eNodeB ─ EPC ─ PGW ─ Remote Host (Server)
 *   RSU_03 (UE) ─┘
 *
 * نموذج MQTT:
 *   MQTT بروتوكول Layer 7 غير موجود في NS-3 3.35.
 *   يُمثَّل بحركة TCP بحجم 500 بايت كل 5 ثوانٍ لكل RSU
 *   (مطابق لحجم JSON الـ RSU الفعلي المقاس).
 *   تقيس NS-3 PDR وLatency وThroughput على مستوى TCP/IP.
 *
 * اختبار قابلية التوسع:
 *   يقبل --nRSU=N لتغيير عدد وحدات RSU (افتراضي: 3)
 *
 * الخرج: lte_results.csv
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/lte-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/internet-module.h"
#include "ns3/applications-module.h"
#include "ns3/mobility-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/ipv4-global-routing-helper.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("TrafficLteSim");

int main(int argc, char* argv[])
{
    uint32_t nRSU       = 3;    // عدد وحدات RSU (UEs)
    uint32_t simTime    = 300;  // ثانية
    uint32_t payloadSz  = 500;  // بايت — حجم JSON الـ RSU (أكبر من JSON الكاميرا)

    CommandLine cmd;
    cmd.AddValue("nRSU",    "Number of RSU nodes (UEs)", nRSU);
    cmd.AddValue("simTime", "Simulation duration (s)",   simTime);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);

    NS_LOG_UNCOND("=== LTE Simulation | nRSU=" << nRSU
        << " simTime=" << simTime << "s ===");

    /*  LTE + EPC  */
    Ptr<LteHelper> lteHelper = CreateObject<LteHelper>();
    Ptr<PointToPointEpcHelper> epcHelper =
        CreateObject<PointToPointEpcHelper>();
    lteHelper->SetEpcHelper(epcHelper);

    /*  Remote Host (يُمثّل السيرفر المركزي)  */
    NodeContainer remoteHostContainer;
    remoteHostContainer.Create(1);
    Ptr<Node> remoteHost = remoteHostContainer.Get(0);

    InternetStackHelper internet;
    internet.Install(remoteHostContainer);

    /*
     * P2P Link بين PGW والسيرفر:
     * 100 Gbps لضمان أن اللينك هذا لا يكون عنق الزجاجة
     * (العنق الزجاجة الحقيقي هو الواجهة الجوية LTE).
     */
    PointToPointHelper p2ph;
    p2ph.SetDeviceAttribute("DataRate", DataRateValue(DataRate("100Gbps")));
    p2ph.SetDeviceAttribute("Mtu",      UintegerValue(1500));
    p2ph.SetChannelAttribute("Delay",   TimeValue(MilliSeconds(1)));

    Ptr<Node> pgw = epcHelper->GetPgwNode();
    NetDeviceContainer internetDevs = p2ph.Install(pgw, remoteHost);

    Ipv4AddressHelper ipv4h;
    ipv4h.SetBase("1.0.0.0", "255.0.0.0");
    Ipv4InterfaceContainer internetIfaces = ipv4h.Assign(internetDevs);
    Ipv4Address remoteHostAddr = internetIfaces.GetAddress(1);

    /*
     * Static Route على Remote Host للوصول لشبكة UEs (7.0.0.0/8)
     * EPC يعيِّن UEs عناوين من هذا النطاق تلقائياً.
     */
    Ipv4StaticRoutingHelper routingHelper;
    Ptr<Ipv4StaticRouting> remoteStaticRoute =
        routingHelper.GetStaticRouting(remoteHost->GetObject<Ipv4>());
    remoteStaticRoute->AddNetworkRouteTo(
        Ipv4Address("7.0.0.0"), Ipv4Mask("255.0.0.0"), 1);

    /*  eNodeB وعقد RSU  */
    NodeContainer enbNode, ueNodes;
    enbNode.Create(1);
    ueNodes.Create(nRSU);

    /*  مواضع ثابتة  */
    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");

    // eNodeB في مركز منطقة التغطية (برج اتصالات 4G)
    Ptr<ListPositionAllocator> enbAlloc =
        CreateObject<ListPositionAllocator>();
    enbAlloc->Add(Vector(0.0, 0.0, 30.0));   // ارتفاع البرج 30م
    mobility.SetPositionAllocator(enbAlloc);
    mobility.Install(enbNode);

    // RSUs موزعة في المنطقة (مواقع من مشروعنا)
    Ptr<ListPositionAllocator> ueAlloc =
        CreateObject<ListPositionAllocator>();
    for (uint32_t i = 0; i < nRSU; i++) {
        // توزيع دائري بقطر 500م حول البرج
        double angle = 2.0 * M_PI * i / nRSU;
        double radius = 300.0 + (i % 3) * 100.0;
        ueAlloc->Add(Vector(radius * cos(angle),
                            radius * sin(angle), 6.0));
    }
    mobility.SetPositionAllocator(ueAlloc);
    mobility.Install(ueNodes);

    /*  تثبيت أجهزة LTE  */
    NetDeviceContainer enbDevs = lteHelper->InstallEnbDevice(enbNode);
    NetDeviceContainer ueLteDevs = lteHelper->InstallUeDevice(ueNodes);

    internet.Install(ueNodes);

    Ipv4InterfaceContainer ueIpIfaces =
        epcHelper->AssignUeIpv4Address(ueLteDevs);

    // Default route لكل UE نحو EPC
    for (uint32_t i = 0; i < ueNodes.GetN(); ++i) {
        Ptr<Ipv4StaticRouting> ueRoute = routingHelper.GetStaticRouting(
            ueNodes.Get(i)->GetObject<Ipv4>());
        ueRoute->SetDefaultRoute(
            epcHelper->GetUeDefaultGatewayAddress(), 1);
    }

    // ربط جميع UEs بالـ eNodeB
    lteHelper->Attach(ueLteDevs, enbDevs.Get(0));

    /*  التطبيقات  */
    uint16_t port = 8080;
    Address serverAddr(InetSocketAddress(remoteHostAddr, port));

    // TCP Sink على السيرفر
    PacketSinkHelper sink("ns3::TcpSocketFactory",
        InetSocketAddress(Ipv4Address::GetAny(), port));
    ApplicationContainer sinkApps = sink.Install(remoteHost);
    sinkApps.Start(Seconds(0.0));
    sinkApps.Stop(Seconds(simTime));

    /*
     * OnOff TCP من كل RSU للسيرفر:
     * TCP اختير لأن MQTT يعمل فوقه (connection-oriented)
     * Stagger 1s بين RSUs لتجنب TCP connection storms عند البداية
     */
    for (uint32_t i = 0; i < nRSU; i++) {
        OnOffHelper onoff("ns3::TcpSocketFactory", serverAddr);
        uint64_t dataRateBps =
            static_cast<uint64_t>(payloadSz * 8.0 / 0.05);
        onoff.SetConstantRate(DataRate(dataRateBps), payloadSz);
        onoff.SetAttribute("OnTime",
            StringValue("ns3::ConstantRandomVariable[Constant=0.05]"));
        onoff.SetAttribute("OffTime",
            StringValue("ns3::ConstantRandomVariable[Constant=4.95]"));

        ApplicationContainer app = onoff.Install(ueNodes.Get(i));
        app.Start(Seconds(2.0 + i * 1.0));
        app.Stop(Seconds(simTime));
    }

    /*  Flow Monitor  */
    FlowMonitorHelper fmHelper;
    Ptr<FlowMonitor> fm = fmHelper.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    /*  جمع النتائج  */
    fm->CheckForLostPackets();
    Ptr<Ipv4FlowClassifier> classifier =
        DynamicCast<Ipv4FlowClassifier>(fmHelper.GetClassifier());

    std::ofstream csv("lte_results_" + std::to_string(nRSU) + "rsu.csv");
    csv << "nRSU,rsu_idx,packets_tx,packets_rx,"
           "pdr_pct,avg_delay_ms,throughput_kbps\n";

    uint32_t totalTx = 0, totalRx = 0;
    uint32_t rsuIdx = 1;

    for (auto& kv : fm->GetFlowStats()) {
        Ipv4FlowClassifier::FiveTuple t = classifier->FindFlow(kv.first);
        if (t.destinationPort != port) continue;

        const FlowMonitor::FlowStats& fs = kv.second;
        uint32_t tx = fs.txPackets;
        uint32_t rx = fs.rxPackets;
        double pdr   = (tx > 0) ? 100.0 * rx / tx : 0.0;
        double delay = (rx > 0) ? fs.delaySum.GetMilliSeconds() / rx : 0.0;
        double tput  = 0.0;
        if (fs.timeLastRxPacket > fs.timeFirstTxPacket) {
            tput = fs.rxBytes * 8.0 /
                (fs.timeLastRxPacket - fs.timeFirstTxPacket).GetSeconds() /
                1000.0;
        }

        NS_LOG_UNCOND("RSU_" << std::setw(2) << std::setfill('0') << rsuIdx
            << " | TX=" << tx << " RX=" << rx
            << " PDR=" << pdr << "%"
            << " Delay=" << delay << "ms"
            << " Tput=" << tput << "kbps");

        csv << nRSU << "," << rsuIdx << ","
            << tx   << "," << rx     << ","
            << pdr  << "," << delay  << ","
            << tput << "\n";

        totalTx += tx; totalRx += rx;
        rsuIdx++;
    }
    csv.close();

    double overallPDR = (totalTx > 0) ? 100.0 * totalRx / totalTx : 0.0;
    NS_LOG_UNCOND("\n=== LTE Results | nRSU=" << nRSU << " ===");
    NS_LOG_UNCOND("Total TX=" << totalTx << " RX=" << totalRx);
    NS_LOG_UNCOND("Overall PDR=" << overallPDR << "%");
    NS_LOG_UNCOND("Results → lte_results_" << nRSU << "rsu.csv");

    Simulator::Destroy();
    return 0;
}

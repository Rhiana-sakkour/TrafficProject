
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/lte-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/internet-module.h"
#include "ns3/applications-module.h"
#include "ns3/mobility-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/ipv4-static-routing-helper.h"
#include <fstream>
#include <sstream>
#include <vector>
#include <map>
#include <cmath>
#include <iomanip>
#include <algorithm>
using namespace ns3;
NS_LOG_COMPONENT_DEFINE("TrafficLteTrace");

/* ── دالة الإرسال — مستقلة (لا lambda، متوافقة NS-3 3.35) ──────── */
static void SendUdpPacket(Ptr<Socket> sock, uint32_t sz) {
    sock->Send(Create<Packet>(sz));
}

/* ── بنية سجل RSU trace ─────────────────────────────────────────── */
struct RsuEntry {
    double      wall_ts;
    std::string rsu_id;
    uint32_t    payload_bytes;
    uint32_t    seq;
};

/* ── تحميل ملف الـ trace ─────────────────────────────────────────── */
std::vector<RsuEntry> LoadRsuTrace(const std::string& path) {
    std::vector<RsuEntry> entries;
    std::ifstream f(path);
    if (!f.is_open()) {
        NS_LOG_UNCOND("ERROR: Cannot open: " << path);
        NS_LOG_UNCOND("  Run rsu_aggregation.py first to generate trace.");
        return entries;
    }
    std::string line;
    std::getline(f, line); // تخطي header
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::istringstream ss(line);
        RsuEntry e; char c;
        ss >> e.wall_ts >> c;
        std::getline(ss, e.rsu_id, ',');
        ss >> e.payload_bytes >> c >> e.seq;
        entries.push_back(e);
    }
    NS_LOG_UNCOND("[RSU Trace] Loaded " << entries.size()
        << " entries from: " << path);
    return entries;
}

int main(int argc, char* argv[])
{
    std::string tracePath =
        "/mnt/c/TrafficProject/data/ns3_results/real_rsu_trace.csv";
    uint32_t nRSU    = 3;    // للـ Scalability: 3,6,12,25,50
    uint32_t simTime = 0;    // 0 = يُحدَّد من الـ trace تلقائياً

    CommandLine cmd;
    cmd.AddValue("trace",   "RSU trace CSV path", tracePath);
    cmd.AddValue("nRSU",    "Total RSU count (for scalability)", nRSU);
    cmd.AddValue("simTime", "Override sim time (0=auto)", simTime);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);

    /* ── تحميل الـ trace ──────────────────────────────────────────── */
    std::vector<RsuEntry> trace = LoadRsuTrace(tracePath);
    if (trace.empty()) return 1;

    /* تحويل timestamps لنسبية تبدأ من الصفر */
    double t0 = trace[0].wall_ts, maxT = 0.0;
    for (size_t i = 0; i < trace.size(); i++) {
        trace[i].wall_ts -= t0;
        if (trace[i].wall_ts > maxT) maxT = trace[i].wall_ts;
    }

    /* حساب simTime التلقائي */
    uint32_t autoTime = (uint32_t)(maxT + 15.0);
    if (simTime == 0) simTime = autoTime;

    /* جمع معرفات الـ RSUs الحقيقية من الـ trace */
    std::map<std::string, uint32_t> realRsuIdx;
    for (size_t i = 0; i < trace.size(); i++) {
        if (realRsuIdx.find(trace[i].rsu_id) == realRsuIdx.end()) {
            uint32_t idx = realRsuIdx.size();
            realRsuIdx[trace[i].rsu_id] = idx;
        }
    }
    uint32_t nRealRSU = realRsuIdx.size(); // عادةً 3

    /*
     * Scalability:
     * إذا nRSU > nRealRSU: نُنشئ RSUs إضافية (virtual)
     * كل virtual RSU تنسخ نمط RSU_01 مع offset زمني صغير.
     * هذا يُحاكي تحميلاً أعلى على شبكة LTE.
     */
    uint32_t totalUEs = std::max(nRSU, nRealRSU);

    NS_LOG_UNCOND("Duration=" << maxT << "s"
        << " | SimTime=" << simTime << "s"
        << " | RealRSUs=" << nRealRSU
        << " | TotalUEs=" << totalUEs);

    /* ── إنشاء LTE + EPC ──────────────────────────────────────────── */
    /* FIX: زيادة SrsPeriodicity لدعم أكثر من 40 UE
 * القيمة 320 تسمح بـ 320 UE كحد أقصى لكل eNodeB
 * القيم المتاحة في NS-3 3.35: 2,5,10,20,40,80,160,320 */
Config::SetDefault("ns3::LteEnbRrc::SrsPeriodicity",
                   UintegerValue(320));
Config::SetDefault("ns3::LteEnbRrc::SrsPeriodicity",
                   UintegerValue(320));
Ptr<LteHelper> lteHelper = CreateObject<LteHelper>();
    Ptr<PointToPointEpcHelper> epcHelper =
        CreateObject<PointToPointEpcHelper>();
    lteHelper->SetEpcHelper(epcHelper);

    /* Remote Host (السيرفر المركزي) */
    NodeContainer remoteHostCont;
    remoteHostCont.Create(1);
    Ptr<Node> remoteHost = remoteHostCont.Get(0);
    InternetStackHelper internet;
    internet.Install(remoteHostCont);

    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", DataRateValue(DataRate("100Gbps")));
    p2p.SetDeviceAttribute("Mtu",      UintegerValue(1500));
    p2p.SetChannelAttribute("Delay",   TimeValue(MilliSeconds(1)));

    Ptr<Node>          pgw         = epcHelper->GetPgwNode();
    NetDeviceContainer inetDevs    = p2p.Install(pgw, remoteHost);

    Ipv4AddressHelper ip4h;
    ip4h.SetBase("1.0.0.0", "255.0.0.0");
    Ipv4InterfaceContainer inetIf = ip4h.Assign(inetDevs);
    Ipv4Address remoteAddr = inetIf.GetAddress(1);

    Ipv4StaticRoutingHelper routingHelper;
    Ptr<Ipv4StaticRouting> rhRoute =
        routingHelper.GetStaticRouting(remoteHost->GetObject<Ipv4>());
    rhRoute->AddNetworkRouteTo(
        Ipv4Address("7.0.0.0"), Ipv4Mask("255.0.0.0"), 1);

    /* عقد eNodeB و RSU (UEs) */
    NodeContainer enbNode, ueNodes;
    enbNode.Create(1);
    ueNodes.Create(totalUEs);

    MobilityHelper mob;
    mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");

    Ptr<ListPositionAllocator> enbPos =
        CreateObject<ListPositionAllocator>();
    enbPos->Add(Vector(0.0, 0.0, 30.0));
    mob.SetPositionAllocator(enbPos);
    mob.Install(enbNode);

    Ptr<ListPositionAllocator> uePos =
        CreateObject<ListPositionAllocator>();
    for (uint32_t i = 0; i < totalUEs; i++) {
        double angle  = 2.0 * M_PI * i / totalUEs;
        double radius = 200.0 + (i % 3) * 100.0;
        uePos->Add(Vector(
            radius * cos(angle),
            radius * sin(angle),
            6.0));
    }
    mob.SetPositionAllocator(uePos);
    mob.Install(ueNodes);

    NetDeviceContainer enbDevs  = lteHelper->InstallEnbDevice(enbNode);
    NetDeviceContainer ueLteDev = lteHelper->InstallUeDevice(ueNodes);

    internet.Install(ueNodes);
    epcHelper->AssignUeIpv4Address(ueLteDev);

    for (uint32_t i = 0; i < ueNodes.GetN(); i++) {
        Ptr<Ipv4StaticRouting> r =
            routingHelper.GetStaticRouting(
                ueNodes.Get(i)->GetObject<Ipv4>());
        r->SetDefaultRoute(epcHelper->GetUeDefaultGatewayAddress(), 1);
    }
    lteHelper->Attach(ueLteDev, enbDevs.Get(0));

    /* ── Packet Sink على السيرفر ─────────────────────────────────── */
    uint16_t serverPort = 9;
    PacketSinkHelper sink("ns3::UdpSocketFactory",
        InetSocketAddress(Ipv4Address::GetAny(), serverPort));
    ApplicationContainer sinkApp = sink.Install(remoteHost);
    sinkApp.Start(Seconds(0.0));
    sinkApp.Stop(Seconds(simTime));

    /*
     * لماذا UDP وليس TCP هنا؟
     * UDP حزمي (packet-oriented) = مناسب تماماً للمحاكاة المدفوعة بـ trace.
     * ما يُقاس هو أداء طبقة LTE (PDR، Latency، Throughput) وليس
     * سلوك TCP نفسه. LTE توفر موثوقية على مستوى link layer بغض النظر.
     */

    /* ── إنشاء socket لكل UE ─────────────────────────────────────── */
    std::map<uint32_t, Ptr<Socket> > ueSockets;  // ueIdx → socket
    for (uint32_t i = 0; i < totalUEs; i++) {
        Ptr<Socket> sock = Socket::CreateSocket(
            ueNodes.Get(i), UdpSocketFactory::GetTypeId());
        sock->Connect(InetSocketAddress(remoteAddr, serverPort));
        ueSockets[i] = sock;
    }

    /* ── جدولة حزم الـ RSUs الحقيقية من الـ trace ───────────────── */
    uint32_t scheduled = 0, skipped = 0;

    for (size_t i = 0; i < trace.size(); i++) {
        const RsuEntry& e = trace[i];

        if (realRsuIdx.find(e.rsu_id) == realRsuIdx.end()) {
            skipped++; continue;
        }
        uint32_t ueIdx = realRsuIdx[e.rsu_id];
        double   at    = e.wall_ts + 2.0;  // +2s هامش

        if (at >= simTime) { skipped++; continue; }

        Simulator::Schedule(Seconds(at), &SendUdpPacket,
                            ueSockets[ueIdx], e.payload_bytes);
        scheduled++;
    }

    /*
     * ── الـ RSUs الإضافية (Virtual) للـ Scalability ──────────────
     * إذا طُلب nRSU > nRealRSU: نأخذ نمط RSU_01 من الـ trace
     * ونُكرِّره للـ UEs الإضافية مع offset زمني 0.5s لكل UE.
     * هذا يُحاكي المنافسة على نطاق LTE تحت أحمال متزايدة.
     */
    if (totalUEs > nRealRSU) {
        /* استخرج حزم RSU_01 كـ pattern أساسي */
        std::vector<std::pair<double,uint32_t> > basePattern;
        for (size_t i = 0; i < trace.size(); i++) {
            if (trace[i].rsu_id == "RSU_01") {
                basePattern.push_back(
                    std::make_pair(trace[i].wall_ts,
                                   trace[i].payload_bytes));
            }
        }

        for (uint32_t v = nRealRSU; v < totalUEs; v++) {
            double offset = (v - nRealRSU + 1) * 0.5;
            for (size_t p = 0; p < basePattern.size(); p++) {
                double at = basePattern[p].first + 2.0 + offset;
                if (at >= simTime) continue;
                Simulator::Schedule(Seconds(at), &SendUdpPacket,
                                    ueSockets[v],
                                    basePattern[p].second);
                scheduled++;
            }
        }
    }

    NS_LOG_UNCOND("[RSU Trace] Scheduled=" << scheduled
        << " Skipped=" << skipped
        << " VirtualRSUs=" << (totalUEs > nRealRSU ? totalUEs-nRealRSU : 0));

    /* ── FlowMonitor ─────────────────────────────────────────────── */
    FlowMonitorHelper      fmHelper;
    Ptr<FlowMonitor>       fm = fmHelper.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();
    fm->CheckForLostPackets();

    Ptr<Ipv4FlowClassifier> classifier =
        DynamicCast<Ipv4FlowClassifier>(fmHelper.GetClassifier());

    /* ── تحليل النتائج ───────────────────────────────────────────── */
    std::string csvName =
        "lte_trace_results_" + std::to_string(totalUEs) + "rsu.csv";
    std::ofstream csv(csvName);
    csv << "nRSU,rsu_idx,rsu_type,packets_tx,packets_rx,"
           "pdr_pct,avg_delay_ms,throughput_kbps\n";

    uint32_t totalTx = 0, totalRx = 0, flowIdx = 1;

    std::map<FlowId, FlowMonitor::FlowStats> stats = fm->GetFlowStats();
    for (std::map<FlowId, FlowMonitor::FlowStats>::iterator
         it = stats.begin(); it != stats.end(); ++it) {

        Ipv4FlowClassifier::FiveTuple t = classifier->FindFlow(it->first);

        /* فلترة: فقط RSU → Server (dest port 9) */
        if (t.destinationPort != serverPort) continue;

        const FlowMonitor::FlowStats& fs = it->second;
        uint32_t tx = fs.txPackets;
        uint32_t rx = fs.rxPackets;

        double pdr   = (tx > 0) ? 100.0 * rx / tx : 0.0;
        double delay = (rx > 0) ?
            fs.delaySum.GetMilliSeconds() / rx : 0.0;
        double tput  = 0.0;
        if (fs.timeLastRxPacket > fs.timeFirstTxPacket) {
            tput = fs.rxBytes * 8.0 /
                (fs.timeLastRxPacket -
                 fs.timeFirstTxPacket).GetSeconds() / 1000.0;
        }

        /* تحديد نوع الـ RSU: حقيقي أم virtual */
        std::string rsuType = (flowIdx <= nRealRSU) ? "real" : "virtual";

        std::ostringstream rsuName;
        rsuName << "RSU_"
                << std::setw(2) << std::setfill('0') << flowIdx;

        NS_LOG_UNCOND(rsuName.str()
            << " [" << rsuType << "]"
            << "  TX=" << tx
            << "  RX=" << rx
            << "  PDR=" << pdr << "%"
            << "  Delay=" << delay << "ms"
            << "  Tput=" << tput << "kbps");

        csv << totalUEs << "," << flowIdx << "," << rsuType << ","
            << tx   << "," << rx    << ","
            << pdr  << "," << delay << "," << tput << "\n";

        totalTx += tx;
        totalRx += rx;
        flowIdx++;
    }
    csv.close();
    Simulator::Destroy();

    double overall = (totalTx > 0) ? 100.0 * totalRx / totalTx : 0.0;
    NS_LOG_UNCOND("\n=== LTE Trace-Driven | nRSU=" << totalUEs << " ===");
    NS_LOG_UNCOND("Trace entries: " << trace.size()
        << " | Real RSUs: " << nRealRSU
        << " | Virtual RSUs: "
        << (totalUEs > nRealRSU ? totalUEs - nRealRSU : 0));
    NS_LOG_UNCOND("TX=" << totalTx
        << "  RX=" << totalRx
        << "  PDR=" << overall << "%");
    NS_LOG_UNCOND("Results -> " << csvName);

    return 0;
}

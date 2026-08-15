
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/wifi-module.h"
#include "ns3/mobility-module.h"
#include "ns3/internet-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"
#include <fstream>
#include <sstream>
#include <vector>
#include <map>
#include <algorithm>
using namespace ns3;
NS_LOG_COMPONENT_DEFINE("TraceWifiSim");

static void SendPacket(Ptr<Socket> sock, uint32_t sz) {
    sock->Send(Create<Packet>(sz));
}

struct TraceEntry {
    double wall_ts;
    std::string cam_id, rsu_id;
    uint32_t payload_bytes, seq;
};

std::vector<TraceEntry> LoadTrace(const std::string& path) {
    std::vector<TraceEntry> entries;
    std::ifstream f(path);
    if (!f.is_open()) {
        NS_LOG_UNCOND("ERROR: Cannot open: " << path);
        return entries;
    }
    std::string line;
    std::getline(f, line);
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::istringstream ss(line);
        TraceEntry e; char c;
        ss >> e.wall_ts >> c;
        std::getline(ss, e.cam_id, ',');
        std::getline(ss, e.rsu_id, ',');
        ss >> e.payload_bytes >> c >> e.seq;
        entries.push_back(e);
    }
    NS_LOG_UNCOND("[Trace] Loaded " << entries.size()
        << " packets from: " << path);
    return entries;
}

int main(int argc, char* argv[]) {
    std::string tracePath =
        "/mnt/c/TrafficProject/data/ns3_results/real_traffic_trace.csv";
    CommandLine cmd;
    cmd.AddValue("trace", "Trace CSV path", tracePath);
    cmd.Parse(argc, argv);
    Time::SetResolution(Time::NS);

    std::vector<TraceEntry> trace = LoadTrace(tracePath);
    if (trace.empty()) { return 1; }

    double t0 = trace[0].wall_ts, maxT = 0.0;
    for (size_t i = 0; i < trace.size(); i++) {
        trace[i].wall_ts -= t0;
        if (trace[i].wall_ts > maxT) maxT = trace[i].wall_ts;
    }
    double simTime = maxT + 15.0;
    NS_LOG_UNCOND("Duration=" << maxT << "s SimTime="
        << simTime << "s Packets=" << trace.size());

    struct Cluster {
        std::string rsuName;
        std::vector<std::string> camNames;
        std::string subnetBase;
        Vector rsuPos;
        std::vector<Vector> camPositions;
    };

    std::vector<Cluster> clusters;

    Cluster c1; c1.rsuName="RSU_01"; c1.subnetBase="10.1.";
    c1.camNames.push_back("CAM_01"); c1.camNames.push_back("CAM_02");
    c1.rsuPos=Vector(-110,35,8);
    c1.camPositions.push_back(Vector(-110,15,8));
    c1.camPositions.push_back(Vector(-110,55,8));
    clusters.push_back(c1);

    Cluster c2; c2.rsuName="RSU_02"; c2.subnetBase="10.2.";
    c2.camNames.push_back("CAM_03");
    c2.camNames.push_back("CAM_04");
    c2.camNames.push_back("CAM_05");
    c2.rsuPos=Vector(-97,32,8);
    c2.camPositions.push_back(Vector(-100,15,8));
    c2.camPositions.push_back(Vector(-100,45,8));
    c2.camPositions.push_back(Vector(-90,35,8));
    clusters.push_back(c2);

    Cluster c3; c3.rsuName="RSU_03"; c3.subnetBase="10.3.";
    c3.camNames.push_back("CAM_06"); c3.camNames.push_back("CAM_07");
    c3.rsuPos=Vector(-70,-10,8);
    c3.camPositions.push_back(Vector(-70,-55,8));
    c3.camPositions.push_back(Vector(-70,35,8));
    clusters.push_back(c3);

    std::map<std::string, Ptr<Socket> > camSockets;
    InternetStackHelper internet;
    FlowMonitorHelper fmHelper;
    uint32_t ipBase = 1;
    uint16_t port = 9;

    for (size_t ci = 0; ci < clusters.size(); ci++) {
        const Cluster& cl = clusters[ci];
        NodeContainer rsuNode, cNodes;
        rsuNode.Create(1);
        cNodes.Create(cl.camNames.size());

        WifiHelper wifi;
        wifi.SetStandard(WIFI_STANDARD_80211n_2_4GHZ);
        /*
         * FIX: MinstrelHtWifiManager بدلاً من MinstrelWifiManager
         * MinstrelWifiManager: يدعم فقط Legacy rates (802.11a/b/g)
         * MinstrelHtWifiManager: يدعم HT rates المطلوبة لـ 802.11n
         * استخدام Manager غير متوافق مع المعيار → crash فوري
         */
        wifi.SetRemoteStationManager("ns3::MinstrelHtWifiManager");

        YansWifiChannelHelper ch;
        ch.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
        ch.AddPropagationLoss("ns3::LogDistancePropagationLossModel",
                              "Exponent", DoubleValue(3.0));
        ch.AddPropagationLoss("ns3::NakagamiPropagationLossModel",
                              "m0",DoubleValue(1.0),
                              "m1",DoubleValue(1.0),
                              "m2",DoubleValue(1.0));
        YansWifiPhyHelper phy;
        phy.SetChannel(ch.Create());

        WifiMacHelper mac;
        std::string ssidStr = "itsnet_" + cl.rsuName;
        Ssid ssid(ssidStr);
        mac.SetType("ns3::ApWifiMac","Ssid",SsidValue(ssid));
        NetDeviceContainer rsuDev = wifi.Install(phy, mac, rsuNode);
        mac.SetType("ns3::StaWifiMac","Ssid",SsidValue(ssid),
                    "ActiveProbing",BooleanValue(false));
        NetDeviceContainer camDevs = wifi.Install(phy, mac, cNodes);

        MobilityHelper mob;
        mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");
        Ptr<ListPositionAllocator> pos =
            CreateObject<ListPositionAllocator>();
        pos->Add(cl.rsuPos);
        for (size_t pi=0; pi<cl.camPositions.size(); pi++)
            pos->Add(cl.camPositions[pi]);
        mob.SetPositionAllocator(pos);
        NodeContainer all; all.Add(rsuNode); all.Add(cNodes);
        mob.Install(all);

        internet.Install(all);
        Ipv4AddressHelper addr;
        std::ostringstream base;
        base << "10." << ipBase << ".0.0";
        addr.SetBase(base.str().c_str(),"255.255.255.0");
        Ipv4InterfaceContainer rsuIf = addr.Assign(rsuDev);
        addr.Assign(camDevs);
        ipBase++;

        Ipv4Address rsuIP = rsuIf.GetAddress(0);

        PacketSinkHelper sink("ns3::UdpSocketFactory",
            InetSocketAddress(Ipv4Address::GetAny(),port));
        ApplicationContainer sinkApp = sink.Install(rsuNode);
        sinkApp.Start(Seconds(0.0));
        sinkApp.Stop(Seconds(simTime));

        for (uint32_t si=0; si<cl.camNames.size(); si++) {
            Ptr<Socket> sock = Socket::CreateSocket(
                cNodes.Get(si), UdpSocketFactory::GetTypeId());
            sock->Connect(InetSocketAddress(rsuIP,port));
            camSockets[cl.camNames[si]] = sock;
        }
    }

    uint32_t scheduled=0, skipped=0;
    for (size_t i=0; i<trace.size(); i++) {
        const TraceEntry& e = trace[i];
        std::map<std::string,Ptr<Socket> >::iterator it =
            camSockets.find(e.cam_id);
        if (it==camSockets.end()) { skipped++; continue; }
        double at = e.wall_ts + 2.0;
        if (at >= simTime) { skipped++; continue; }
        Simulator::Schedule(Seconds(at), &SendPacket,
                            it->second, e.payload_bytes);
        scheduled++;
    }
    NS_LOG_UNCOND("[Trace] Scheduled=" << scheduled
        << " Skipped=" << skipped);

    Ptr<FlowMonitor> fm = fmHelper.InstallAll();
    Simulator::Stop(Seconds(simTime));
    Simulator::Run();
    fm->CheckForLostPackets();

    Ptr<Ipv4FlowClassifier> classifier =
        DynamicCast<Ipv4FlowClassifier>(fmHelper.GetClassifier());

    std::ofstream csv("wifi_trace_results.csv");
    csv << "cluster,subnet,packets_tx,packets_rx,"
           "pdr_pct,avg_delay_ms,throughput_kbps\n";

    uint32_t grandTx=0, grandRx=0;

    for (size_t ci=0; ci<clusters.size(); ci++) {
        const Cluster& cl = clusters[ci];
        uint32_t tx=0,rx=0;
        double dsum=0,rbytes=0,dur=0;

        std::map<FlowId,FlowMonitor::FlowStats> stats = fm->GetFlowStats();
        for (std::map<FlowId,FlowMonitor::FlowStats>::iterator
             it=stats.begin(); it!=stats.end(); ++it) {
            Ipv4FlowClassifier::FiveTuple t =
                classifier->FindFlow(it->first);
            std::ostringstream oss;
            t.sourceAddress.Print(oss);
            if (oss.str().rfind(cl.subnetBase,0)!=0) continue;
            const FlowMonitor::FlowStats& fs = it->second;
            tx     += fs.txPackets;
            rx     += fs.rxPackets;
            dsum   += fs.delaySum.GetMilliSeconds();
            rbytes += fs.rxBytes;
            if (fs.timeLastRxPacket > fs.timeFirstTxPacket) {
                double d=(fs.timeLastRxPacket-
                          fs.timeFirstTxPacket).GetSeconds();
                if(d>dur) dur=d;
            }
        }

        double pdr   = tx>0 ? 100.0*rx/tx : 0.0;
        double delay = rx>0 ? dsum/rx : 0.0;
        double tput  = dur>0 ? rbytes*8.0/dur/1000.0 : 0.0;

        NS_LOG_UNCOND(cl.rsuName
            << " TX="<<tx<<" RX="<<rx
            <<" PDR="<<pdr<<"%"
            <<" Delay="<<delay<<"ms"
            <<" Tput="<<tput<<"kbps");

        csv<<cl.rsuName<<","<<cl.subnetBase<<"x,"
           <<tx<<","<<rx<<","
           <<pdr<<","<<delay<<","<<tput<<"\n";

        grandTx+=tx; grandRx+=rx;
    }
    csv.close();
    Simulator::Destroy();

    double overall = grandTx>0 ? 100.0*grandRx/grandTx : 0.0;
    NS_LOG_UNCOND("\n=== Trace-Driven WiFi Complete ===");
    NS_LOG_UNCOND("TX="<<grandTx<<" RX="<<grandRx
        <<" PDR="<<overall<<"%");
    NS_LOG_UNCOND("Results -> wifi_trace_results.csv");
    return 0;
}

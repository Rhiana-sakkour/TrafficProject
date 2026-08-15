
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/wifi-module.h"
#include "ns3/lte-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/internet-module.h"
#include "ns3/applications-module.h"
#include "ns3/mobility-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/netanim-module.h"
#include "ns3/ipv4-static-routing-helper.h"
#include <fstream>
#include <sstream>
#include <vector>
#include <cmath>
#include <iomanip>
using namespace ns3;
NS_LOG_COMPONENT_DEFINE("MiniIntegrated");

static void SendPacket(Ptr<Socket> sock, uint32_t sz) {
    if (sock) sock->Send(Create<Packet>(sz));
}

struct TraceEntry {
    double wall_ts; std::string cam_id, rsu_id;
    uint32_t payload_bytes, seq;
};

std::vector<TraceEntry> LoadTrace(const std::string& path) {
    std::vector<TraceEntry> v;
    std::ifstream f(path);
    if (!f.is_open()) return v;
    std::string line; std::getline(f, line);
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::istringstream ss(line); TraceEntry e; char c;
        ss >> e.wall_ts >> c;
        std::getline(ss, e.cam_id, ',');
        std::getline(ss, e.rsu_id, ',');
        ss >> e.payload_bytes >> c >> e.seq;
        if (e.cam_id == "CAM_06") v.push_back(e);
    }
    return v;
}

int main(int argc, char* argv[])
{
    std::string tracePath =
        "/mnt/c/TrafficProject/data/ns3_results/real_traffic_trace.csv";
    uint32_t simTime = 120;
    CommandLine cmd;
    cmd.AddValue("trace",   "Trace CSV", tracePath);
    cmd.AddValue("simTime", "Duration",  simTime);
    cmd.Parse(argc, argv);
    Time::SetResolution(Time::NS);

    std::vector<TraceEntry> trace = LoadTrace(tracePath);
    bool hasTrace = !trace.empty();
    if (hasTrace) {
        double t0=trace[0].wall_ts, maxT=0;
        for (size_t i=0;i<trace.size();i++){
            trace[i].wall_ts-=t0;
            if(trace[i].wall_ts>maxT) maxT=trace[i].wall_ts;
        }
        if(maxT+15.0>(double)simTime) simTime=(uint32_t)(maxT+15.0);
        NS_LOG_UNCOND("[Trace] CAM_06 entries="<<trace.size()
            <<" Duration="<<maxT<<"s");
    } else {
        NS_LOG_UNCOND("[Trace] Not found - using synthetic schedule");
    }
    NS_LOG_UNCOND("SimTime="<<simTime<<"s");

    /* ════════════════════════════════════════════════════════════
     * الخطوة 1: إنشاء العقد
     * CAM_06 (node 0), RSU_03 (node 1) — منفصلتان تماماً
     * ════════════════════════════════════════════════════════════ */
    Ptr<Node> camNode = CreateObject<Node>();  // CAM_06
    Ptr<Node> rsuNode = CreateObject<Node>();  // RSU_03
    Ptr<Node> srvNode = CreateObject<Node>();  // Server

    /* ════════════════════════════════════════════════════════════
     * الخطوة 2: Internet Stack على جميع العقد مرة واحدة فقط
     * FIX: سبب الكراش = تثبيت inet مرتين على rsuNode
     *      الحل = نثبِّت مرة واحدة على الثلاثة هنا
     * ════════════════════════════════════════════════════════════ */
    InternetStackHelper inet;
    inet.Install(camNode);
    inet.Install(rsuNode);
    inet.Install(srvNode);

    /* ════════════════════════════════════════════════════════════
     * الخطوة 3: المواضع الفيزيائية
     * ════════════════════════════════════════════════════════════ */
    MobilityHelper mob;
    mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");

    Ptr<ListPositionAllocator> pos = CreateObject<ListPositionAllocator>();
    pos->Add(Vector(-70.0,-55.0,8.0));  // CAM_06 (من camera_config.py)
    pos->Add(Vector(-70.0,-10.0,8.0));  // RSU_03 (45م شمالاً)
    pos->Add(Vector(0.0,0.0,0.0));      // Server

    mob.SetPositionAllocator(pos);
    NodeContainer all3;
    all3.Add(camNode); all3.Add(rsuNode); all3.Add(srvNode);
    mob.Install(all3);

    /* ════════════════════════════════════════════════════════════
     * PART A: WiFi 802.11n — CAM_06 → RSU_03 (45 متر)
     * ════════════════════════════════════════════════════════════ */
    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211n_2_4GHZ);
    /* MinstrelHtWifiManager: متوافق مع 802.11n HT rates
     * MinstrelWifiManager (legacy) → crash لأنه لا يدعم HT */
    wifi.SetRemoteStationManager("ns3::MinstrelHtWifiManager");

    /* LogDistance(n=3.0) + Nakagami(m=1=Rayleigh):
     * نفس نموذج traffic_trace_wifi.cc الذي أعطى PDR=13.65% عند 45م */
    YansWifiChannelHelper ch;
    ch.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");
    ch.AddPropagationLoss("ns3::LogDistancePropagationLossModel",
                          "Exponent",DoubleValue(3.0));
    ch.AddPropagationLoss("ns3::NakagamiPropagationLossModel",
                          "m0",DoubleValue(1.0),
                          "m1",DoubleValue(1.0),
                          "m2",DoubleValue(1.0));
    YansWifiPhyHelper phy;
    phy.SetChannel(ch.Create());

    WifiMacHelper mac;
    Ssid ssid("itsnet_mini");
    mac.SetType("ns3::ApWifiMac","Ssid",SsidValue(ssid));
    NetDeviceContainer rsuWifiDev = wifi.Install(phy, mac, rsuNode);
    mac.SetType("ns3::StaWifiMac","Ssid",SsidValue(ssid),
                "ActiveProbing",BooleanValue(false));
    NetDeviceContainer camWifiDev = wifi.Install(phy, mac, camNode);

    Ipv4AddressHelper wAddr;
    wAddr.SetBase("10.1.0.0","255.255.255.0");
    Ipv4InterfaceContainer rsuWifiIf = wAddr.Assign(rsuWifiDev);
    wAddr.Assign(camWifiDev);

    /* Sink على RSU_03 لاستقبال بيانات WiFi */
    uint16_t wifiPort = 9;
    PacketSinkHelper wSinkH("ns3::UdpSocketFactory",
        InetSocketAddress(Ipv4Address::GetAny(),wifiPort));
    ApplicationContainer wSink = wSinkH.Install(rsuNode);
    wSink.Start(Seconds(0)); wSink.Stop(Seconds(simTime));

    /* Socket إرسال: CAM_06 → RSU_03 عبر WiFi */
    Ptr<Socket> camSock = Socket::CreateSocket(
        camNode, UdpSocketFactory::GetTypeId());
    camSock->Connect(InetSocketAddress(rsuWifiIf.GetAddress(0),wifiPort));

    uint32_t wSched=0;
    if (hasTrace) {
        for (size_t i=0;i<trace.size();i++){
            double at=trace[i].wall_ts+2.0;
            if(at>=simTime) continue;
            Simulator::Schedule(Seconds(at),&SendPacket,
                                camSock,trace[i].payload_bytes);
            wSched++;
        }
    } else {
        uint32_t n=(uint32_t)((simTime-4.0)/5.0);
        for(uint32_t i=0;i<n;i++){
            Simulator::Schedule(Seconds(2.0+i*5.0),
                                &SendPacket,camSock,250);
            wSched++;
        }
    }
    NS_LOG_UNCOND("[WiFi] Scheduled="<<wSched<<" pkts | 45m Rayleigh");

    /* ════════════════════════════════════════════════════════════
     * PART B: LTE/4G — RSU_03 → Central Server
     * FIX: لا inet.Install هنا — تم بالفعل في الخطوة 2
     * ════════════════════════════════════════════════════════════ */
    Config::SetDefault("ns3::LteEnbRrc::SrsPeriodicity",
                   UintegerValue(320));
Ptr<LteHelper>             lteH=CreateObject<LteHelper>();
    Ptr<PointToPointEpcHelper> epc=CreateObject<PointToPointEpcHelper>();
    lteH->SetEpcHelper(epc);

    Ptr<Node> pgw=epc->GetPgwNode();

    /* P2P backhaul: PGW → Server */
    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate",DataRateValue(DataRate("100Gbps")));
    p2p.SetDeviceAttribute("Mtu",     UintegerValue(1500));
    p2p.SetChannelAttribute("Delay",  TimeValue(MilliSeconds(1)));
    NetDeviceContainer inetDev=p2p.Install(pgw,srvNode);
    p2p.EnablePcap("mini-backhaul",inetDev);  // → Wireshark

    Ipv4AddressHelper ip4;
    ip4.SetBase("1.0.0.0","255.0.0.0");
    Ipv4InterfaceContainer inetIf=ip4.Assign(inetDev);
    Ipv4Address srvAddr=inetIf.GetAddress(1);

    /* Static route على Server نحو UE subnet */
    Ipv4StaticRoutingHelper rHelper;
    Ptr<Ipv4StaticRouting> sRoute=
        rHelper.GetStaticRouting(srvNode->GetObject<Ipv4>());
    sRoute->AddNetworkRouteTo(
        Ipv4Address("7.0.0.0"),Ipv4Mask("255.0.0.0"),1);

    /* eNodeB */
    Ptr<Node> enbNode=CreateObject<Node>();
    mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    Ptr<ListPositionAllocator> ep=CreateObject<ListPositionAllocator>();
    ep->Add(Vector(0.0,0.0,30.0));
    mob.SetPositionAllocator(ep);
    mob.Install(enbNode);

    /* RSU_03 كـ LTE UE */
    NodeContainer enbCont; enbCont.Add(enbNode);
    NodeContainer ueCont;  ueCont.Add(rsuNode);
    NetDeviceContainer enbDev=lteH->InstallEnbDevice(enbCont);
    NetDeviceContainer ueDev =lteH->InstallUeDevice(ueCont);

    /* FIX: لا inet.Install(ueCont) هنا — تم بالفعل أعلاه */
    epc->AssignUeIpv4Address(ueDev);

    /* Default route لـ RSU_03 عبر LTE */
    Ptr<Ipv4StaticRouting> rsuRoute=
        rHelper.GetStaticRouting(rsuNode->GetObject<Ipv4>());
    rsuRoute->SetDefaultRoute(epc->GetUeDefaultGatewayAddress(),1);

    lteH->Attach(ueDev,enbDev.Get(0));
    lteH->EnableTraces();  // DlPhyStats.txt, UlPhyStats.txt ...

    /* Sink على Server لاستقبال MQTT sim */
    uint16_t ltePort=8080;
    PacketSinkHelper lSinkH("ns3::UdpSocketFactory",
        InetSocketAddress(Ipv4Address::GetAny(),ltePort));
    ApplicationContainer lSink=lSinkH.Install(srvNode);
    lSink.Start(Seconds(0)); lSink.Stop(Seconds(simTime));

    /* Socket إرسال: RSU_03 → Server عبر LTE */
    Ptr<Socket> rsuSock=Socket::CreateSocket(
        rsuNode,UdpSocketFactory::GetTypeId());
    rsuSock->Connect(InetSocketAddress(srvAddr,ltePort));

    uint32_t lSched=0;
    if (hasTrace) {
        for (size_t i=0;i<trace.size();i++){
            double at=trace[i].wall_ts+3.0;  // +1s تجميع RSU
            if(at>=simTime) continue;
            Simulator::Schedule(Seconds(at),&SendPacket,
                                rsuSock,trace[i].payload_bytes*2);
            lSched++;
        }
    } else {
        uint32_t n=(uint32_t)((simTime-5.0)/5.0);
        for(uint32_t i=0;i<n;i++){
            Simulator::Schedule(Seconds(3.0+i*5.0),
                                &SendPacket,rsuSock,500);
            lSched++;
        }
    }
    NS_LOG_UNCOND("[LTE ] Scheduled="<<lSched<<" pkts | 4G EPC");

    /* ════════════════════════════════════════════════════════════
     * NetAnim — 5 عقد مرئية بالألوان
     * ════════════════════════════════════════════════════════════ */
    AnimationInterface anim("mini_animation.xml");

    anim.SetConstantPosition(camNode,  100.0, 200.0);
    anim.SetConstantPosition(rsuNode,  300.0, 200.0);
    anim.SetConstantPosition(srvNode,  550.0, 200.0);
    anim.SetConstantPosition(enbNode,  300.0, 370.0);
    anim.SetConstantPosition(pgw,      430.0, 370.0);

    anim.UpdateNodeDescription(camNode,"CAM_06 [Edge]");
    anim.UpdateNodeColor(camNode,255,140,0);       // برتقالي
    anim.UpdateNodeSize(camNode->GetId(),20,20);

    anim.UpdateNodeDescription(rsuNode,"RSU_03 [Fog]");
    anim.UpdateNodeColor(rsuNode,30,120,255);      // أزرق
    anim.UpdateNodeSize(rsuNode->GetId(),22,22);

    anim.UpdateNodeDescription(srvNode,"Server [Cloud]");
    anim.UpdateNodeColor(srvNode,220,30,30);       // أحمر
    anim.UpdateNodeSize(srvNode->GetId(),20,20);

    anim.UpdateNodeDescription(enbNode,"eNodeB (4G)");
    anim.UpdateNodeColor(enbNode,0,200,80);        // أخضر

    anim.UpdateNodeDescription(pgw,"PGW/EPC");
    anim.UpdateNodeColor(pgw,220,200,0);           // أصفر

    /* ════════════════════════════════════════════════════════════
     * FlowMonitor
     * ════════════════════════════════════════════════════════════ */
    FlowMonitorHelper fmH;
    Ptr<FlowMonitor>  fm=fmH.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();
    fm->CheckForLostPackets();

    Ptr<Ipv4FlowClassifier> cls=
        DynamicCast<Ipv4FlowClassifier>(fmH.GetClassifier());

    std::ofstream csv("mini_integrated_results.csv");
    csv<<"link,layer_src,layer_dst,protocol,"
         "packets_tx,packets_rx,pdr_pct,"
         "avg_delay_ms,throughput_kbps\n";

    uint32_t totalTx=0,totalRx=0;
    std::map<FlowId,FlowMonitor::FlowStats> stats=fm->GetFlowStats();
    for(std::map<FlowId,FlowMonitor::FlowStats>::iterator
        it=stats.begin();it!=stats.end();++it){
        if(it->second.txPackets==0) continue;
        Ipv4FlowClassifier::FiveTuple t=cls->FindFlow(it->first);
        std::string link,lsrc,ldst;
        if(t.destinationPort==wifiPort){
            link="WiFi_EdgeToFog"; lsrc="Edge"; ldst="Fog";
        } else if(t.destinationPort==ltePort){
            link="LTE_FogToCloud"; lsrc="Fog";  ldst="Cloud";
        } else continue;

        const FlowMonitor::FlowStats& fs=it->second;
        uint32_t tx=fs.txPackets,rx=fs.rxPackets;
        double pdr=(tx>0)?100.0*rx/tx:0.0;
        double delay=(rx>0)?fs.delaySum.GetMilliSeconds()/rx:0.0;
        double tput=0.0;
        if(fs.timeLastRxPacket>fs.timeFirstTxPacket)
            tput=fs.rxBytes*8.0/
                (fs.timeLastRxPacket-fs.timeFirstTxPacket).GetSeconds()/1000.0;

        NS_LOG_UNCOND("["<<link<<"]"
            <<" TX="<<tx<<" RX="<<rx
            <<" PDR="<<pdr<<"%"
            <<" Delay="<<delay<<"ms"
            <<" Tput="<<tput<<"kbps");

        csv<<link<<","<<lsrc<<","<<ldst<<",UDP,"
           <<tx<<","<<rx<<","<<pdr<<","<<delay<<","<<tput<<"\n";
        totalTx+=tx; totalRx+=rx;
    }
    csv.close();
    Simulator::Destroy();

    double overall=(totalTx>0)?100.0*totalRx/totalTx:0.0;
    NS_LOG_UNCOND("\n╔══ Mini Integrated Results ══════════════╗");
    NS_LOG_UNCOND("║ WiFi (Edge→Fog): 45m Rayleigh ~13-20% PDR");
    NS_LOG_UNCOND("║ LTE  (Fog→Cloud): 4G EPC ~100% PDR");
    NS_LOG_UNCOND("║ TX="<<totalTx<<" RX="<<totalRx
        <<" Overall PDR="<<overall<<"%");
    NS_LOG_UNCOND("╚═════════════════════════════════════════╝");
    NS_LOG_UNCOND("NetAnim → mini_animation.xml");
    NS_LOG_UNCOND("Results → mini_integrated_results.csv");
    NS_LOG_UNCOND("PCAP    → mini-backhaul-*.pcap");
    return 0;
}

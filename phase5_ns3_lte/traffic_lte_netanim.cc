
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/lte-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/internet-module.h"
#include "ns3/applications-module.h"
#include "ns3/mobility-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/ipv4-static-routing-helper.h"
#include "ns3/netanim-module.h"
#include <fstream>
#include <sstream>
#include <cmath>
#include <iomanip>
using namespace ns3;
NS_LOG_COMPONENT_DEFINE("TrafficLteNetAnim");
int main(int argc, char* argv[]) {
    uint32_t nRSU=3, simTime=300, payload=500;
    CommandLine cmd;
    cmd.AddValue("nRSU","Number of RSU nodes",nRSU);
    cmd.AddValue("simTime","Duration (s)",simTime);
    cmd.Parse(argc,argv);
    Time::SetResolution(Time::NS);
    NS_LOG_UNCOND("=== LTE+NetAnim | nRSU="<<nRSU<<" simTime="<<simTime<<"s ===");
    Ptr<LteHelper> lteHelper=CreateObject<LteHelper>();
    Ptr<PointToPointEpcHelper> epcHelper=CreateObject<PointToPointEpcHelper>();
    lteHelper->SetEpcHelper(epcHelper);
    NodeContainer remoteHostCont; remoteHostCont.Create(1);
    Ptr<Node> remoteHost=remoteHostCont.Get(0);
    InternetStackHelper internet; internet.Install(remoteHostCont);
    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate",DataRateValue(DataRate("100Gbps")));
    p2p.SetDeviceAttribute("Mtu",UintegerValue(1500));
    p2p.SetChannelAttribute("Delay",TimeValue(MilliSeconds(1)));
    Ptr<Node> pgw=epcHelper->GetPgwNode();
    NetDeviceContainer inetDevs=p2p.Install(pgw,remoteHost);
    /* CHANGE 1: تسجيل PCAP على رابط P2P backhaul
     * يُولِّد: lte-backhaul-0-0.pcap و lte-backhaul-0-1.pcap
     * يُفتح ببرنامج Wireshark لعرض كل حزمة TCP بالتفاصيل */
    p2p.EnablePcap("lte-backhaul", inetDevs);
    Ipv4AddressHelper ip4h; ip4h.SetBase("1.0.0.0","255.0.0.0");
    Ipv4InterfaceContainer inetIf=ip4h.Assign(inetDevs);
    Ipv4Address remoteAddr=inetIf.GetAddress(1);
    Ipv4StaticRoutingHelper routingHelper;
    Ptr<Ipv4StaticRouting> rhRoute=routingHelper.GetStaticRouting(remoteHost->GetObject<Ipv4>());
    rhRoute->AddNetworkRouteTo(Ipv4Address("7.0.0.0"),Ipv4Mask("255.0.0.0"),1);
    NodeContainer enbNode,ueNodes; enbNode.Create(1); ueNodes.Create(nRSU);
    MobilityHelper mob; mob.SetMobilityModel("ns3::ConstantPositionMobilityModel");
    Ptr<ListPositionAllocator> enbPos=CreateObject<ListPositionAllocator>();
    enbPos->Add(Vector(0.0,0.0,30.0));
    mob.SetPositionAllocator(enbPos); mob.Install(enbNode);
    Ptr<ListPositionAllocator> uePos=CreateObject<ListPositionAllocator>();
    for(uint32_t i=0;i<nRSU;i++){
        double angle=2.0*M_PI*i/nRSU;
        double radius=200.0+(i%3)*100.0;
        uePos->Add(Vector(radius*cos(angle),radius*sin(angle),6.0));
    }
    mob.SetPositionAllocator(uePos); mob.Install(ueNodes);
    NetDeviceContainer enbDevs=lteHelper->InstallEnbDevice(enbNode);
    NetDeviceContainer ueLteDev=lteHelper->InstallUeDevice(ueNodes);
    internet.Install(ueNodes);
    epcHelper->AssignUeIpv4Address(ueLteDev);
    for(uint32_t i=0;i<ueNodes.GetN();i++){
        Ptr<Ipv4StaticRouting> r=routingHelper.GetStaticRouting(ueNodes.Get(i)->GetObject<Ipv4>());
        r->SetDefaultRoute(epcHelper->GetUeDefaultGatewayAddress(),1);
    }
    lteHelper->Attach(ueLteDev,enbDevs.Get(0));
    /* CHANGE 2: تسجيل إحصاءات LTE PHY/MAC/RLC/PDCP
     * يُولِّد: DlPhyStats.txt, UlPhyStats.txt, DlMacStats.txt ...
     * تحتوي SINR, CQI, MCS, RB allocation لكل UE في كل TTI (1ms) */
    lteHelper->EnableTraces();
    uint16_t serverPort=8080;
    Address serverAddr(InetSocketAddress(remoteAddr,serverPort));
    PacketSinkHelper sink("ns3::TcpSocketFactory",InetSocketAddress(Ipv4Address::GetAny(),serverPort));
    ApplicationContainer sinkApp=sink.Install(remoteHost);
    sinkApp.Start(Seconds(0.0)); sinkApp.Stop(Seconds(simTime));
    for(uint32_t i=0;i<nRSU;i++){
        OnOffHelper onoff("ns3::TcpSocketFactory",serverAddr);
        onoff.SetConstantRate(DataRate("80000bps"),payload);
        onoff.SetAttribute("OnTime",StringValue("ns3::ConstantRandomVariable[Constant=0.05]"));
        onoff.SetAttribute("OffTime",StringValue("ns3::ConstantRandomVariable[Constant=4.95]"));
        ApplicationContainer app=onoff.Install(ueNodes.Get(i));
        app.Start(Seconds(2.0+i*1.0)); app.Stop(Seconds(simTime));
    }
    /* CHANGE 3: AnimationInterface — يُسجِّل الطوبولوجيا وحركة الحزم
     * الملف lte_animation.xml يُفتح ببرنامج NetAnim على Windows
     * يعرض: مواقع العقد، الروابط، تدفق الحزم بالألوان والزمن */
    AnimationInterface anim("lte_animation.xml");
    anim.SetConstantPosition(remoteHost, 600.0, 0.0);
    anim.SetConstantPosition(pgw,        400.0, 0.0);
    /* ألوان العقد */
    anim.UpdateNodeDescription(enbNode.Get(0), "eNodeB");
    anim.UpdateNodeColor(enbNode.Get(0), 0, 200, 0);    /* أخضر */
    anim.UpdateNodeSize(enbNode.Get(0)->GetId(), 20, 20);
    anim.UpdateNodeDescription(pgw, "PGW/EPC");
    anim.UpdateNodeColor(pgw, 200, 200, 0);             /* أصفر */
    anim.UpdateNodeDescription(remoteHost, "CentralServer");
    anim.UpdateNodeColor(remoteHost, 200, 0, 0);        /* أحمر */
    anim.UpdateNodeSize(remoteHost->GetId(), 20, 20);
    for(uint32_t i=0;i<nRSU;i++){
        std::ostringstream desc;
        desc<<"RSU_"<<std::setw(2)<<std::setfill('0')<<(i+1);
        anim.UpdateNodeDescription(ueNodes.Get(i),desc.str());
        anim.UpdateNodeColor(ueNodes.Get(i), 0, 0, 200); /* أزرق */
        anim.UpdateNodeSize(ueNodes.Get(i)->GetId(), 12, 12);
    }
    FlowMonitorHelper fmHelper; Ptr<FlowMonitor> fm=fmHelper.InstallAll();
    Simulator::Stop(Seconds(simTime)); Simulator::Run();
    fm->CheckForLostPackets();
    Ptr<Ipv4FlowClassifier> classifier=DynamicCast<Ipv4FlowClassifier>(fmHelper.GetClassifier());
    std::string csvName="lte_netanim_results_"+std::to_string(nRSU)+"rsu.csv";
    std::ofstream csv(csvName);
    csv<<"nRSU,rsu_idx,packets_tx,packets_rx,pdr_pct,avg_delay_ms,throughput_kbps\n";
    uint32_t totalTx=0,totalRx=0,rsuIdx=1;
    std::map<FlowId,FlowMonitor::FlowStats> stats=fm->GetFlowStats();
    for(std::map<FlowId,FlowMonitor::FlowStats>::iterator it=stats.begin();it!=stats.end();++it){
        Ipv4FlowClassifier::FiveTuple t=classifier->FindFlow(it->first);
        if(t.destinationPort!=serverPort) continue;
        const FlowMonitor::FlowStats& fs=it->second;
        uint32_t tx=fs.txPackets, rx=fs.rxPackets;
        double pdr=(tx>0)?100.0*rx/tx:0.0;
        double delay=(rx>0)?fs.delaySum.GetMilliSeconds()/rx:0.0;
        double tput=0.0;
        if(fs.timeLastRxPacket>fs.timeFirstTxPacket)
            tput=fs.rxBytes*8.0/(fs.timeLastRxPacket-fs.timeFirstTxPacket).GetSeconds()/1000.0;
        std::ostringstream rn; rn<<"RSU_"<<std::setw(2)<<std::setfill('0')<<rsuIdx;
        NS_LOG_UNCOND(rn.str()<<" TX="<<tx<<" RX="<<rx<<" PDR="<<pdr<<"% Delay="<<delay<<"ms Tput="<<tput<<"kbps");
        csv<<nRSU<<","<<rsuIdx<<","<<tx<<","<<rx<<","<<pdr<<","<<delay<<","<<tput<<"\n";
        totalTx+=tx; totalRx+=rx; rsuIdx++;
    }
    csv.close(); Simulator::Destroy();
    double overall=(totalTx>0)?100.0*totalRx/totalTx:0.0;
    NS_LOG_UNCOND("\n=== LTE+NetAnim Done | nRSU="<<nRSU<<" PDR="<<overall<<"% ===");
    NS_LOG_UNCOND("CSV    -> "<<csvName);
    NS_LOG_UNCOND("NetAnim-> lte_animation.xml");
    NS_LOG_UNCOND("PCAP   -> lte-backhaul-*.pcap");
    return 0;
}

// Independent host-side reference: flat output indices, int64 accumulation.
// Only the read-only RTL inspection port is used; no task dispatch here.
#include <algorithm>
#include <array>
#include <limits>

static void VerifyComplex(DieBench& tb,unsigned rows,unsigned scenario,
                          const std::string& trace_path,unsigned stalls) {
  auto read=[&](unsigned offset){return int32_t(tb.InspectWord(offset));};
  auto check_input=[&](unsigned base,unsigned count,auto formula) {
    for(unsigned i=0;i<count;++i)
      if(read(base+4*i)!=formula(i))throw std::runtime_error("complex input changed/initialized incorrectly");
  };
  auto ga=[](unsigned i){return int((i*3+1)%13)-6;};
  auto gb=[](unsigned i){return int((i*5+2)%11)-5;};
  auto cx=[](unsigned i){return int((i*7+3)%17)-8;};
  auto cw=[](unsigned i){return int((i*3+2)%7)-3;};
  check_input(GEMM_A,rows*GEMM_K,ga);check_input(GEMM_B,GEMM_K*GEMM_N,gb);
  check_input(GEMM_BIAS,GEMM_N,[](unsigned i){return int(i)-6;});
  check_input(CONV_X,CONV_IH*CONV_IW*CONV_CI,cx);
  check_input(CONV_W,CONV_CO*3*3*CONV_CI,cw);
  check_input(CONV_BIAS,CONV_CO,[](unsigned i){return int(i)*5-8;});
  uint32_t checksum=0;
  std::vector<int32_t> outputs[2];
  unsigned zeros[2]={0,0},positive[2]={0,0};
  for(unsigned op=0;op<2;++op) {
    const unsigned width=op?CONV_OW*CONV_CO:GEMM_N;
    for(unsigned index=0;index<rows*width;++index) {
      const unsigned row=index/width;
      int64_t expected;
      if(!op) {
        const unsigned col=index%GEMM_N;
        expected=int(col)-6;
        for(unsigned k=0;k<GEMM_K;++k)expected+=int64_t(ga(row*GEMM_K+k))*gb(k*GEMM_N+col);
      } else {
        const unsigned co=index%CONV_CO,x=(index/CONV_CO)%CONV_OW;
        expected=int(co)*5-8;
        // Flatten the reduction in the independent reference, unlike device loops.
        for(unsigned tap=0;tap<18;++tap) {
          const unsigned ci=tap%2,kx=(tap/2)%3,ky=tap/6;
          expected+=int64_t(cx(((row+ky)*10+x+kx)*2+ci))*cw(co*18+tap);
        }
      }
      expected=std::max<int64_t>(0,expected);
      const int32_t actual=read((op?CONV_Y:GEMM_Y)+4*index);
      if(actual!=expected)throw std::runtime_error("complex output mismatch op="+std::to_string(op+1)+
        " index="+std::to_string(index)+" expected="+std::to_string(expected)+" actual="+std::to_string(actual));
      outputs[op].push_back(actual);checksum+=uint32_t(actual);
      if(actual==0)++zeros[op];else ++positive[op];
    }
    if(!zeros[op] || !positive[op])throw std::runtime_error("test does not cover both ReLU branches");
    if(tb.InspectWord((op?CONV_Y:GEMM_Y)-4)!=0xa5a5a5a5 ||
       tb.InspectWord((op?CONV_Y:GEMM_Y)+4*rows*width)!=0x5a5a5a5a)
      throw std::runtime_error("complex output guard overwritten");
  }
  for(unsigned core=0;core<4;++core)
    if(tb.InspectWord(COMPLEX_DONE+4*core)!=1)throw std::runtime_error("missing complex done");
  if(tb.model.io_checksum!=checksum)throw std::runtime_error("complex CPU checksum mismatch");
  std::array<unsigned,4> state{},row_events{},rvv_config{};
  const std::array<uint32_t,4> macs{tb.model.io_rvvMacIssued_0,tb.model.io_rvvMacIssued_1,
                                  tb.model.io_rvvMacIssued_2,tb.model.io_rvvMacIssued_3};
  std::array<uint32_t,2> begin{UINT32_MAX,UINT32_MAX},end{};
  unsigned active[2]={0,0};bool overlap=false;unsigned progress_during_overlap[2]={0,0};
  uint32_t last_cycle=0;
  for(const auto& e:tb.events) {
    const unsigned op=e.op-1,lane=e.core%2;
    const unsigned first=lane*(rows/2)+std::min(lane,rows%2),count=rows/2+(lane<rows%2);
    if(e.cycle<=last_cycle)throw std::runtime_error("device trace timestamp order");
    last_cycle=e.cycle;
    if(e.event==TRACE_START) {
      if(state[e.core] || e.detail!=first)throw std::runtime_error("invalid START trace");
      state[e.core]=1;++active[op];begin[op]=std::min(begin[op],e.cycle);
    } else if(e.event==TRACE_RVV_CONFIG) {
      if(state[e.core]!=1 || rvv_config[e.core] || row_events[e.core] || e.detail!=((16u<<8)|4u))
        throw std::runtime_error("RVV must report VLEN=128 and e32,m1 VL=4");
      rvv_config[e.core]=1;
    } else if(e.event==TRACE_ROW) {
      if(state[e.core]!=1 || !rvv_config[e.core] || e.detail!=first+row_events[e.core] || row_events[e.core]>=count)
        throw std::runtime_error("invalid ROW_DONE trace");
      ++row_events[e.core];
      if(active[1-op])++progress_during_overlap[op];
    } else {
      if(state[e.core]!=1 || !rvv_config[e.core] || row_events[e.core]!=count || e.detail!=count)
        throw std::runtime_error("invalid END trace");
      state[e.core]=2;--active[op];end[op]=std::max(end[op],e.cycle);
    }
    overlap|=active[0] && active[1];
  }
  for(auto s:state)if(s!=2)throw std::runtime_error("missing device START/END trace");
  for(unsigned core=0;core<4;++core) {
    const unsigned count=rows/2+((core%2)<rows%2);
    const unsigned expected=count*(core<2?GEMM_K*((GEMM_N+3)/4):CONV_OW*18*((CONV_CO+3)/4));
    if(macs[core]!=expected)throw std::runtime_error("hardware RVV MAC dispatch count mismatch core="+
      std::to_string(core)+" expected="+std::to_string(expected)+" actual="+std::to_string(macs[core]));
    std::cout<<"[RVV hardware] Coral"<<core<<" vector MAC dispatched="<<macs[core]
             <<" (vmacc.vx/vmadd.vx)\n";
  }
  if(scenario==1 && (overlap || begin[1]<=end[0]))throw std::runtime_error("serial tasks overlapped");
  if(scenario==2 && (!overlap || (rows>=7 &&
      (!progress_during_overlap[0] || !progress_during_overlap[1]))))
    throw std::runtime_error("parallel overlap/progress check failed");
  if(!trace_path.empty()) {
    std::ofstream out(trace_path);
    if(!out)throw std::runtime_error("cannot create trace JSON");
    out<<"{\"compute_backend\":\"RVV\",\"vlen_bits\":128,\"rvv_mac_dispatched\":["
       <<macs[0]<<','<<macs[1]<<','<<macs[2]<<','<<macs[3]
       <<"],\"scenario\":\""<<(scenario==1?"serial":"parallel")<<"\",\"rows\":"<<rows<<",\"events\":[";
    for(unsigned i=0;i<tb.events.size();++i) {
      const auto& e=tb.events[i];if(i)out<<',';
      out<<"{\"cycle\":"<<e.cycle<<",\"op\":"<<e.op<<",\"core\":"<<e.core
         <<",\"event\":"<<e.event<<",\"detail\":"<<e.detail<<'}';
    }
    out<<"],\"outputs\":[";
    for(unsigned op=0;op<2;++op) {
      if(op)out<<',';out<<'[';
      for(unsigned i=0;i<outputs[op].size();++i){if(i)out<<',';out<<outputs[op][i];}
      out<<']';
    }
    out<<"]}\n";
    out.flush();if(!out)throw std::runtime_error("cannot write trace JSON");
  }
  std::cout<<"{\"result\":\"PASS\",\"control_cpu\":\"PicoRV32 RV32IM RTL\",\"coral_cores\":4,"
    <<"\"compute_backend\":\"RVV\",\"vlen_bits\":128,\"rvv_mac_dispatched\":["
    <<macs[0]<<','<<macs[1]<<','<<macs[2]<<','<<macs[3]<<"],"
    <<"\"rtl_shared_sram\":true,\"cpu_runs_runtime\":true,\"testbench_dispatch\":false,"
    <<"\"scenario\":\""<<(scenario==1?"serial":"parallel")<<"\",\"rows\":"<<rows
    <<",\"operators\":2,\"launches\":4,\"completion_mask\":15,\"checksum\":"<<checksum
    <<",\"gemm_checked\":"<<outputs[0].size()<<",\"conv_checked\":"<<outputs[1].size()
    <<",\"overlap_observed\":"<<(overlap?"true":"false")
    <<",\"gemm_rows_during_overlap\":"<<progress_during_overlap[0]
    <<",\"conv_rows_during_overlap\":"<<progress_during_overlap[1]
    <<",\"trace_events\":"<<tb.events.size()<<",\"stall_period\":"<<stalls<<"}\n";
}

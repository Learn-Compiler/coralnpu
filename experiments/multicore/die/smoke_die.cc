#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>
#include "../device/complex_abi.h"
#include "VCoralDie.h"
#include "verilated.h"

// The testbench has no Coral host AXI driver, RAM BFM, or kernel dispatch path.
// It can only reset/load the CPU boot RAM, release reset, and inspect outputs.
class DieBench {
 public:
  struct Event { uint32_t cycle,op,core,event,detail; };
  std::vector<Event> events;
  VerilatedContext context;
  VCoralDie model{&context};
  uint64_t cycles=0,started_at=0,deadline;
  bool running=false;
  DieBench(uint64_t limit,unsigned stalls):deadline(limit) {
    model.io_aclk=0;model.io_aresetn=0;model.io_run=0;
    model.io_bootEn=0;model.io_bootIndex=0;model.io_bootData=0;
    model.io_inspectEn=0;model.io_inspectIndex=0;model.io_stallPeriod=stalls;
    for(unsigned i=0;i<20;++i)Tick();
    model.io_aresetn=1;
    for(unsigned i=0;i<20;++i)Tick();
  }
  void Tick() {
    if(running && cycles-started_at>=deadline)
      throw std::runtime_error("simulated cycle deadline exceeded");
    model.io_aclk=0;context.timeInc(1);model.eval();
    model.io_aclk=1;context.timeInc(1);model.eval();++cycles;
    if(model.io_traceValid) {
      const uint32_t word=model.io_traceData;
      if((word&0xf0000000u)!=0xc0000000u)throw std::runtime_error("invalid device trace magic");
      Event e{model.io_traceCycle,(word>>24)&15,(word>>20)&15,(word>>16)&15,word&65535};
      if(e.core>3 || e.op!=(e.core<2?OP_GEMM:OP_CONV) || e.event<1 || e.event>4)
        throw std::runtime_error("invalid device trace fields");
      events.push_back(e);
      std::cout<<"[cycle "<<e.cycle<<"] Coral"<<e.core<<" "
               <<(e.op==OP_GEMM?"GEMM+bias+ReLU":"Conv3x3+bias+ReLU")<<" ";
      if(e.event==TRACE_RVV_CONFIG)
        std::cout<<"RVV_CONFIG vlenb="<<(e.detail>>8)<<" vl(e32,m1)="<<(e.detail&255)<<std::endl;
      else std::cout<<(e.event==TRACE_START?"START first_row=":e.event==TRACE_ROW?"ROW_DONE row=":"END rows=")
                    <<e.detail<<std::endl;
    }
    if(model.io_cpuTrap)throw std::runtime_error("control CPU trapped");
    if(context.gotFinish())throw std::runtime_error("unexpected RTL finish");
  }
  void Boot(const std::vector<uint8_t>& image,unsigned elements,unsigned rounds,unsigned mode,unsigned wait_cycles,
            unsigned scenario,unsigned rows) {
    if(image.empty() || image.size()>49152)throw std::runtime_error("invalid CPU boot binary size");
    model.io_bootEn=1;
    for(unsigned word=0;word<16384;++word) {
      uint32_t value=0;
      for(unsigned byte=0;byte<4;++byte)
        if(4*word+byte<image.size())value|=uint32_t(image[4*word+byte])<<(8*byte);
      if(word==0xff00/4)value=elements;
      if(word==0xff04/4)value=rounds;
      if(word==0xff08/4)value=mode;
      if(word==0xff0c/4)value=wait_cycles;
      if(word==0xff10/4)value=scenario;
      if(word==0xff14/4)value=rows;
      model.io_bootIndex=word;model.io_bootData=value;Tick();
    }
    model.io_bootEn=0;
    model.io_run=1;started_at=cycles;running=true;
  }
  uint32_t InspectWord(uint32_t offset) {
    if(offset>=16384 || offset%4)throw std::runtime_error("invalid SRAM inspection offset");
    model.io_inspectIndex=offset/16;model.io_inspectEn=1;Tick();
    if(!model.io_inspectValid)throw std::runtime_error("SRAM inspection not acknowledged");
    const uint32_t result=model.io_inspectData[(offset%16)/4];
    model.io_inspectEn=0;Tick();return result;
  }
};

#include "complex_check.h"

int main(int argc,char** argv) {
  try {
    std::string boot,trace;
    unsigned count=37,rounds=1,mode=0,stalls=3,wait_cycles=1000000;
    unsigned scenario=0,rows=7;
    uint64_t limit=2000000;
    for(int i=1;i<argc;++i) {
      const std::string name=argv[i];
      if(i+1==argc)throw std::runtime_error("missing option value");
      if(name=="--boot"){boot=argv[++i];continue;}
      if(name=="--trace-json"){trace=argv[++i];continue;}
      const uint64_t n=std::stoull(argv[++i]);
      if(name=="--elements" && n<=128)count=unsigned(n);
      else if(name=="--rounds" && n>=1 && n<=2)rounds=unsigned(n);
      else if(name=="--mode" && n<=3)mode=unsigned(n);
      else if(name=="--scenario" && n<=2)scenario=unsigned(n);
      else if(name=="--rows" && n>=1 && n<=8)rows=unsigned(n);
      else if(name=="--wait-cycles" && n>=1 && n<=1000000)wait_cycles=unsigned(n);
      else if(name=="--stall-period" && n<=1000)stalls=unsigned(n);
      else if(name=="--max-cycles" && n>=40)limit=n;
      else throw std::runtime_error("invalid option/range");
    }
    std::ifstream input(boot,std::ios::binary);
    if(!input)throw std::runtime_error("cannot open CPU boot binary");
    const std::vector<uint8_t> image((std::istreambuf_iterator<char>(input)),{});
    DieBench tb(limit,stalls);tb.Boot(image,count,rounds,mode,wait_cycles,scenario,rows);
    while(tb.model.io_finish==0)tb.Tick();
    for(unsigned i=0;i<16;++i)tb.Tick();
    if(tb.model.io_finish!=0x600d0001) {
      std::cerr<<"controller reported failure code=0x"<<std::hex<<tb.model.io_finish
               <<" coral_fault=0x"<<unsigned(tb.model.io_coralFault)
               <<" trap_mask=0x"<<unsigned(tb.model.io_trapMask)
               <<" started=0x"<<unsigned(tb.model.io_started)<<std::dec<<'\n';
      return 1;
    }
    if(tb.model.io_started!=15 || tb.model.io_coralWfi!=15 || tb.model.io_coralFault ||
       tb.model.io_busError || tb.model.io_trapMask || tb.model.io_decodeErrors!=(mode==1?1u:0u) ||
       tb.model.io_launches!=(scenario?4:4*rounds))
      throw std::runtime_error("incorrect die control/completion state");
    if(scenario) {
      VerifyComplex(tb,rows,scenario,trace,stalls);
      tb.model.final();return 0;
    }
    uint32_t checksum=0;
    for(unsigned i=0;i<count;++i) {
      if(tb.InspectWord(4*i)!=3*i+1 || tb.InspectWord(0x400+4*i)!=7*i+2 ||
         tb.InspectWord(0x800+4*i)!=10*i+3)
        throw std::runtime_error("incorrect independently inspected SRAM result/input");
      const uint32_t second=tb.InspectWord(0x1000+4*i);
      if(second!=(rounds==2?17*i+5:0xdeadbeefu))
        throw std::runtime_error("incorrect second operator result");
      checksum+=rounds==2?second:10*i+3;
    }
    for(unsigned core=0;core<4;++core)
      if(tb.InspectWord(0xc00+4*core)!=1)throw std::runtime_error("missing Coral completion flag");
    for(uint32_t offset:{0x7fcu,0xffcu})
      if(tb.InspectWord(offset)!=0xa5a5a5a5)throw std::runtime_error("output prefix guard overwritten");
    for(uint32_t offset:{0x800u,0x1000u})
      if(tb.InspectWord(offset+4*count)!=0x5a5a5a5a)throw std::runtime_error("output suffix guard overwritten");
    if(tb.model.io_checksum!=checksum)throw std::runtime_error("CPU checksum mismatch");
    std::cout<<"{\"result\":\"PASS\",\"control_cpu\":\"PicoRV32 RV32IM RTL\",\"coral_cores\":4,"
             <<"\"compute_backend\":\"RVV\","
             <<"\"rtl_shared_sram\":true,\"cpu_runs_runtime\":true,\"testbench_dispatch\":false,"
             <<"\"elements\":"<<count<<",\"operators\":"<<rounds<<",\"launches\":"<<tb.model.io_launches
             <<",\"completion_mask\":15,\"checksum\":"<<checksum
             <<",\"cpu_cycles\":"<<tb.cycles-tb.started_at<<",\"stall_period\":"<<stalls
             <<",\"decode_errors\":"<<tb.model.io_decodeErrors
             <<",\"shared_read_beats\":"<<tb.model.io_readBeats
             <<",\"shared_write_beats\":"<<tb.model.io_writeBeats<<"}\n";
    tb.model.final();return 0;
  }catch(const std::exception& error) {
    std::cerr<<"FAIL: "<<error.what()<<'\n';return 1;
  }
}

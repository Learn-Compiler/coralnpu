#include <array>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "firmware.h"
#include "runtime.h"
#include "verilated.h"
#if defined(CLUSTER_QUAD) && defined(CLUSTER_RVV)
#include "VQuadCoralRvv.h"
using Model = VQuadCoralRvv;
#elif defined(CLUSTER_QUAD)
#include "VQuadCoral.h"
using Model = VQuadCoral;
#elif defined(CLUSTER_RVV)
#include "VDualCoralRvv.h"
using Model = VDualCoralRvv;
#else
#include "VDualCoral.h"
using Model = VDualCoral;
#endif

struct Address { uint32_t address=0; unsigned id=0, len=0, size=0, burst=0, beat=0; };

class Bench {
 public:
#ifdef CLUSTER_QUAD
  static constexpr unsigned kCores=4;
#else
  static constexpr unsigned kCores=2;
#endif
  static constexpr uint32_t kBase=0x20000000;
  VerilatedContext context;
  Model model{&context};
  std::array<uint8_t, 16384> ram{};
  uint64_t cycles=0, reads=0, writes=0;
  const uint64_t deadline;
  const unsigned stall_period;
  std::array<bool,kCores> host_aw{},host_w{},host_b{};
  bool read_busy=false, write_busy=false, b_valid=false;
  Address rd,wr;
  unsigned b_id=0,b_resp=0;

  Bench(uint64_t limit,unsigned stalls):deadline(limit),stall_period(stalls) {
    model.io_aresetn=0;
#define INIT_HOST(N) \
    model.io_hosts_##N##_read_addr_valid=0; \
    model.io_hosts_##N##_read_data_ready=1; \
    model.io_hosts_##N##_write_addr_valid=0; \
    model.io_hosts_##N##_write_data_valid=0; \
    model.io_hosts_##N##_write_resp_ready=1;
    INIT_HOST(0) INIT_HOST(1)
#ifdef CLUSTER_QUAD
    INIT_HOST(2) INIT_HOST(3)
#endif
#undef INIT_HOST
    for(int i=0;i<20;++i) Tick();
    model.io_aresetn=1;
    for(int i=0;i<20;++i) Tick();
  }
  void CheckRange(uint32_t a,unsigned size) const {
    if(a<kBase || uint64_t(a)-kBase+size>ram.size())
      throw std::runtime_error("shared-memory access outside mapped RAM");
  }
  uint32_t ReadWord(uint32_t a) const {
    CheckRange(a,4); uint32_t x=0;
    for(unsigned i=0;i<4;++i)x|=uint32_t(ram[a-kBase+i])<<(8*i);
    return x;
  }
  void WriteWord(uint32_t a,uint32_t x) {
    CheckRange(a,4);
    for(unsigned i=0;i<4;++i)ram[a-kBase+i]=uint8_t(x>>(8*i));
  }
  static void Advance(Address& a) { if(a.burst==1)a.address+=1u<<a.size; ++a.beat; }
  void Tick() {
    if(cycles>=deadline)throw std::runtime_error("simulated cycle deadline exceeded");
    const bool allow=stall_period==0 || cycles%stall_period!=0;
    model.io_aclk=0;
    model.io_memory_read_addr_ready=!read_busy && allow;
    model.io_memory_read_data_valid=read_busy;
    model.io_memory_read_data_bits_id=rd.id;
    model.io_memory_read_data_bits_resp=0;
    model.io_memory_read_data_bits_last=rd.beat==rd.len;
    for(int i=0;i<4;++i)model.io_memory_read_data_bits_data[i]=0;
    if(read_busy) {
      unsigned bytes=1u<<rd.size;
      CheckRange(rd.address,bytes);
      if(bytes>16 || rd.address%16+bytes>16)throw std::runtime_error("unsupported crossing AXI beat");
      for(unsigned i=0;i<bytes;++i) {
        unsigned lane=rd.address%16+i;
        model.io_memory_read_data_bits_data[lane/4]|=uint32_t(ram[rd.address-kBase+i])<<(8*(lane%4));
      }
    }
    model.io_memory_write_addr_ready=!write_busy && !b_valid && allow;
    model.io_memory_write_data_ready=write_busy && !b_valid && allow;
    model.io_memory_write_resp_valid=b_valid;
    model.io_memory_write_resp_bits_id=b_id;
    model.io_memory_write_resp_bits_resp=b_resp;
    context.timeInc(1);model.eval();
    const bool ar=model.io_memory_read_addr_valid && model.io_memory_read_addr_ready;
    const bool r=model.io_memory_read_data_valid && model.io_memory_read_data_ready;
    const bool aw=model.io_memory_write_addr_valid && model.io_memory_write_addr_ready;
    const bool w=model.io_memory_write_data_valid && model.io_memory_write_data_ready;
    const bool b=model.io_memory_write_resp_valid && model.io_memory_write_resp_ready;
    Address next_rd{model.io_memory_read_addr_bits_addr,model.io_memory_read_addr_bits_id,
                    model.io_memory_read_addr_bits_len,model.io_memory_read_addr_bits_size,
                    model.io_memory_read_addr_bits_burst,0};
    Address next_wr{model.io_memory_write_addr_bits_addr,model.io_memory_write_addr_bits_id,
                    model.io_memory_write_addr_bits_len,model.io_memory_write_addr_bits_size,
                    model.io_memory_write_addr_bits_burst,0};
    const unsigned strobe=model.io_memory_write_data_bits_strb;
    std::array<uint32_t,4> data{};
    for(int i=0;i<4;++i)data[i]=model.io_memory_write_data_bits_data[i];
    const bool last=model.io_memory_write_data_bits_last;
#define SAMPLE_HOST(N) \
    host_aw[N]=model.io_hosts_##N##_write_addr_valid && model.io_hosts_##N##_write_addr_ready; \
    host_w[N]=model.io_hosts_##N##_write_data_valid && model.io_hosts_##N##_write_data_ready; \
    host_b[N]=model.io_hosts_##N##_write_resp_valid && model.io_hosts_##N##_write_resp_ready; \
    if(host_b[N] && model.io_hosts_##N##_write_resp_bits_resp!=0) \
      throw std::runtime_error("host AXI write failed");
    SAMPLE_HOST(0) SAMPLE_HOST(1)
#ifdef CLUSTER_QUAD
    SAMPLE_HOST(2) SAMPLE_HOST(3)
#endif
#undef SAMPLE_HOST
    model.io_aclk=1;context.timeInc(1);model.eval();++cycles;
    if(b)b_valid=false;
    if(r) {++reads; if(rd.beat==rd.len)read_busy=false;else Advance(rd);}
    if(ar) {
      if(next_rd.size>4 || next_rd.burst>1)throw std::runtime_error("unsupported AXI read mode");
      rd=next_rd;read_busy=true;
    }
    if(aw) {
      if(next_wr.size>4 || next_wr.burst>1)throw std::runtime_error("unsupported AXI write mode");
      wr=next_wr;write_busy=true;
    }
    if(w) {
      uint32_t line=wr.address&~15u;
      for(unsigned lane=0;lane<16;++lane)if(strobe&(1u<<lane)) {
        CheckRange(line+lane,1);
        ram[line+lane-kBase]=uint8_t(data[lane/4]>>(8*(lane%4)));
      }
      ++writes;
      if(last!=(wr.beat==wr.len))throw std::runtime_error("AXI WLAST/length mismatch");
      if(last){write_busy=false;b_valid=true;b_id=wr.id;b_resp=0;}else Advance(wr);
    }
    if(model.io_fault_0 || model.io_fault_1)throw std::runtime_error("Coral core fault");
#ifdef CLUSTER_QUAD
    if(model.io_fault_2 || model.io_fault_3)throw std::runtime_error("Coral core fault");
#endif
    if(context.gotFinish())throw std::runtime_error("unexpected RTL finish");
  }
  void HostWrite(unsigned core,uint32_t address,uint32_t value) {
#define SET_HOST(N) \
    model.io_hosts_##N##_write_addr_valid=1; \
    model.io_hosts_##N##_write_addr_bits_addr=address; \
    model.io_hosts_##N##_write_addr_bits_id=0; \
    model.io_hosts_##N##_write_addr_bits_len=0; \
    model.io_hosts_##N##_write_addr_bits_size=2; \
    model.io_hosts_##N##_write_addr_bits_burst=1; \
    model.io_hosts_##N##_write_addr_bits_prot=0; \
    model.io_hosts_##N##_write_addr_bits_lock=0; \
    model.io_hosts_##N##_write_addr_bits_cache=0; \
    model.io_hosts_##N##_write_addr_bits_qos=0; \
    model.io_hosts_##N##_write_addr_bits_region=0; \
    model.io_hosts_##N##_write_data_valid=1; \
    for(int i=0;i<4;++i)model.io_hosts_##N##_write_data_bits_data[i]=0; \
    model.io_hosts_##N##_write_data_bits_data[(address%16)/4]=value; \
    model.io_hosts_##N##_write_data_bits_strb=15u<<(address%16); \
    model.io_hosts_##N##_write_data_bits_last=1;
    switch(core) {
      case 0: {SET_HOST(0)} break;
      case 1: {SET_HOST(1)} break;
#ifdef CLUSTER_QUAD
      case 2: {SET_HOST(2)} break;
      case 3: {SET_HOST(3)} break;
#endif
      default: throw std::runtime_error("invalid host core index");
    }
#undef SET_HOST
    bool aw_done=false,w_done=false,b_done=false;
    const auto limit=cycles+2000;
    while(!b_done) {
      if(cycles>=limit)throw std::runtime_error("host AXI transaction timeout");
      Tick();aw_done|=host_aw[core];w_done|=host_w[core];b_done|=host_b[core];
#define UPDATE_HOST(N) \
      model.io_hosts_##N##_write_addr_valid=!aw_done; \
      model.io_hosts_##N##_write_data_valid=!w_done;
      switch(core) {
        case 0: {UPDATE_HOST(0)} break;
        case 1: {UPDATE_HOST(1)} break;
#ifdef CLUSTER_QUAD
        case 2: {UPDATE_HOST(2)} break;
        case 3: {UPDATE_HOST(3)} break;
#endif
      }
#undef UPDATE_HOST
    }
    if(!aw_done || !w_done)throw std::runtime_error("host completed before AW/W");
  }
  void WaitIdle() {
    for(;;) {
      bool idle=model.io_wfi_0 && model.io_wfi_1;
#ifdef CLUSTER_QUAD
      idle=idle && model.io_wfi_2 && model.io_wfi_3;
#endif
      if(idle && !read_busy && !write_busy && !b_valid)return;
      Tick();
    }
  }
};

int main(int argc,char** argv) {
  try {
    unsigned count=37,stalls=3;uint64_t max_cycles=200000;
    std::string elf;
    for(int i=1;i<argc;++i) {
      std::string a=argv[i];
      if(i+1>=argc)throw std::runtime_error("missing option value");
      if(a=="--elf"){elf=argv[++i];continue;}
      const auto n=std::stoull(argv[++i]);
      if(a=="--elements" && n>=1 && n<=128)count=unsigned(n);
      else if(a=="--max-cycles" && n>=40)max_cycles=n;
      else if(a=="--stall-period" && n<=1000)stalls=unsigned(n);
      else throw std::runtime_error("invalid option/range");
    }
    Bench tb(max_cycles,stalls);
    for(unsigned i=0;i<count;++i) {
      tb.WriteWord(Bench::kBase+4*i,3*i+1);
      tb.WriteWord(Bench::kBase+0x400+4*i,7*i+2);
      tb.WriteWord(Bench::kBase+0x800+4*i,0xdeadbeef);
    }
    tb.WriteWord(Bench::kBase+0x7fc,0xa5a5a5a5);
    tb.WriteWord(Bench::kBase+0x800+4*count,0x5a5a5a5a);
#ifdef CLUSTER_RVV
    constexpr bool vector=true;
#else
    constexpr bool vector=false;
#endif
    if(vector && !elf.empty())throw std::runtime_error("ELF learning path is scalar only");
    const auto image=elf.empty()?cluster::Image::Words(AddFirmware(vector)):cluster::Image::Elf(elf);
    cluster::Runtime<Bench> runtime(tb);
    runtime.Load(image);
    const auto start=tb.cycles;
    runtime.Launch(Bench::kBase,Bench::kBase+0x400,Bench::kBase+0x800,count,Bench::kBase+0xc00);
    runtime.Wait();
    for(unsigned i=0;i<count;++i)if(tb.ReadWord(Bench::kBase+0x800+4*i)!=10*i+3)
      throw std::runtime_error("incorrect sharded output at index "+std::to_string(i));
    if(tb.ReadWord(Bench::kBase+0x7fc)!=0xa5a5a5a5 ||
       tb.ReadWord(Bench::kBase+0x800+4*count)!=0x5a5a5a5a)
      throw std::runtime_error("output guard overwritten");
    std::cout<<"{\"result\":\"PASS\",\"cores\":"<<Bench::kCores<<",\"firmware\":\""
             <<(!elf.empty()?"LLVM RV32I ELF":(vector?"RVV integer add":"RV32 scalar integer add"))
             <<"\",\"completion_mask\":"<<((1u<<Bench::kCores)-1)<<",\"firmware_source\":\""
             <<(elf.empty()?"builtin":"elf")
             <<"\",\"elements\":"<<count<<",\"cycles\":"<<tb.cycles
             <<",\"execution_cycles\":"<<tb.cycles-start<<",\"read_beats\":"<<tb.reads
             <<",\"write_beats\":"<<tb.writes<<",\"stall_period\":"<<stalls
             <<",\"rtl\":true,\"shared_ram_model\":\"C++ AXI BFM\"}\n";
    tb.model.final();return 0;
  }catch(const std::exception& e){std::cerr<<"FAIL: "<<e.what()<<'\n';return 1;}
}

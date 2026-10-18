// Read-only inspection of device CSR samples and hardware-timestamped events.
#include <algorithm>
#include <array>
#include <map>
#include "../device/profile.h"

static const char* ProfileOpName(unsigned op) {
  return op==OP_GEMM?"gemm":op==OP_CONV?"conv":"add";
}
static const char* ScenarioName(unsigned scenario) {
  return scenario==1?"serial":scenario==2?"parallel":scenario==3?"gemm":scenario==4?"conv":"add";
}
static void WriteProfile(DieBench& tb,unsigned scenario,unsigned rounds,const std::string& path) {
  struct Record {
    unsigned op,core,invocation,items;
    uint32_t start,end,cycles,instructions;
  };
  std::vector<Record> records;
  std::array<const DieBench::Event*,4> active{};
  for(const auto& event:tb.events) {
    if(event.event==TRACE_START) {
      if(active[event.core])throw std::runtime_error("profiling START before previous END");
      active[event.core]=&event;
    } else if(event.event==TRACE_END) {
      const auto* start=active[event.core];
      if(!start || start->op!=event.op)throw std::runtime_error("profiling END without matching START");
      const unsigned invocation=event.op==OP_ADD?start->detail:0;
      if(invocation>=2)throw std::runtime_error("profiling invocation out of bounds");
      const unsigned slot=(event.op==OP_ADD?invocation*4:0)+event.core;
      const unsigned offset=PROFILE_BASE-0x20000000u+PROFILE_STRIDE*slot;
      std::array<uint32_t,8> words{};
      for(unsigned word=0;word<8;++word)words[word]=tb.InspectWord(offset+4*word);
      const uint32_t cycles=words[5]-words[4],instructions=words[7]-words[6];
      if(words[0]!=PROFILE_MAGIC || words[1]!=event.core || words[2]!=event.op ||
         words[3]!=invocation || !cycles || !instructions ||
         cycles>event.cycle-start->cycle || uint64_t(instructions)>4ull*(cycles+8))
        throw std::runtime_error("invalid device mcycle/minstret profile record");
      const unsigned expected_op=scenario==3?OP_GEMM:scenario==4?OP_CONV:
                                  scenario?event.core<2?OP_GEMM:OP_CONV:OP_ADD;
      if(event.op!=expected_op)throw std::runtime_error("profile operator/core assignment mismatch");
      records.push_back({event.op,event.core,invocation,event.detail,start->cycle,event.cycle,cycles,instructions});
      active[event.core]=nullptr;
    }
  }
  for(auto pending:active)if(pending)throw std::runtime_error("missing profiling END");
  if(records.size()!=(scenario?4:4*rounds))throw std::runtime_error("missing device performance records");
  // Full correctness is checked separately before this report is written.
  if(path.empty())return;
  std::ofstream out(path);
  if(!out)throw std::runtime_error("cannot create profile JSON");
  out<<"{\"schema_version\":1,\"result\":\"PASS\",\"scenario\":\""<<ScenarioName(scenario)
     <<"\",\"counter_source\":\"Coral hardware mcycle/minstret\","
     <<"\"time_source\":\"RTL AXI START/END timestamps\",\"cores\":[";
  for(unsigned i=0;i<records.size();++i) {
    const auto& r=records[i];if(i)out<<',';
    out<<"{\"operator\":\""<<ProfileOpName(r.op)<<"\",\"core\":"<<r.core
       <<",\"invocation\":"<<r.invocation<<",\"work_items\":"<<r.items
       <<",\"start_cycle\":"<<r.start<<",\"end_cycle\":"<<r.end
       <<",\"sample_cycles\":"<<r.cycles<<",\"retired_instructions\":"<<r.instructions<<'}';
  }
  out<<"],\"operators\":[";
  std::map<std::pair<unsigned,unsigned>,std::vector<Record>> groups;
  for(const auto& r:records)groups[{r.op,r.invocation}].push_back(r);
  bool first=true;
  for(const auto& group:groups) {
    uint32_t begin=UINT32_MAX,end=0;uint64_t instructions=0;
    for(const auto& r:group.second) {
      begin=std::min(begin,r.start);end=std::max(end,r.end);instructions+=r.instructions;
    }
    if(!first)out<<',';first=false;
    out<<"{\"operator\":\""<<ProfileOpName(group.first.first)<<"\",\"invocation\":"<<group.first.second
       <<",\"start_cycle\":"<<begin<<",\"end_cycle\":"<<end<<",\"elapsed_cycles\":"<<end-begin
       <<",\"retired_instructions\":"<<instructions<<",\"cores\":"<<group.second.size()<<'}';
  }
  out<<"]}\n";out.flush();if(!out)throw std::runtime_error("cannot write profile JSON");
}

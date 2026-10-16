#ifndef CORAL_CLUSTER_RUNTIME_H_
#define CORAL_CLUSTER_RUNTIME_H_
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>
#include "device/kernel_abi.h"

namespace cluster {
static_assert(sizeof(ClusterArgs) == 20, "device/host argument ABI mismatch");

struct Segment {
  uint32_t address;
  std::vector<uint8_t> bytes; // Includes zero-filled BSS.
};

struct Image {
  uint32_t entry = 0;
  std::vector<Segment> segments;

  static Image Words(const std::vector<uint32_t>& words) {
    if(words.empty() || words.size()>2048)throw std::runtime_error("invalid built-in image");
    Image image;
    Segment segment{0,{}};
    for(auto word:words)for(unsigned i=0;i<4;++i)segment.bytes.push_back(uint8_t(word>>(8*i)));
    image.segments.push_back(std::move(segment));
    return image;
  }

  // Restricted static ELF32 loader: RV32, little endian, ITCM + private DTCM.
  // No dynamic linking, relocations, virtual memory, TLS, or arbitrary SoC writes.
  static Image Elf(const std::string& path) {
    std::ifstream input(path,std::ios::binary|std::ios::ate);
    if(!input || input.tellg()<52 || input.tellg()>1024*1024)
      throw std::runtime_error("ELF missing or invalid file size");
    const size_t length=size_t(input.tellg());
    input.seekg(0);
    std::vector<uint8_t> bytes(length);
    if(!input.read(reinterpret_cast<char*>(bytes.data()),length))
      throw std::runtime_error("cannot read ELF");
    auto u16=[&](size_t off)->uint16_t {
      if(off+2>bytes.size())throw std::runtime_error("truncated ELF");
      return uint16_t(bytes[off])|uint16_t(bytes[off+1])<<8;
    };
    auto u32=[&](size_t off)->uint32_t {
      if(off+4>bytes.size())throw std::runtime_error("truncated ELF");
      uint32_t value=0;for(unsigned i=0;i<4;++i)value|=uint32_t(bytes[off+i])<<(8*i);
      return value;
    };
    if(bytes[0]!=0x7f || bytes[1]!='E' || bytes[2]!='L' || bytes[3]!='F' ||
       bytes[4]!=1 || bytes[5]!=1 || bytes[6]!=1 || u16(16)!=2 || u16(18)!=243 ||
       u32(20)!=1 || u16(40)!=52 || u16(42)!=32 || u32(36)!=0)
      throw std::runtime_error("expected static ELF32 little-endian RV32I soft-float image");
    const uint32_t phoff=u32(28), count=u16(44);
    if(count==0 || count>32 || uint64_t(phoff)+uint64_t(count)*32>bytes.size())
      throw std::runtime_error("invalid ELF program header table");
    Image image;image.entry=u32(24);
    bool executable_entry=false;
    for(unsigned i=0;i<count;++i) {
      const size_t off=size_t(phoff)+32*i;
      const uint32_t type=u32(off);
      if(type==2 || type==3 || type==7)throw std::runtime_error("dynamic/TLS ELF unsupported");
      if(type!=1)continue;
      const uint32_t file=u32(off+4), va=u32(off+8), pa=u32(off+12);
      const uint32_t filesz=u32(off+16), memsz=u32(off+20), flags=u32(off+24);
      if(memsz==0)continue;
      const uint64_t end=uint64_t(va)+memsz;
      const bool code=(flags&1)!=0;
      if(va!=pa || va%4 || memsz%4 || filesz>memsz || uint64_t(file)+filesz>bytes.size() ||
         (code ? (end>0x2000 || (flags&2)) : (va<0x10100 || end>0x17000)))
        throw std::runtime_error("ELF segment outside permitted ITCM/DTCM layout");
      for(const auto& old:image.segments)
        if(va<uint64_t(old.address)+old.bytes.size() && old.address<end)
          throw std::runtime_error("overlapping ELF load segments");
      Segment segment{va,std::vector<uint8_t>(memsz,0)};
      std::copy_n(bytes.begin()+file,filesz,segment.bytes.begin());
      image.segments.push_back(std::move(segment));
      executable_entry|=code && image.entry>=va && image.entry<va+filesz;
    }
    if(!executable_entry || image.entry%4)
      throw std::runtime_error("ELF entry is not aligned executable code");
    return image;
  }
};

struct Shard { unsigned first, count; uint32_t done; };

// Transport provides HostWrite/ReadWord/WriteWord/WaitIdle and kCores.
// Replace Bench with an MMIO/driver transport when moving to FPGA or a host CPU.
template<class Transport> class Runtime {
 public:
  explicit Runtime(Transport& transport):transport_(transport) {}

  void Load(const Image& image) {
    for(unsigned core=0;core<Transport::kCores;++core) {
      for(const auto& segment:image.segments) {
        for(size_t offset=0;offset<segment.bytes.size();offset+=4) {
          uint32_t word=0;
          for(unsigned j=0;j<4 && offset+j<segment.bytes.size();++j)
            word|=uint32_t(segment.bytes[offset+j])<<(8*j);
          transport_.HostWrite(core,segment.address+uint32_t(offset),word);
        }
      }
      transport_.HostWrite(core,CLUSTER_CSR_ADDRESS+4,image.entry);
    }
    loaded_=true;
  }

  void Launch(uint32_t a,uint32_t b,uint32_t c,unsigned count,uint32_t done_base) {
    if(!loaded_ || launched_ || count==0)throw std::runtime_error("invalid runtime launch state");
    const unsigned base=count/Transport::kCores, rest=count%Transport::kCores;
    shards_.clear();
    for(unsigned core=0;core<Transport::kCores;++core) {
      const unsigned first=core*base+std::min(core,rest), size=base+(core<rest);
      const uint32_t done=done_base+4*core;
      transport_.WriteWord(done,0);
      const ClusterArgs args{a+4*first,b+4*first,c+4*first,size,done};
      const uint32_t fields[]={args.a,args.b,args.c,args.count,args.done};
      for(unsigned i=0;i<5;++i)transport_.HostWrite(core,CLUSTER_ARGS_ADDRESS+4*i,fields[i]);
      shards_.push_back({first,size,done});
      std::cout<<"runtime: core="<<core<<" first="<<first<<" count="<<size
               <<" done=0x"<<std::hex<<done<<std::dec<<'\n';
    }
    for(unsigned core=0;core<Transport::kCores;++core) {
      transport_.HostWrite(core,CLUSTER_CSR_ADDRESS,1);
      transport_.HostWrite(core,CLUSTER_CSR_ADDRESS,0);
    }
    launched_=true;
  }

  void Wait() {
    if(!launched_)throw std::runtime_error("runtime wait before launch");
    transport_.WaitIdle(); // Includes WFI and draining outstanding memory responses.
    for(const auto& shard:shards_)
      if(transport_.ReadWord(shard.done)!=1)
        throw std::runtime_error("cores stopped without all completion flags");
  }

 private:
  Transport& transport_;
  bool loaded_=false,launched_=false; // One launch per reset in this minimal version.
  std::vector<Shard> shards_;
};
} // namespace cluster
#endif

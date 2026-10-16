#ifndef CORAL_CLUSTER_FIRMWARE_H_
#define CORAL_CLUSTER_FIRMWARE_H_
#include <cstdint>
#include <vector>

// Tiny position-fixed RV32 program, loaded at ITCM 0. No stack, libc or linker.
// x8 points at a five-word DTCM argument block: A, B, C, count, done_address.
inline std::vector<uint32_t> AddFirmware(bool vector) {
  std::vector<uint32_t> p;
  auto imm = [](int value, int rs, int f, int rd, int op) -> uint32_t {
    return (uint32_t(value)&4095u)<<20 | rs<<15 | f<<12 | rd<<7 | op;
  };
  auto branch = [](int offset, int rs1, int rs2, int funct3) -> uint32_t {
    uint32_t n = uint32_t(offset)&8191u;
    return ((n>>12)&1)<<31 | ((n>>5)&63)<<25 | rs2<<20 | rs1<<15 |
           funct3<<12 | ((n>>1)&15)<<8 | ((n>>11)&1)<<7 | 0x63;
  };
  p.push_back(0x00010437);  // lui s0,0x10; DTCM=0x10000
  const int args[] = {5,6,7,28,29};
  for (int i=0;i<5;++i) p.push_back(imm(i*4,8,2,args[i],0x03));
  if (vector) {
    p.push_back(imm(0x600,0,0,31,0x13)); // li t6,0x600; mstatus.VS=Dirty
    p.push_back(0x300fa073); // csrs mstatus,t6
  }
  const auto empty_branch = p.size();
  p.push_back(0); // beq t3,zero,done; even empty shards signal completion.
  const auto loop = p.size();
  if (vector) {
    // vsetvli t5,t3,e32,m1,ta,ma; vle32 v0/v1; vadd.vv v2; vse32 v2.
    p.push_back(0x0d0e7f57);
    p.push_back(0x0202e007);
    p.push_back(0x02036087);
    p.push_back(0x02008157);
    p.push_back(0x0203e127);
    p.push_back(0x41ee0e33);  // sub t3,t3,t5
    p.push_back(imm(2,30,1,30,0x13)); // slli t5,t5,2
    for (int r : {5,6,7}) p.push_back(30<<20 | r<<15 | r<<7 | 0x33);
  } else {
    p.push_back(imm(0,5,2,10,0x03));
    p.push_back(imm(0,6,2,11,0x03));
    p.push_back(11<<20 | 10<<15 | 10<<7 | 0x33);
    p.push_back(10<<20 | 7<<15 | 2<<12 | 0x23);
    for (int r : {5,6,7}) p.push_back(imm(4,r,0,r,0x13));
    p.push_back(imm(-1,28,0,28,0x13));
  }
  p.push_back(branch((int(loop)-int(p.size()))*4,28,0,1));
  p[empty_branch] = branch((int(p.size())-int(empty_branch))*4,28,0,0);
  p.push_back(0x0330000f); // fence rw,rw
  p.push_back(imm(1,0,0,10,0x13));
  p.push_back(10<<20 | 29<<15 | 2<<12 | 0x23);
  p.push_back(0x0330000f);
  p.push_back(0x10500073); // wfi
  p.push_back(0x0000006f); // j . if WFI resumes
  // Keep speculative/prefetched words in the final fetch lines deterministic.
  while (p.size()<64) p.push_back(0x00000013); // nop
  return p;
}
#endif

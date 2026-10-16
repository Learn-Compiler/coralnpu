#include "platform.h"

/* These functions execute as RV32 instructions on the actual control CPU. */
void coral_load(const uint32_t *image,unsigned words,uint32_t entry) {
  for(unsigned core=0;core<CORAL_COUNT;++core) {
    write32(coral_address(core,CLUSTER_CSR_ADDRESS),3); // Reset + clock gate.
    for(unsigned i=0;i<words;++i)
      write32(coral_address(core,4*i),image[i]);
    write32(coral_address(core,CLUSTER_CSR_ADDRESS+4),entry);
  }
  memory_fence();
}

void coral_launch_add(uint32_t a,uint32_t b,uint32_t c,unsigned count) {
  const unsigned base=count/CORAL_COUNT,rest=count%CORAL_COUNT;
  for(unsigned core=0;core<CORAL_COUNT;++core) {
    // A completed WFI kernel is restarted through its control CSR for each op.
    write32(coral_address(core,CLUSTER_CSR_ADDRESS),3);
    const unsigned first=core*base+(core<rest?core:rest);
    const uint32_t done=SHARED_BASE+0xc00+4*core;
    write32(done,0);
    const ClusterArgs args={a+4*first,b+4*first,c+4*first,
                           base+(core<rest),done};
    const uint32_t fields[]={args.a,args.b,args.c,args.count,args.done};
    for(unsigned field=0;field<5;++field)
      write32(coral_address(core,CLUSTER_ARGS_ADDRESS+4*field),fields[field]);
  }
  memory_fence();
  for(unsigned core=0;core<CORAL_COUNT;++core) {
    write32(coral_address(core,CLUSTER_CSR_ADDRESS),1);
    write32(coral_address(core,CLUSTER_CSR_ADDRESS),0);
  }
}

void coral_start_mask(unsigned mask) {
  memory_fence();
  for(unsigned core=0;core<CORAL_COUNT;++core)if(mask&(1u<<core)) {
    write32(coral_address(core,CLUSTER_CSR_ADDRESS),1);
    write32(coral_address(core,CLUSTER_CSR_ADDRESS),0);
  }
}

void coral_prepare_complex(unsigned rows) {
  for(unsigned core=0;core<CORAL_COUNT;++core) {
    const unsigned lane=core&1,conv=core>=2;
    const unsigned first=lane*(rows/2)+(lane<(rows%2)?lane:rows%2);
    const uint32_t done=SHARED_BASE+COMPLEX_DONE+4*core;
    const ComplexArgs args={conv?OP_CONV:OP_GEMM,core,first,rows/2+(lane<rows%2),
      SHARED_BASE+(conv?CONV_X:GEMM_A),SHARED_BASE+(conv?CONV_W:GEMM_B),
      SHARED_BASE+(conv?CONV_BIAS:GEMM_BIAS),SHARED_BASE+(conv?CONV_Y:GEMM_Y),done};
    const uint32_t *fields=(const uint32_t *)&args;
    write32(done,0);
    for(unsigned field=0;field<sizeof(args)/4;++field)
      write32(coral_address(core,CLUSTER_ARGS_ADDRESS+4*field),fields[field]);
  }
  memory_fence();
}

uint32_t coral_wait_mask(unsigned mask,uint32_t timeout_cycles) {
  const uint32_t start=read32(SYSTEM_BASE+SYS_CYCLES);
  for(;;) {
    if(read32(SYSTEM_BASE+SYS_FAULT))return DIE_CORAL_FAULT;
    if(read32(SYSTEM_BASE+SYS_BUS_ERROR))return DIE_BUS_ERROR;
    // Architectural traps can redirect to mtvec without asserting the fault pin.
    // The bootstrap kernel is non-interrupting; any nonzero mcause is an error.
    unsigned trap_mask=0;
    for(unsigned core=0;core<CORAL_COUNT;++core)
      if(read32(coral_address(core,CLUSTER_CSR_ADDRESS+0x10c)))trap_mask|=1u<<core;
    if(trap_mask) {
      write32(SYSTEM_BASE+SYS_TRAP_MASK,trap_mask);
      return DIE_CORAL_FAULT;
    }
    unsigned completed=0;
    for(unsigned core=0;core<CORAL_COUNT;++core)
      if(read32(SHARED_BASE+0xc00+4*core)==1)completed|=1u<<core;
    if((completed&mask)==mask && (read32(SYSTEM_BASE+SYS_WFI)&mask)==mask) {
      memory_fence();
      return 0;
    }
    if(read32(SYSTEM_BASE+SYS_CYCLES)-start>=timeout_cycles)return DIE_WAIT_TIMEOUT;
  }
}

uint32_t coral_wait(uint32_t timeout_cycles) {
  return coral_wait_mask(15,timeout_cycles);
}

void die_finish(uint32_t code) {
  memory_fence();
  write32(SYSTEM_BASE+SYS_FINISH,code);
  for(;;)__asm__ volatile("j ." ::: "memory");
}

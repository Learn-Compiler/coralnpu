#include "kernel_abi.h"
#include "profile.h"
#ifdef __riscv_vector
#include <riscv_vector.h>
#endif

/* Each core runs this same function on the shard chosen by the host runtime. */
void kernel_entry(const volatile ClusterArgs *args) {
  const uint32_t *a = (const uint32_t *)(uintptr_t)args->a;
  const uint32_t *b = (const uint32_t *)(uintptr_t)args->b;
  uint32_t *c = (uint32_t *)(uintptr_t)args->c;
  const uint32_t count = args->count;
#ifdef NCORAL_PROFILE
  __asm__ volatile("fence rw,rw" ::: "memory");
  *(volatile uint32_t *)(uintptr_t)0x20003ff0u=
    0xc0000000u | (OP_ADD<<24) | (args->core<<20) | (1u<<16) | args->invocation;
  __asm__ volatile("fence rw,rw" ::: "memory");
  const CoralCounterSample begin=coral_counter_sample();
#endif
#ifdef __riscv_vector
  for (uint32_t i=0;i<count;) {
    const unsigned vl=__riscv_vsetvl_e32m1(count-i);
    const vuint32m1_t va=__riscv_vle32_v_u32m1(a+i,vl);
    const vuint32m1_t vb=__riscv_vle32_v_u32m1(b+i,vl);
    const vuint32m1_t vc=__riscv_vadd_vv_u32m1(va,vb,vl);
    __riscv_vse32_v_u32m1(c+i,vc,vl);
    i+=vl;
  }
#else
  // Compatibility with the old standalone scalar-cluster build.
  for (uint32_t i=0;i<count;++i)c[i]=a[i]+b[i];
#endif

#ifdef NCORAL_PROFILE
  coral_profile_end(args->profile,args->core,OP_ADD,args->invocation,begin);
  *(volatile uint32_t *)(uintptr_t)0x20003ff0u=
    0xc0000000u | (OP_ADD<<24) | (args->core<<20) | (3u<<16) | count;
#endif
  /* Publish output before completion. Volatile alone is not a hardware fence. */
  __asm__ volatile("fence rw,rw" ::: "memory");
  *(volatile uint32_t *)(uintptr_t)args->done = 1;
  __asm__ volatile("fence rw,rw" ::: "memory");
}

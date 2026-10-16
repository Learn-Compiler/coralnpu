#include "complex_abi.h"
#include <riscv_vector.h>

static inline void fence(void) { __asm__ volatile("fence rw,rw" ::: "memory"); }
/* Device-side logging: a real AXI store, decoded by the RTL trace endpoint.
 * No printf/libc or string/data segment is needed. Labels carry software IDs. */
static void coral_log(const ComplexArgs *args,unsigned event,unsigned detail) {
  fence();
  *(volatile uint32_t *)(uintptr_t)CORAL_TRACE =
    0xc0000000u | (args->op<<24) | (args->core<<20) | (event<<16) | detail;
  fence();
}

void kernel_entry(const ComplexArgs *args) {
  const int32_t *a=(const int32_t *)(uintptr_t)args->a;
  const int32_t *b=(const int32_t *)(uintptr_t)args->b;
  const int32_t *bias=(const int32_t *)(uintptr_t)args->bias;
  int32_t *y=(int32_t *)(uintptr_t)args->y;
  coral_log(args,TRACE_START,args->first_row);
  uint32_t vlenb;
  __asm__ volatile("csrr %0, vlenb" : "=r"(vlenb));
  const unsigned lanes=__riscv_vsetvl_e32m1(4);
  coral_log(args,TRACE_RVV_CONFIG,(vlenb<<8)|lanes);
  /* Original shared weights remain [Co][Ky][Kx][Ci]. Pack them once into
   * private DTCM as [tap][Co], then each inner MAC uses a contiguous vector.
   * No cross-core writes, heap, global data or shared scratch are needed. */
  int32_t packed[18*CONV_CO] __attribute__((aligned(16)));
  if(args->op==OP_CONV && args->rows) {
    for(unsigned co=0;co<CONV_CO;) {
      const unsigned vl=__riscv_vsetvl_e32m1(CONV_CO-co);
      for(unsigned tap=0;tap<18;++tap) {
        const vint32m1_t w=__riscv_vlse32_v_i32m1(b+co*18+tap,18*sizeof(int32_t),vl);
        __riscv_vse32_v_i32m1(packed+tap*CONV_CO+co,w,vl);
      }
      co+=vl;
    }
  }
  for(unsigned row=args->first_row;row<args->first_row+args->rows;++row) {
    if(args->op==OP_GEMM) {
      for(unsigned n=0;n<GEMM_N;) {
        const unsigned vl=__riscv_vsetvl_e32m1(GEMM_N-n);
        vint32m1_t acc=__riscv_vle32_v_i32m1(bias+n,vl);
        for(unsigned k=0;k<GEMM_K;++k) {
          const vint32m1_t w=__riscv_vle32_v_i32m1(b+k*GEMM_N+n,vl);
          acc=__riscv_vmacc_vx_i32m1(acc,a[row*GEMM_K+k],w,vl);
        }
        acc=__riscv_vmax_vx_i32m1(acc,0,vl);
        __riscv_vse32_v_i32m1(y+row*GEMM_N+n,acc,vl);
        n+=vl;
      }
    } else {
      /* NHWC input/output; weights [output channel][ky][kx][input channel]. */
      for(unsigned x=0;x<CONV_OW;++x)for(unsigned co=0;co<CONV_CO;) {
        const unsigned vl=__riscv_vsetvl_e32m1(CONV_CO-co);
        vint32m1_t acc=__riscv_vle32_v_i32m1(bias+co,vl);
        for(unsigned ky=0;ky<3;++ky)for(unsigned kx=0;kx<3;++kx)
          for(unsigned ci=0;ci<CONV_CI;++ci) {
            const unsigned tap=(ky*3+kx)*CONV_CI+ci;
            const vint32m1_t w=__riscv_vle32_v_i32m1(packed+tap*CONV_CO+co,vl);
            acc=__riscv_vmacc_vx_i32m1(acc,a[((row+ky)*CONV_IW+x+kx)*CONV_CI+ci],w,vl);
          }
        acc=__riscv_vmax_vx_i32m1(acc,0,vl);
        __riscv_vse32_v_i32m1(y+(row*CONV_OW+x)*CONV_CO+co,acc,vl);
        co+=vl;
      }
    }
    coral_log(args,TRACE_ROW,row);
  }
  coral_log(args,TRACE_END,args->rows);
  fence();
  *(volatile uint32_t *)(uintptr_t)args->done=1;
  fence();
}

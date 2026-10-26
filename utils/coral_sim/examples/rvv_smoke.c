#include <stdint.h>
#include <riscv_vector.h>
static uint32_t a[19], b[19], c[19];
int main(void) {
  for (unsigned i=0;i<19;++i) { a[i]=i*7+3; b[i]=i*5+1; c[i]=0xdeadbeef; }
  for (unsigned i=0;i<19;) {
    size_t vl=__riscv_vsetvl_e32m1(19-i);
    vuint32m1_t x=__riscv_vle32_v_u32m1(a+i,vl);
    vuint32m1_t y=__riscv_vle32_v_u32m1(b+i,vl);
    __riscv_vse32_v_u32m1(c+i,__riscv_vadd_vv_u32m1(x,y,vl),vl);
    i+=vl;
  }
  for (unsigned i=0;i<19;++i) if(c[i]!=12*i+4)return 1;
  return 0;
}

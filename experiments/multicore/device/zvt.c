#include "task_abi.h"

/* Signed INT8 16x16x4 matrix microkernel. Matrix state and operand loads stay
 * inside one assembly block: an intervening vsetvli would reset mtype/altfmt.
 * A and B each hold four 16-byte K-major rows; C receives a 16x16 INT32 tile. */
void kernel_entry(volatile NcKernelArgs *args) {
  if(args->abi!=NC_KERNEL_ABI || args->size!=sizeof(NcKernelArgs))return;
  const int8_t *a=(const int8_t *)(uintptr_t)args->bindings[0];
  const int8_t *b=(const int8_t *)(uintptr_t)args->bindings[1];
  int32_t *c=(int32_t *)(uintptr_t)args->bindings[2];
  __asm__ volatile(
    "li t0, 0x60000000\n csrs mstatus, t0\n"
    "li t0, 16417\n li t1, 18\n msetmtype t0, t1\n"
    "li t1, 16\n msettn zero, t1\n vtzero mt0\n"
    "li t0, 16419\n li t1, 256\n msetmtype t0, t1\n"
    "li t1, 16\n msettn zero, t1\n"
    "mv t3, %0\n mv t4, %1\n li t5, 4\n"
    "1: vle8.v v8, (t3)\n vle8.v v16, (t4)\n"
    "vtmms.tvv mt0, v8, v16\n"
    "addi t3, t3, 16\n addi t4, t4, 16\n addi t5, t5, -1\n bnez t5, 1b\n"
    "li t0, 16417\n li t1, 18\n msetmtype t0, t1\n"
    "li t1, 16\n msettn zero, t1\n"
    "li t2, 0\n mv t3, %2\n"
    "2: vtmv.v.t v4, t2\n vse32.v v4, (t3)\n"
    "addi t3, t3, 64\n addi t2, t2, 1\n li t1, 16\n bltu t2, t1, 2b\n"
    "vtdiscard\n"
    : : "r"(a),"r"(b),"r"(c)
    : "t0","t1","t2","t3","t4","t5","v4","v5","v6","v7","v8","v16","memory");
  __asm__ volatile("fence rw,rw" ::: "memory");
  *(volatile uint32_t *)(uintptr_t)args->done=1;
}

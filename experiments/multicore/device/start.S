.section .text.start,"ax",@progbits
.globl _start
.type _start,@function
_start:
  /* Each core has its own DTCM at the same local address. Stack is 16B aligned. */
  lui sp, 0x18
  lui a0, 0x10
#ifdef __riscv_vector
  /* Enable/dirty vector state before any RVV instruction. Controller stays RV32IM. */
  li t0, 0x600
  csrs mstatus, t0
#endif
  call kernel_entry
1:
  wfi
  j 1b
.size _start, .-_start

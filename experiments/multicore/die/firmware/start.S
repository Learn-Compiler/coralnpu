.section .text.start,"ax",@progbits
.globl _start
.type _start,@function
_start:
  li sp, 0xff00
  la t0, __bss_start
  la t1, __bss_end
1:
  bgeu t0, t1, 2f
  sw zero, 0(t0)
  addi t0, t0, 4
  j 1b
2:
  call control_main
3:
  j 3b
.size _start, .-_start

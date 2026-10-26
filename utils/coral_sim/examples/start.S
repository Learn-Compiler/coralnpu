.section .text.init,"ax",@progbits
.globl _start
.type _start,@function
_start:
  la sp, __stack_top
  la t0, trap_handler
  csrw mtvec, t0
  # Enable floating, vector and matrix architectural state.
  li t0, 0x60006600
  csrs mstatus, t0
  la s0, __coral_profile
  li t0, 0x43505231
  sw t0, 0(s0)
  csrr t0, mcycle
  sw t0, 4(s0)
  csrr t0, minstret
  sw t0, 8(s0)
  call main
  sw a0, 20(s0)
  csrr t0, minstret
  sw t0, 16(s0)
  csrr t0, mcycle
  sw t0, 12(s0)
  li t0, 1
  sw t0, 36(s0)
  bnez a0, trap_handler
  fence rw,rw
  .word 0x08000073 # Coral mpause: successful halt, as in upstream CRT.
1: j 1b
.balign 4
trap_handler:
  la t6, __coral_profile
  csrr t0, mcause
  sw t0, 24(t6)
  csrr t0, mepc
  sw t0, 28(t6)
  csrr t0, mtval
  sw t0, 32(t6)
  ebreak # Failed assertion/exception; upstream TB rejects fault status.
2: j 2b

.section .bss.coral_profile,"aw",@nobits
.balign 4
.globl __coral_profile
.type __coral_profile,@object
__coral_profile:
  .zero 40
.size __coral_profile, .-__coral_profile

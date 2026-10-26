.section .text
.globl _start
_start:
  li a0, 4
  la a1, message
  slli zero, zero, 31
  ebreak
  srai zero, zero, 7
  li a0, 0x20
  la a1, status
  slli zero, zero, 31
  ebreak
  srai zero, zero, 7
.section .rodata
message: .asciz "Hello via RISC-V semihosting!\n"
.balign 4
status: .word 0x20026, 0

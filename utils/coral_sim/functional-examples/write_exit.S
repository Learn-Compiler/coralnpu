.section .text
.globl _start
_start:
  li a0, 1
  la a1, message
  la a2, message_end
  sub a2, a2, a1
  li a7, 64
  ecall
  li a0, 7
  li a7, 93
  ecall
.section .rodata
message: .ascii "Hello from an independent RISC-V ELF!\n"
message_end:

.section .text
.globl _start
_start:
  li a0, 0
  la a1, buffer
  li a2, 64
  li a7, 63
  ecall
  bltz a0, failed
  mv a2, a0
  li a0, 1
  li a7, 64
  ecall
  li a0, 0
  li a7, 93
  ecall
failed:
  li a0, 1
  li a7, 93
  ecall
.section .bss
.balign 16
buffer: .space 64

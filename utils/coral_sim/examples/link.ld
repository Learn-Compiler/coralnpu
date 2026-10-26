OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
  ITCM (rx) : ORIGIN = 0, LENGTH = 8K
  DTCM (rw) : ORIGIN = 0x10000, LENGTH = 32K
}
PHDRS { text PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
  .text : ALIGN(4) { KEEP(*(.text.init)) *(.text .text.*) } > ITCM :text
  .data : ALIGN(16) { *(.rodata .rodata.*) *(.srodata*) *(.data .data.*) *(.sdata*) } > DTCM :data
  .bss (NOLOAD) : ALIGN(16) { *(.bss .bss.*) *(.sbss*) *(COMMON) . = ALIGN(16); } > DTCM :data
  __stack_top = ORIGIN(DTCM) + LENGTH(DTCM);
  ASSERT(. <= __stack_top - 4096, "data overlaps reserved 4 KiB stack")
  /DISCARD/ : { *(.comment) *(.eh_frame*) }
}

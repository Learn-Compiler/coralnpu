OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
  ITCM (rx) : ORIGIN = 0, LENGTH = 8K
  DTCM (rw) : ORIGIN = 0x10000, LENGTH = 32K
  EXTERNAL (rx) : ORIGIN = 0x20000000, LENGTH = 4M
}
PHDRS { init PT_LOAD FLAGS(5); text PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
  .init : ALIGN(4) {
    KEEP(*(.text.init))
    /* Keep frequently called compiler-rt arithmetic helpers in fast ITCM. */
    *(.text.__* .text._putchar .text.putchar .text.OUTLINED_FUNCTION_*)
  } > ITCM :init
  .text : ALIGN(32) { *(.text .text.*) } > EXTERNAL :text
  .data : ALIGN(16) { *(.rodata .rodata.*) *(.srodata*) *(.data .data.*) *(.sdata*) } > DTCM :data
  .bss (NOLOAD) : ALIGN(16) { *(.bss .bss.*) *(.sbss*) *(COMMON) . = ALIGN(16); } > DTCM :data
  __stack_top = ORIGIN(DTCM) + LENGTH(DTCM);
  ASSERT(. <= __stack_top - 4096, "data overlaps reserved 4 KiB stack")
  /DISCARD/ : { *(.comment) *(.eh_frame*) }
}

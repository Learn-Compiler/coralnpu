OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { BOOT (rwx) : ORIGIN = 0, LENGTH = 48K }
SECTIONS {
  .text : { KEEP(*(.text.start)) *(.text .text.*) } > BOOT
  .rodata : { *(.rodata .rodata.* .srodata .srodata.*) } > BOOT
  .data : { *(.data .data.* .sdata .sdata.*) . = ALIGN(4); } > BOOT
  .bss (NOLOAD) : {
    __bss_start = .; *(.bss .bss.* .sbss .sbss.*) *(COMMON)
    . = ALIGN(4); __bss_end = .;
  } > BOOT
  /DISCARD/ : { *(.comment .note.* .eh_frame .riscv.attributes) }
}

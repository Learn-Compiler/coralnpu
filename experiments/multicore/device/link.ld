OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
  ITCM (rx) : ORIGIN = 0x00000000, LENGTH = 8K
  /* First 256B reserved for arguments; upper 4K reserved for the stack. */
  DATA (rw) : ORIGIN = 0x00010100, LENGTH = 0x6f00
}
PHDRS {
  text PT_LOAD FLAGS(5);
  data PT_LOAD FLAGS(6);
}
SECTIONS {
  .text : {
    KEEP(*(.text.start))
    *(.text .text.*)
    /* Deterministic instruction prefetch padding: RISC-V NOP in little endian. */
    FILL(0x13000000)
    . = ALIGN(256);
  } > ITCM :text
  .data : {
    *(.rodata .rodata.* .srodata .srodata.*)
    *(.data .data.* .sdata .sdata.*)
    . = ALIGN(4);
  } > DATA :data
  .bss (NOLOAD) : {
    *(.bss .bss.* .sbss .sbss.*) *(COMMON)
    . = ALIGN(4);
  } > DATA :data
  /DISCARD/ : { *(.comment .note.* .eh_frame .riscv.attributes) }
}

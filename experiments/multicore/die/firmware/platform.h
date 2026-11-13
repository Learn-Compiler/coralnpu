#ifndef CORAL_DIE_PLATFORM_H_
#define CORAL_DIE_PLATFORM_H_
#include <stdint.h>
#include <stddef.h>
#include "../../device/kernel_abi.h"
#include "../../device/complex_abi.h"
#include "../../device/profile.h"

#define CORAL_COUNT 4u
#define SHARED_BASE 0x20000000u
#define CORAL_BASE 0x40000000u
#define CORAL_STRIDE 0x00100000u
#define SYSTEM_BASE 0x50000000u
#define BOOT_CONFIG 0x0000ff00u
#define DIE_PASS 0x600d0001u
#define DIE_BAD_CONFIG 0xbad00001u
#define DIE_CORAL_FAULT 0xbad00002u
#define DIE_BUS_ERROR 0xbad00003u
#define DIE_WAIT_TIMEOUT 0xbad00004u
#define DIE_BAD_RESULT 0xbad00005u

enum { SYS_ID=0, SYS_WFI=4, SYS_FAULT=8, SYS_FINISH=12, SYS_CYCLES=16,
       SYS_STARTED=20, SYS_BUS_ERROR=24, SYS_PHASE=28, SYS_CHECKSUM=32, SYS_DECODE_ERRORS=36,
       SYS_LAUNCHES=40, SYS_TRAP_MASK=44 };

typedef struct { uint32_t elements, rounds, mode, wait_cycles, scenario, rows; } BootConfig;

static inline uint32_t read32(uint32_t address) {
  return *(volatile uint32_t *)(uintptr_t)address;
}
static inline void write32(uint32_t address, uint32_t value) {
  *(volatile uint32_t *)(uintptr_t)address=value;
}
static inline void memory_fence(void) {
  __asm__ volatile("fence rw,rw" ::: "memory");
}
static inline uint32_t coral_address(unsigned core,uint32_t offset) {
  return CORAL_BASE+CORAL_STRIDE*core+offset;
}

void coral_load(const uint32_t *image,unsigned words,uint32_t entry);
void coral_launch_add(uint32_t a,uint32_t b,uint32_t c,unsigned count,unsigned invocation);
uint32_t coral_wait(uint32_t timeout_cycles);
void coral_prepare_complex(unsigned rows,unsigned only_op);
void coral_start_mask(unsigned mask);
uint32_t coral_wait_mask(unsigned mask,uint32_t timeout_cycles);
void queued_main(unsigned scenario,unsigned n,unsigned mode,uint32_t timeout);
void complex_main(unsigned scenario,unsigned rows,uint32_t timeout_cycles);
void die_finish(uint32_t code) __attribute__((noreturn));
#endif

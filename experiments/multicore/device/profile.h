#ifndef NCORAL_PROFILE_H_
#define NCORAL_PROFILE_H_
#include <stdint.h>

#define PROFILE_BASE 0x20003000u
#define PROFILE_MAGIC 0x50524631u
#define PROFILE_STRIDE 32u
#define OP_ADD 3u
/* Eight records: four cores, up to two dependent ADD invocations. */
typedef struct {
  uint32_t magic, core, op, invocation;
  uint32_t cycle_begin, cycle_end, instret_begin, instret_end;
} CoralProfile;
typedef struct { uint32_t cycle, instret; } CoralCounterSample;

#ifdef NCORAL_PROFILE
static inline CoralCounterSample coral_counter_sample(void) {
  CoralCounterSample sample;
  __asm__ volatile("csrr %0, mcycle\n\tcsrr %1, minstret"
                   : "=r"(sample.cycle), "=r"(sample.instret) :: "memory");
  return sample;
}
static inline void coral_profile_end(uint32_t address, unsigned core, unsigned op,
                                     unsigned invocation, CoralCounterSample begin) {
  __asm__ volatile("fence rw,rw" ::: "memory");
  const CoralCounterSample end=coral_counter_sample();
  volatile CoralProfile *out=(volatile CoralProfile *)(uintptr_t)address;
  out->core=core; out->op=op; out->invocation=invocation;
  out->cycle_begin=begin.cycle; out->cycle_end=end.cycle;
  out->instret_begin=begin.instret; out->instret_end=end.instret;
  __asm__ volatile("fence rw,rw" ::: "memory");
  out->magic=PROFILE_MAGIC;
  __asm__ volatile("fence rw,rw" ::: "memory");
}
#endif
#endif

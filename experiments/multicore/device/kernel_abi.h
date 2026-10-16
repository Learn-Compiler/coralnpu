#ifndef CORAL_CLUSTER_KERNEL_ABI_H_
#define CORAL_CLUSTER_KERNEL_ABI_H_
#include <stdint.h>

/* Version 1: device addresses are RV32 addresses, never host pointers. */
#define CLUSTER_ARGS_ADDRESS 0x00010000u
#define CLUSTER_CSR_ADDRESS  0x00030000u
typedef struct {
  uint32_t a;
  uint32_t b;
  uint32_t c;
  uint32_t count;
  uint32_t done;
} ClusterArgs;
#endif

#ifndef NCORAL_TASK_ABI_H_
#define NCORAL_TASK_ABI_H_
#include <stdint.h>
#define NC_KERNEL_ABI 2u
#define NC_KERNEL_ZVT 2u
#define NC_ARGS_ADDRESS 0x00010000u
/* Same kernel ABI for one or several cores. All pointers are device addresses.
 * A kernel may be invoked repeatedly for different logical programs on a core. */
typedef struct {
  uint32_t abi, size, program_id[3], grid[3], core_id;
  uint32_t bindings[3], scalars[4], done;
} NcKernelArgs;
#endif

#ifndef CORAL_COMPLEX_ABI_H_
#define CORAL_COMPLEX_ABI_H_
#include <stdint.h>
#define COMPLEX_BASE 0x20000000u
#define GEMM_A 0x000u
#define GEMM_B 0x400u
#define GEMM_BIAS 0x900u
#define GEMM_Y 0xa00u
#define COMPLEX_DONE 0xc00u
#define CONV_X 0x1000u
#define CONV_W 0x1400u
#define CONV_BIAS 0x1600u
#define CONV_Y 0x1800u
#define CORAL_TRACE 0x20003ff0u
#define GEMM_N 12u
#define GEMM_K 24u
#define CONV_IH 10u
#define CONV_IW 10u
#define CONV_CI 2u
#define CONV_OW 8u
#define CONV_CO 4u
enum { OP_GEMM=1, OP_CONV=2 };
enum { TRACE_START=1, TRACE_ROW=2, TRACE_END=3, TRACE_RVV_CONFIG=4 };
/* All pointers are 32-bit physical addresses; arguments live in private DTCM. */
typedef struct {
  uint32_t op,core,first_row,rows,a,b,bias,y,done,profile;
} ComplexArgs;
#endif

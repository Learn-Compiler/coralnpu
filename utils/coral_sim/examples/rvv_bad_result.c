/* The firmware check passes, then one actual SRAM result is corrupted.
 * The host readback validator must catch this even though main returns zero. */
#define main rvv_original_main
#include "rvv_smoke.c"
#undef main
int main(void) {
  int status = rvv_original_main();
  if (status) return status;
  ((volatile uint32_t *)c)[7] ^= 1;
  return 0;
}

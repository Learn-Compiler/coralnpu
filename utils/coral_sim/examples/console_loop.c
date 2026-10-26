#include <stdio.h>
int main(void) {
  printf("running before timeout");
  for (;;) __asm__ volatile("nop");
}

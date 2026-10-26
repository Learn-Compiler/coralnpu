#include <stdio.h>

volatile int input_a = 7;
volatile int input_b = 5;

int main(void) {
  puts("Hello, world!");
  int sum = input_a + input_b;
  printf("%d + %d = %d\n", input_a, input_b, sum);
  printf("hex = 0x%08x\n", (unsigned)sum);
  return sum == 12 ? 0 : 1;
}

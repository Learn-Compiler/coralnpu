#include <stdio.h>

volatile float input_a = 1.25f;
volatile float input_b = 2.5f;

static int same(const char *a, const char *b) {
  while (*a && *a == *b) { ++a; ++b; }
  return *a == *b;
}

static int format(char *out, size_t size, const char *fmt, ...) {
  va_list args;
  va_start(args, fmt);
  int result = vsnprintf(out, size, fmt, args);
  va_end(args);
  return result;
}

int main(void) {
  float sum = input_a + input_b;
  printf("sum = %.3f\n", sum);
  printf("negative = %.2f\n", -(double)sum);
  printf("scientific = %.2e\n", (double)sum);
  printf("general = %.3g\n", (double)sum);
  char out[64];
  int length = snprintf(out, sizeof(out), "snprintf: %.3f / %d", sum, 42);
  puts(out);
  if (length != 20 || !same(out, "snprintf: 3.750 / 42")) return 1;
  struct { char before; char text[5]; char after; } guard = {'L', {0}, 'R'};
  length = format(guard.text, sizeof(guard.text), "%.3f", sum);
  printf("truncated = '%s', required = %d\n", guard.text, length);
  if (length != 5 || !same(guard.text, "3.75") || guard.before != 'L' || guard.after != 'R') return 2;
  char single = 'X';
  if (snprintf(&single, 1, "%.3f", sum) != 5 || single != 0) return 3;
  if (snprintf((void *)0, 0, "%.3f", sum) != 5) return 4;
  if (snprintf(out, sizeof(out), "%lld / %llu", -1234567890123LL, 18446744073709551615ULL) != 37
      || !same(out, "-1234567890123 / 18446744073709551615")) return 5;
  if (snprintf(out, sizeof(out), "%+.2f / %.2f", 0.0, 1.125) != 12
      || !same(out, "+0.00 / 1.12")) return 6;
  if (snprintf(out, sizeof(out), "%f / %f", __builtin_inf(), __builtin_nan("")) != 9
      || !same(out, "inf / nan")) return 7;
  puts("format checks complete");
  return 0;
}

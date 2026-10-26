#include <stdio.h>
#include <stdint.h>

/* Keep upstream's underscore APIs internal; export normal stdio names below. */
#include "../third_party/mpaland-printf/printf.h"
#undef printf
#undef vprintf
#undef snprintf
#undef vsnprintf
#undef sprintf

/* SystemC UART: these byte stores execute through the Coral RTL AXI port. */
static volatile unsigned char *const uart = (void *)0x54000000u;

int putchar(int c) {
  *uart = (unsigned char)c;
  return (unsigned char)c;
}
void _putchar(char c) { (void)putchar((unsigned char)c); }

int coral_console_write(const void *bytes, size_t count) {
  const unsigned char *p = bytes;
  for (size_t i = 0; i < count; ++i) putchar(p[i]);
  return (int)count;
}
int puts(const char *s) {
  while (*s) putchar((unsigned char)*s++);
  putchar('\n');
  return 0;
}
int vprintf(const char *format, va_list args) { return vprintf_(format, args); }
int printf(const char *format, ...) {
  va_list args;
  va_start(args, format);
  int result = vprintf_(format, args);
  va_end(args);
  return result;
}
int vsnprintf(char *buffer, size_t count, const char *format, va_list args) {
  return vsnprintf_(buffer, count, format, args);
}
int snprintf(char *buffer, size_t count, const char *format, ...) {
  va_list args;
  va_start(args, format);
  int result = vsnprintf_(buffer, count, format, args);
  va_end(args);
  return result;
}
int vsprintf(char *buffer, const char *format, va_list args) {
  return vsnprintf_(buffer, (size_t)-1, format, args);
}
int sprintf(char *buffer, const char *format, ...) {
  va_list args;
  va_start(args, format);
  int result = vsprintf(buffer, format, args);
  va_end(args);
  return result;
}

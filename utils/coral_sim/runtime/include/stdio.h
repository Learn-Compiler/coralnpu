#ifndef CORAL_SIM_STDIO_H
#define CORAL_SIM_STDIO_H
#include <stddef.h>
#include <stdarg.h>
#define EOF (-1)
#ifdef __cplusplus
extern "C" {
#endif
/* Bare-metal stdio output API backed by pinned mpaland/printf. This is not
 * a complete hosted C library: FILE, file I/O and scanf are not provided. */
int putchar(int c);
int puts(const char *s);
int vprintf(const char *format, va_list args) __attribute__((format(printf, 1, 0)));
int printf(const char *format, ...) __attribute__((format(printf, 1, 2)));
int vsnprintf(char *buffer, size_t count, const char *format, va_list args)
    __attribute__((format(printf, 3, 0)));
int snprintf(char *buffer, size_t count, const char *format, ...)
    __attribute__((format(printf, 3, 4)));
int vsprintf(char *buffer, const char *format, va_list args)
    __attribute__((format(printf, 2, 0)));
int sprintf(char *buffer, const char *format, ...)
    __attribute__((format(printf, 2, 3)));
int coral_console_write(const void *bytes, size_t count);
#ifdef __cplusplus
}
#endif
#endif
